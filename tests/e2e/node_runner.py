#!/usr/bin/env python3
"""Private two-VM acceptance runner for the real Xboard/NodeWorker.

Run lifecycle actions as Root on an explicitly authorized Linux Node lab VM.
The manifest, credentials, callback output, state and raw journals stay private.
Public stdout is a whitelist of check names, byte counts and pinned hashes.
Nothing is installed until start-rest/start-new/install-new is explicitly invoked.

Manifest schema 1: run_id, base, binary, credentials_file, panel (old_url,
new_url, ca_file, ca_fingerprint, optional dual_ca_file/dual_ca_fingerprint),
panel_ip, node_ip, callbacks (inspect/rotate/credentials-refresh/proxy argv).
Panel may instead specify trust_file, a Root-0600 JSON created later with only
the four CA keys above; URLs/callbacks stay in the immutable manifest.
The credentials file is the canonical panel_probe.php Root-0600 export.
Callbacks have public argv; requests, including proxy UUID, use stdin only.
The proxy callback runs this file's probe-stdin action on the panel VM.
Parent must preserve any existing standard layout before start-rest; this
runner refuses existing standard paths rather than moving/deleting them.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import socketserver
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools/node"))
import node_endpoint as ne

STANDARD_ROOT = Path("/etc/xboard-node")
STANDARD_BINARY = Path("/usr/local/bin/xboard-node")
STANDARD_UNIT = Path("/etc/systemd/system/xboard-node.service")
STANDARD_DROPIN = Path(str(STANDARD_UNIT) + ".d")
STANDARD_TEXT = """[Unit]
Description=Xboard Node Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/etc/xboard-node
EnvironmentFile=-/etc/xboard-node/credentials.env
ExecStart=/usr/local/bin/xboard-node -c /etc/xboard-node/config.yml
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
LimitNOFILE=1048576
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


class LabError(RuntimeError):
    """Only fixed public reason codes may escape the private runner."""


def ensure(condition, code):
    if not condition:
        raise LabError(code)


def write(path, data, mode=0o600):
    ne.atomic_write(path, data.encode() if isinstance(data, str) else data, mode)


def write_json(path, value):
    write(path, json.dumps(value, indent=2) + "\n")


def private_json(path):
    path = ne.safe_path(path, private=True)
    ne.require_root_ancestry(path)
    return json.loads(path.read_text("utf-8"))


def digest_file(path):
    return ne.digest(Path(path).read_bytes())


def remove_owned_standard_dropin(directory, expected):
    """Caller has verified the standard layout's private ownership marker."""
    directory = ne.safe_path(directory, existing=False)
    ensure(directory.is_dir(), 'standard_dropin_not_directory')
    ne.require_root_ancestry(directory)
    dropin = directory / '90-node-endpoint.conf'
    children = list(directory.iterdir())
    ensure(not children or children == [dropin], 'foreign_standard_dropin')
    if children:
        ne.require_root_ancestry(ne.safe_path(dropin))
        ensure(dropin.read_bytes() == expected, 'foreign_standard_dropin')
        dropin.unlink()
    directory.rmdir()


def exact(connection, size):
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise LabError("proxy_closed_early")
        data.extend(part)
    return bytes(data)


def proxy_probe(spec):
    """A real SOCKS5/VLESS client; its UUID arrives via private stdin/file."""
    ensure(spec.get("protocol") in ("vless", "socks"), "unsupported_proxy_protocol")
    host = str(ipaddress.ip_address(spec["host"]))
    target = str(ipaddress.ip_address(spec.get("target", "127.0.0.1")))
    ensure(target == "127.0.0.1", "echo_target_must_be_loopback")
    port, echo_port = int(spec["port"]), int(spec["echo_port"])
    ensure(1024 <= port <= 65535 and 1024 <= echo_port <= 65535, "invalid_proxy_port")
    credential = str(uuid.UUID(spec["uuid"]))
    size = int(spec.get("bytes", 1024 * 1024))
    ensure(1 <= size <= 8 * 1024 * 1024, "invalid_proxy_payload_size")
    hold = float(spec.get("hold_seconds", 2))
    ensure(0 <= hold <= 10, "invalid_proxy_hold_duration")
    chunk = secrets.token_bytes(min(size, 16384))
    payload = (chunk * ((size + len(chunk) - 1) // len(chunk)))[:size]
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(8)
        expected_source = spec.get("expected_source")
        source_validated = expected_source is not None and ipaddress.ip_address(connection.getsockname()[0]) == ipaddress.ip_address(expected_source)
        ensure(expected_source is None or source_validated, "proxy_client_is_not_on_expected_panel_vm")
        if spec["protocol"] == "socks":
            connection.sendall(b"\x05\x01\x02")
            ensure(exact(connection, 2) == b"\x05\x02", "socks_method_rejected")
            value = credential.encode()
            connection.sendall(b"\x01" + bytes([len(value)]) + value + bytes([len(value)]) + value)
            ensure(exact(connection, 2) == b"\x01\x00", "proxy_auth_rejected")
            connection.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(target) + struct.pack("!H", echo_port))
            reply = exact(connection, 4)
            ensure(reply[:2] == b"\x05\x00", "proxy_target_rejected")
            if reply[3] == 1:
                exact(connection, 6)
            elif reply[3] == 4:
                exact(connection, 18)
            elif reply[3] == 3:
                exact(connection, exact(connection, 1)[0] + 2)
            else:
                raise LabError("invalid_socks_reply")
        else:
            connection.sendall(b"\x00" + uuid.UUID(credential).bytes + b"\x00\x01" +
                               struct.pack("!H", echo_port) + b"\x01" + socket.inet_aton(target))
        # Interleave bounded writes/reads: large sendall-before-read can deadlock
        # a correct proxy when both application TCP buffers fill with echoed data.
        received = bytearray()
        for offset in range(0, size, 16384):
            piece = payload[offset:offset + 16384]
            connection.sendall(piece)
            if spec["protocol"] == "vless" and offset == 0:
                version, addons = exact(connection, 2)
                ensure(version == 0, "invalid_vless_response")
                exact(connection, addons)
            received.extend(exact(connection, len(piece)))
        ensure(bytes(received) == payload, "proxy_echo_bytes_changed")
        # Both real counters must get a track tick before report/queue assertion.
        time.sleep(hold)
    return {"passed": True, "bytes_uploaded": size, "bytes_downloaded": size,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "protocol": spec["protocol"], "node_id": int(spec.get("node_id", 0)),
            "cross_vm_source_validated": source_validated}


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    return
                self.request.sendall(data)
        except OSError:
            return


class EchoServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def stable_nft(value, counter=False):
    """Ignore dynamic packet/byte counters, preserve all policy/rule fields."""
    if isinstance(value, dict):
        return {key: stable_nft(item, counter=key == "counter") for key, item in value.items()
                if key not in ("handle", "metainfo") and not (counter and key in ("packets", "bytes"))}
    if isinstance(value, list):
        return [stable_nft(item, counter=counter) for item in value if not (isinstance(item, dict) and "metainfo" in item)]
    return value


def panel_global_guard(value):
    return value.get("global_settings_unchanged") is True or all(value.get(key) is True for key in
           ("test_auth_active", "test_auth_setting_matches_owned", "other_global_settings_unchanged"))


def ws_user_update_evidence(messages, bindings):
    mapping = {(row["protocol"], row["port"]): row["id"] for row in bindings}
    seen = set()
    disconnected = False
    for message in messages:
        if not isinstance(message, str):
            continue
        message = re.sub(r"\x1b\[[0-9;]*m", "", message)
        disconnected = disconnected or "ws disconnected" in message or "scheduling REST reconciliation" in message
        event = re.search(r"\[([a-z]+):(\d+)\]\s+users updated, 1 users(?:\s|$)", message)
        if event:
            key = (event[1], int(event[2]))
            if key in mapping:
                seen.add(mapping[key])
    return seen, disconnected


def observer_user_update_evidence(messages, bindings):
    """Native Observer delta stays a delta for node mode; machine mailbox
    converts it into a full nonempty user list. Preserve this distinction.
    """
    expected = {(row["protocol"], row["port"]): row for row in bindings}
    deltas, full = set(), set()
    disconnected = False
    for message in messages:
        if not isinstance(message, str):
            continue
        message = re.sub(r"\x1b\[[0-9;]*m", "", message)
        disconnected = disconnected or "ws disconnected" in message or "scheduling REST reconciliation" in message
        event = re.search(r"\[([a-z]+):(\d+)\]\s+(users delta: add, 1 users|users updated, 1 users)(?:\s|$)", message)
        if not event:
            continue
        row = expected.get((event[1], int(event[2])))
        if row and row["mode"] == "node" and event[3] == "users delta: add, 1 users":
            deltas.add(row["id"])
        elif row and row["mode"] == "machine" and event[3] == "users updated, 1 users":
            full.add(row["id"])
    return deltas, full, disconnected


class Runner:
    def __init__(self, manifest_path):
        ne.require_linux_root()
        self.manifest_path = ne.safe_path(manifest_path, private=True)
        ne.require_root_ancestry(self.manifest_path)
        self.manifest = private_json(self.manifest_path)
        ensure(self.manifest.get("schema") == 1, "unsupported_manifest_schema")
        self.run_id = self.manifest.get("run_id", "")
        ensure(re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", self.run_id), "invalid_run_id")
        self.base = ne.safe_path(self.manifest["base"], existing=False)
        ne.require_root_ancestry(self.base)
        ensure(re.fullmatch(r"/[A-Za-z0-9_./-]+", str(self.base)), "unsafe_base_path")
        self.panel_ip = str(ipaddress.IPv4Address(self.manifest["panel_ip"]))
        self.node_ip = str(ipaddress.IPv4Address(self.manifest["node_ip"]))
        ensure(self.panel_ip != self.node_ip, "two_different_vms_required")
        self.panel = dict(self.manifest["panel"])
        if self.panel.get("trust_file"):
            trust_path = ne.safe_path(self.panel["trust_file"], existing=False)
            ne.require_root_ancestry(trust_path)
            if trust_path.exists():
                trust = private_json(trust_path)
                allowed = {"ca_file", "ca_fingerprint", "dual_ca_file", "dual_ca_fingerprint"}
                ensure(isinstance(trust, dict) and set(trust).issubset(allowed), "trust_file_contains_non_ca_parameters")
                for prefix in ("", "dual_"):
                    ca_key, pin_key = prefix + "ca_file", prefix + "ca_fingerprint"
                    ensure((ca_key in trust) == (pin_key in trust), "trust_file_requires_paired_path_and_fingerprint")
                    if ca_key in trust:
                        ensure(isinstance(trust[ca_key], str) and Path(trust[ca_key]).is_absolute(), "invalid_trust_ca_path")
                        ne.require_root_ancestry(ne.safe_path(trust[ca_key], private=True))
                        pins = trust[pin_key].replace(":", "").split(",") if isinstance(trust[pin_key], str) else []
                        ensure(1 <= len(pins) <= 2 and all(re.fullmatch(r"[a-fA-F0-9]{64}", pin) for pin in pins), "invalid_independent_trust_fingerprint")
                self.panel.update(trust)
        self.old_url = ne.canonical_url(self.panel["old_url"], allow_http=True)
        self.new_url = ne.canonical_url(self.panel["new_url"])
        ensure(self.old_url.startswith("http://") and self.new_url.startswith("https://"), "expected_old_http_new_https")
        from urllib.parse import urlsplit
        ensure(all(urlsplit(url).hostname == self.panel_ip for url in (self.old_url, self.new_url)), "unexpected_panel_destination")
        self.credential_path = ne.safe_path(self.manifest["credentials_file"], private=True)
        self.credential = private_json(self.credential_path)
        self.validate_credentials(self.credential)
        self.nodes = self.credential["nodes"]
        self.ports = [int(row["port"]) for row in self.nodes]
        self.binary = ne.safe_path(self.manifest["binary"])
        lock = json.loads(ne.LOCK_PATH.read_text())
        self.binary_sha = digest_file(self.binary)
        ensure(self.binary_sha == lock["assets"]["xboard-node-linux-amd64"], "uploaded_asset_pin_mismatch")
        binary_header = self.binary.read_bytes()[:20]
        ensure(binary_header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(binary_header[18:20], "little") == 62,
               "uploaded_asset_not_amd64_elf")
        self.state_path = self.base / "state.json"
        self.state = private_json(self.state_path) if self.state_path.exists() else None
        if self.state:
            ensure(self.state.get("schema") == 1 and self.state.get("run_id") == self.run_id and
                   self.state.get("manifest_sha256") == digest_file(self.manifest_path), "runner_state_owner_mismatch")
            ensure(self.state.get("credential_identity_sha256") == self.credential_identity_sha256(), "fixture_binding_or_token_changed")
            ensure(self.base.stat().st_uid == 0 and not self.base.stat().st_mode & 0o077, "base_must_be_root_private")

    def validate_credentials(self, value):
        ensure(value.get("schema") == 1 and value.get("run_id") == self.run_id, "credential_run_mismatch")
        ensure(type(value.get("user", {}).get("id")) is int and value["user"]["id"] > 0, "invalid_fixture_user")
        uuid.UUID(value["user"]["uuid"])
        ensure(type(value.get("machine", {}).get("id")) is int and value["machine"]["id"] > 0, "invalid_fixture_machine")
        for token in (value.get("node_token", ""), value["machine"].get("token", "")):
            ensure(re.fullmatch(r"[A-Za-z0-9_.~+/=-]{1,4096}", token), "invalid_private_token_alphabet")
        nodes = value.get("nodes", [])
        ensure(len(nodes) == 4, "exactly_four_fixture_nodes_required")
        ensure(len({row["id"] for row in nodes}) == len({row["port"] for row in nodes}) == 4, "duplicate_fixture_node_or_port")
        ensure(sum(row["mode"] == "node" for row in nodes) == 2 and sum(row["mode"] == "machine" for row in nodes) == 2,
               "two_node_and_two_machine_bindings_required")
        for row in nodes:
            ensure(type(row["id"]) is int and row["id"] > 0 and type(row["port"]) is int and 1024 <= row["port"] <= 65535,
                   "invalid_fixture_binding")
            ensure((row["mode"], row["protocol"], row["kernel"]) in (("node", "vless", "xray"), ("machine", "socks", "singbox")),
                   "unexpected_fixture_protocol_kernel")

    def save(self):
        write_json(self.state_path, self.state)
        self.public_report()

    def credential_identity_sha256(self):
        return ne.digest(json.dumps({"run_id": self.credential["run_id"], "node_token": self.credential["node_token"],
                         "machine": self.credential["machine"], "nodes": self.credential["nodes"],
                         "user_id": self.credential["user"]["id"]}, sort_keys=True).encode())

    def record(self, name, **details):
        # Callers supply only fixed labels/public counters, never panel objects.
        self.state["checks"].append({"name": name, "passed": True, **details})
        self.save()

    def public_report(self):
        value = {"schema": 1, "run_id": self.run_id, "result": self.state.get("result", "IN_PROGRESS"),
                 "binary_sha256": self.binary_sha, "upstream_version": "v1.13-orphan.4",
                 "source_commit": "8dbee14fb2f32a4f9324929fc24828cea6e29b38",
                 "runner_sha256": digest_file(__file__), "node_tool_sha256": digest_file(ne.__file__),
                 "asset_staging": "uploaded fixed release asset, independently SHA256/ELF verified; CLI network download excluded",
                 "checks": self.state["checks"], "check_count": len(self.state["checks"]),
                 "cleanup_failures": self.state.get("cleanup_failures", [])}
        write_json(self.base / "report.json", value)
        return value

    def command(self, argv, label, input_bytes=None, timeout=90, allow_failure=False):
        ensure(re.fullmatch(r"[a-zA-Z0-9_-]+", label), "invalid_private_artifact_label")
        stamp = secrets.token_hex(3)
        prefix = self.base / (label + "-" + stamp)
        process = subprocess.run(argv, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=timeout, check=False)
        write(Path(str(prefix) + "-stdout.private"), process.stdout)
        write(Path(str(prefix) + "-stderr.private"), process.stderr)
        if not allow_failure:
            ensure(process.returncode == 0, "private_command_failed_" + label)
        return process

    def callback(self, name, request=None):
        argv = self.manifest.get("callbacks", {}).get(name)
        ensure(isinstance(argv, list) and argv and all(isinstance(part, str) and "\x00" not in part for part in argv),
               "missing_callback_" + name.replace("-", "_"))
        # Explicit public-argv contract also catches accidental manifest leakage.
        secret_values = (self.credential["node_token"], self.credential["machine"]["token"], self.current_uuid())
        ensure(not any(secret and secret in part for secret in secret_values for part in argv), "credential_in_callback_argv")
        value = {"run_id": self.run_id, **(request or {})}
        process = self.command(argv, "callback_" + name.replace("-", "_"), json.dumps(value).encode(), timeout=120)
        result = json.loads(process.stdout)
        ensure(isinstance(result, dict) and result.get("status") != "ERROR", "callback_rejected_" + name.replace("-", "_"))
        return result

    def current_uuid(self):
        return self.state.get("current_uuid", self.credential["user"]["uuid"]) if self.state else self.credential["user"]["uuid"]

    def inspect_panel(self):
        value = self.callback("inspect")
        ensure(value.get("run") == self.run_id and value.get("user", {}).get("id") == self.credential["user"]["id"],
               "panel_inspect_fixture_identity_mismatch")
        ensure({row["id"] for row in value.get("nodes", [])} == {row["id"] for row in self.nodes}, "panel_inspect_binding_mismatch")
        ensure(panel_global_guard(value) and value.get("foreign_records_unchanged") is True,
               "foreign_panel_state_changed")
        return value

    @contextmanager
    def lock(self):
        import fcntl
        ensure(self.base.is_dir() and self.state is not None, "initialize_runner_first")
        path = self.base / ".runner.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            ensure(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077,
                   "unsafe_runner_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LabError("another_runner_action_is_active")
            self.state = private_json(self.state_path)
            yield
        finally:
            os.close(fd)

    def init(self):
        ensure(self.state is None and not self.base.exists(), "runner_base_must_be_new")
        self.base.mkdir(mode=0o700)
        self.state = {"schema": 1, "run_id": self.run_id, "marker": secrets.token_hex(24),
                      "manifest_sha256": digest_file(self.manifest_path), "started_at": int(time.time()),
                      "credential_identity_sha256": self.credential_identity_sha256(),
                      "checks": [], "units": {}, "backups": [], "standard_created": False,
                      "current_uuid": self.credential["user"]["uuid"], "result": "IN_PROGRESS"}
        self.save()
        self.state["protected_services"] = self.protected_services()
        self.record("fixed-uploaded-amd64-release-sha256-and-elf")

    def protected_services(self):
        result = {}
        for unit in self.manifest.get("protected_services", ["docker.service", "flowscope-collector.service"]):
            ensure(re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit) and unit != "xboard-node.service", "invalid_protected_service")
            process = self.command(["systemctl", "show", unit, "--property=ActiveState,MainPID,FragmentPath", "--no-pager"],
                                   "protected_service", allow_failure=True)
            result[unit] = process.stdout.decode().strip()
        return result

    def nft_json(self, table=None):
        argv = ["nft", "-j", "list", "table", "inet", table] if table else ["nft", "-j", "list", "ruleset"]
        return json.loads(self.command(argv, "nft_inventory").stdout)

    def nft_foreign_hash(self, table=None):
        value = self.nft_json()
        entries = value.get("nftables", [])
        if table:
            entries = [entry for entry in entries if not any(isinstance(item, dict) and item.get("family") == "inet" and
                       (item.get("table") == table or (kind == "table" and item.get("name") == table))
                       for kind, item in entry.items())]
        return ne.digest(json.dumps(stable_nft(entries), sort_keys=True).encode())

    def install_network(self):
        ensure(not self.state.get("nft_table"), "fixture_network_already_initialized")
        table = "node_e2e_" + secrets.token_hex(8)
        self.state["nft_table"] = table
        self.state["foreign_nft_sha256"] = self.nft_foreign_hash()
        self.save()  # Ownership intent precedes the first network mutation.
        ports = ", ".join(map(str, self.ports))
        script = ("table inet " + table + " {\n chain fixture_input { type filter hook input priority -5; policy accept;\n"
                  + "  iifname \"lo\" ip saddr 127.0.0.0/8 tcp dport { " + ports + " } accept\n"
                  + "  iifname \"lo\" ip6 saddr ::1 tcp dport { " + ports + " } accept\n"
                  + "  ip saddr " + self.panel_ip + " tcp dport { " + ports + " } accept\n"
                  + "  tcp dport { " + ports + " } drop\n }\n}\n")
        write(self.base / "network-owned.private.nft", script)
        self.command(["nft", "-f", "-"], "nft_install", script.encode())
        value = self.nft_json(table)
        self.state["own_nft_sha256"] = ne.digest(json.dumps(stable_nft(value), sort_keys=True).encode())
        self.save()
        ensure(self.nft_foreign_hash(table) == self.state["foreign_nft_sha256"], "existing_nft_policy_changed")
        self.record("own-four-port-nft-boundary-foreign-tables-preserved")

    def start_echo(self):
        ensure(not self.state.get("echo_pid"), "echo_already_started")
        stdout = os.open(self.base / "echo-stdout.private", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stderr = os.open(self.base / "echo-stderr.private", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--action", "echo-daemon", "--manifest", str(self.manifest_path)],
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
        finally:
            os.close(stdout)
            os.close(stderr)
        self.state["echo_pid"] = process.pid
        self.save()
        until = time.monotonic() + 10
        while time.monotonic() < until:
            ensure(process.poll() is None, "echo_daemon_exited")
            ready = self.base / "echo-ready.json"
            if ready.exists():
                value = private_json(ready)
                ensure(value["pid"] == process.pid and value["marker"] == self.state["marker"], "echo_owner_mismatch")
                self.state["echo_port"] = value["port"]
                self.save()
                return
            time.sleep(0.1)
        raise LabError("echo_daemon_not_ready")

    def echo_daemon(self):
        ensure(self.state is not None, "echo_state_missing")
        server = EchoServer(("127.0.0.1", 0), EchoHandler)
        write_json(self.base / "echo-ready.json", {"pid": os.getpid(), "port": server.server_address[1], "marker": self.state["marker"]})
        stop = threading.Event()
        def request_stop(*unused):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()

    def standard_config(self):
        node_rows = [row for row in self.nodes if row["mode"] == "node"]
        text = ("log:\n  level: info\nhealth_port: 0\nnode:\n  push_interval: 5\n  pull_interval: 30\n  track_interval: 1\n"
                "  device_report_interval: 2\nws:\n  status_interval: 5\n  discovery_interval: 5\ninstances:\n"
                "  - panel:\n      url: " + json.dumps(self.old_url) + "\n      token_env: E2E_NODE_TOKEN\n"
                "    kernel:\n      type: xray\n      config_dir: /etc/xboard-node/e2e-node-data\n    nodes:\n")
        text += "".join("      - node_id: " + str(row["id"]) + "\n        node_type: vless\n" for row in node_rows)
        text += ("  - panel:\n      url: " + json.dumps(self.old_url) + "\n"
                 "    machine:\n      machine_id: " + str(self.credential["machine"]["id"]) + "\n      token_env: E2E_MACHINE_TOKEN\n"
                 "    kernel:\n      type: singbox\n      config_dir: /etc/xboard-node/e2e-machine-data\n")
        ne.parse(text)
        return text

    def add_unit(self, service, unit_bytes, modes=None):
        self.state["units"][service] = {"sha256": ne.digest(unit_bytes), "since": int(time.time()), "modes": modes or ["node", "machine"]}
        self.save()  # Record ownership before writing/starting any service.

    def assert_owned_unit(self, service):
        ensure(service in self.state["units"], "service_not_owned_by_fixture")
        unit = ne.safe_path(Path("/etc/systemd/system") / service)
        ne.require_root_ancestry(unit)
        ensure(digest_file(unit) == self.state["units"][service]["sha256"], "owned_unit_changed_refuse_restart")
        ensure(self.unit_state(service).get("FragmentPath") == str(unit), "owned_unit_fragment_changed")

    def start_rest(self, require_stopped_worker=True):
        ensure(not self.state.get("standard_created"), "standard_fixture_already_created")
        ensure(not any(path.exists() or path.is_symlink() for path in (STANDARD_ROOT, STANDARD_BINARY, STANDARD_UNIT, STANDARD_DROPIN)),
               "parent_must_preserve_existing_standard_layout")
        process = self.command(["systemctl", "show", "xboard-node.service", "--property=LoadState", "--value"], "standard_absence", allow_failure=True)
        ensure(process.stdout.strip() == b"not-found", "existing_standard_unit_loaded")
        for port in self.ports:
            with socket.socket() as probe:
                probe.bind(("0.0.0.0", port))
        before = self.inspect_panel()
        ensure(not any(row["ws_alive"] for row in before["nodes"]), "fixture_identities_already_connected")
        if require_stopped_worker:
            ensure(not before["worker_heartbeat"], "rest_baseline_requires_stopped_worker")
        self.install_network()
        self.start_echo()
        STANDARD_ROOT.mkdir(mode=0o700)
        self.state["standard_created"] = True
        self.save()
        write_json(STANDARD_ROOT / ".e2e-owner.json", {"run_id": self.run_id, "marker": self.state["marker"]})
        write(STANDARD_BINARY, self.binary.read_bytes(), 0o700)
        text = self.standard_config()
        write(STANDARD_ROOT / "config.yml", text)
        write(STANDARD_ROOT / "credentials.env", "E2E_NODE_TOKEN=" + self.credential["node_token"] + "\nE2E_MACHINE_TOKEN=" + self.credential["machine"]["token"] + "\n")
        rows = ne.describe(text)[1]
        metadata = {"version": "v1.13-orphan.4", "config_mode": "instances", "instance_count": 2,
                    "latest_instance_id": rows[0]["id"], "e2e_owner": self.state["marker"],
                    "instances": [{"id": row["id"], "panel_url": row["panel_url"], "mode": row["mode"]} for row in rows]}
        write_json(STANDARD_ROOT / "install-meta.json", metadata)
        for name in ("e2e-node-data", "e2e-machine-data"):
            directory = STANDARD_ROOT / name
            directory.mkdir(mode=0o700)
            write(directory / "sentinel.txt", self.state["marker"])
        write(self.base / "original-config.private.yml", text)
        write_json(self.base / "original-meta.private.json", metadata)
        self.state["credentials_sha256"] = digest_file(STANDARD_ROOT / "credentials.env")
        self.add_unit("xboard-node.service", STANDARD_TEXT.encode())
        write(STANDARD_UNIT, STANDARD_TEXT, 0o644)
        self.command(["systemctl", "daemon-reload"], "daemon_reload")
        self.command(["systemctl", "start", "xboard-node.service"], "start_standard")
        self.state["phase"] = "rest_baseline"
        self.save()
        self.runtime_check()
        self.record("real-panel-rest-baseline-node-multibinding-machine-two-bindings" if require_stopped_worker else
                    "real-panel-owned-standard-fixture-old-http-worker-already-started-node-and-machine-multibinding")

    def active_units(self):
        return [service for service in self.state["units"] if self.unit_state(service).get("ActiveState") == "active"]

    def unit_state(self, service):
        process = self.command(["systemctl", "show", service, "--property=ActiveState,MainPID,FragmentPath", "--no-pager"], "unit_state", allow_failure=True)
        return dict(line.split("=", 1) for line in process.stdout.decode().splitlines() if "=" in line)

    def runtime_check(self, timeout=60):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            units = self.active_units()
            if units and all(int(self.unit_state(service).get("MainPID", "0")) > 0 for service in units):
                try:
                    for row in self.nodes:
                        with socket.create_connection(("127.0.0.1", row["port"]), timeout=1):
                            pass
                    break
                except OSError:
                    pass
            time.sleep(0.5)
        else:
            raise LabError("real_go_proxy_ports_not_ready")
        for row in self.nodes:
            refused = False
            try:
                with socket.create_connection((self.node_ip, row["port"]), timeout=1):
                    pass
            except OSError:
                refused = True
            ensure(refused, "non_panel_proxy_source_not_refused")
        for service in self.active_units():
            status = self.unit_state(service)
            process = self.command(["ss", "-H", "-ltnp"], "listener_pid_inventory")
            listeners = process.stdout.decode()
            selected = [row for row in self.nodes if row["mode"] in self.state["units"][service]["modes"]]
            for row in selected:
                ensure(any(re.search(r":" + str(row["port"]) + r"\s", line) and
                           ("pid=" + status["MainPID"] + ",") in line for line in listeners.splitlines()),
                       "proxy_listener_not_owned_by_go_service_pid")
        ensure(self.nft_foreign_hash(self.state["nft_table"]) == self.state["foreign_nft_sha256"], "foreign_nft_policy_changed")
        self.record("real-systemd-go-proxy-listeners-own-nft-non-panel-source-rejected")

    def probe(self, row, credential=None, payload_bytes=1024 * 1024):
        spec = {"host": self.node_ip, "target": "127.0.0.1", "port": row["port"], "echo_port": self.state["echo_port"],
                "protocol": row["protocol"], "uuid": credential or self.current_uuid(), "bytes": payload_bytes,
                "hold_seconds": 2, "node_id": row["id"], "expected_source": self.panel_ip}
        value = self.callback("proxy", spec)
        ensure(value.get("passed") is True and value.get("node_id") == row["id"] and
               value.get("bytes_uploaded") == value.get("bytes_downloaded") == payload_bytes and
               value.get("cross_vm_source_validated") is True, "cross_vm_proxy_probe_failed")
        return value

    def traffic(self):
        self.runtime_check()
        before = self.inspect_panel()
        proxy_rows = [self.probe(row) for row in self.nodes]
        expected_total = sum(row["bytes_uploaded"] for row in proxy_rows)
        minimum_per_node = 1024 * 1024
        until = time.monotonic() + int(self.manifest.get("queue_timeout", 120))
        baseline_nodes = {row["id"]: row for row in before["nodes"]}
        while time.monotonic() < until:
            after = self.inspect_panel()
            deltas = {key: after["user"][key] - before["user"][key] for key in ("u", "d", "stat_u", "stat_d")}
            server_stats = [{"node_id": row["id"], "u": row["stat_u"] - baseline_nodes[row["id"]]["stat_u"],
                             "d": row["stat_d"] - baseline_nodes[row["id"]]["stat_d"]} for row in after["nodes"]]
            if all(value >= expected_total for value in deltas.values()) and all(row["u"] >= minimum_per_node and
                    row["d"] >= minimum_per_node for row in server_stats):
                self.record("cross-vm-real-proxy-rest-report-queue-user-and-each-server-db-metering",
                            payload_bytes_per_binding=minimum_per_node, binding_count=4,
                            user_uploaded_delta=deltas["u"], user_downloaded_delta=deltas["d"],
                            user_stat_uploaded_delta=deltas["stat_u"], user_stat_downloaded_delta=deltas["stat_d"],
                            per_binding_server_stats=server_stats)
                return
            time.sleep(2)
        raise LabError("real_traffic_queue_db_metering_not_observed")

    def assert_preserved(self):
        ensure(digest_file(STANDARD_BINARY) == self.binary_sha, "proxy_binary_changed")
        ensure(digest_file(STANDARD_ROOT / "credentials.env") == self.state["credentials_sha256"], "node_credentials_changed")
        for name in ("e2e-node-data", "e2e-machine-data"):
            ensure((STANDARD_ROOT / name / "sentinel.txt").read_text() == self.state["marker"], "instance_data_changed")
        value = ne.parse((STANDARD_ROOT / "config.yml").read_text()).plain()
        ensure([row["kernel"]["type"] for row in value["instances"]] == ["xray", "singbox"], "instance_kernel_changed")
        ensure([row["node_id"] for row in value["instances"][0]["nodes"]] == [row["id"] for row in self.nodes if row["mode"] == "node"],
               "traditional_multiple_bindings_changed")
        ensure(value["instances"][1]["machine"]["machine_id"] == self.credential["machine"]["id"], "machine_identity_changed")
        original_meta = private_json(self.base / "original-meta.private.json")
        expected_meta = json.loads(json.dumps(original_meta))
        configured = {row["mode"]: row for row in ne.describe((STANDARD_ROOT / "config.yml").read_text())[1]}
        for entry in expected_meta["instances"]:
            prior_id = entry["id"]
            current = configured[entry["mode"]]
            entry.update(id=current["id"], panel_url=current["panel_url"])
            if expected_meta["latest_instance_id"] == prior_id:
                expected_meta["latest_instance_id"] = current["id"]
        ensure(private_json(STANDARD_ROOT / "install-meta.json") == expected_meta, "unrelated_install_metadata_changed")

    def migrate(self, mode, dual=False):
        ensure(self.state.get("standard_created") and self.unit_state("xboard-node.service").get("ActiveState") == "active", "standard_fixture_not_active")
        self.assert_preserved()
        source = (STANDARD_ROOT / "config.yml").read_text()
        rows = ne.describe(source)[1]
        selected = next(row for row in rows if row["mode"] == mode)
        ensure(selected["panel_url"] == self.old_url, "fixture_binding_already_migrated")
        ca_file = self.panel["dual_ca_file" if dual else "ca_file"]
        pin = self.panel["dual_ca_fingerprint" if dual else "ca_fingerprint"]
        # No separate WS authentication against an active identity. Production
        # migration itself stops the sole owned service before its auth probe.
        argv = [sys.executable, str(REPOSITORY / "tools/node/node_endpoint.py"), "migrate", "--config", str(STANDARD_ROOT / "config.yml"),
                "--credentials", str(STANDARD_ROOT / "credentials.env"), "--meta", str(STANDARD_ROOT / "install-meta.json"),
                "--service", "xboard-node.service", "--instance-id", selected["id"], "--from-url", self.old_url,
                "--panel-url", self.new_url, "--ca-file", ca_file, "--ca-fingerprint", pin,
                "--confirm-migrate", "MIGRATE:" + selected["id"]]
        result = json.loads(self.command(argv, "migrate_" + mode, timeout=180).stdout)
        self.state["backups"].append(result["backup"])
        if mode == "node" and not dual:
            self.state["initial_private_root_sha256"] = pin.replace(":", "").lower()
        self.state["phase"] = "migrated_" + mode
        self.save()
        expected, unused_row, unused_id = ne.migrate_text(source, selected["id"], self.old_url, self.new_url)
        ensure((STANDARD_ROOT / "config.yml").read_text() == expected, "non_target_configuration_changed")
        self.assert_preserved()
        self.runtime_check()
        self.record("actual-cli-selected-" + mode + "-https-migration-all-bindings-kernel-data-preserved", dual_ca=dual)
        self.observe_tls_leaf()
        self.ws_check(mode)

    def observe_tls_leaf(self):
        from urllib.parse import urlsplit
        target = urlsplit(self.new_url)
        ca = STANDARD_ROOT / "node-endpoint-trust.pem"
        ensure(ca.is_file(), "service_trust_not_installed")
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((target.hostname, target.port or 443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=target.hostname) as secure:
                value = ne.digest(secure.getpeercert(binary_form=True))
        self.state["last_leaf_sha256"] = value
        self.save()
        return value

    def ws_check(self, mode=None):
        if mode is None and self.state.get("phase") != "new_install":
            rows = ne.describe((STANDARD_ROOT / "config.yml").read_text())[1]
            modes = {row["mode"] for row in rows if row["panel_url"] == self.new_url}
            selected = [row for row in self.nodes if row["mode"] in modes]
        else:
            selected = [row for row in self.nodes if mode is None or row["mode"] == mode]
        ensure(selected, "no_https_bindings_selected_for_wss_check")
        until = time.monotonic() + 60
        since = int(time.time()) - 10
        while time.monotonic() < until:
            value = self.inspect_panel()
            nodes = {row["id"]: row for row in value["nodes"]}
            if value["worker_heartbeat"] and all(nodes[row["id"]].get("ws_alive") and
                    nodes[row["id"]].get("metrics_ws_connected") and nodes[row["id"]]["metrics_updated_at"] >= since for row in selected):
                self.record("real-nodeworker-wss-active-" + (mode or "all") + "-fresh-go-metrics", binding_count=len(selected))
                return
            time.sleep(2)
        raise LabError("real_nodeworker_wss_metrics_not_observed")

    def rotate_users(self, observer_only=False, native_delta=False):
        # Strong evidence is an in-place update delivered by the real Redis
        # subscriber, with no reload/restart/authentication probe by this runner.
        if self.state.get("phase") != "new_install":
            ensure(all(row["panel_url"] == self.new_url for row in ne.describe((STANDARD_ROOT / "config.yml").read_text())[1]),
                   "both_fixture_instances_must_be_https_for_full_wss_uuid_rotate")
        self.ws_check()
        pids = {service: self.unit_state(service)["MainPID"] for service in self.active_units()}
        cursors = {}
        for service in pids:
            output = self.command(["journalctl", "--unit", service, "--lines=0", "--show-cursor", "--no-pager"], "rotate_journal_cursor").stdout.decode()
            match = re.search(r"^-- cursor: (.+)$", output, re.MULTILINE)
            ensure(match is not None, "rotation_journal_cursor_missing")
            cursors[service] = match[1].strip()
        old_uuid = self.current_uuid()
        self.callback("rotate-delta" if native_delta else "rotate-observer" if observer_only else "rotate")
        refreshed = self.callback("credentials-refresh")
        self.validate_credentials(refreshed)
        ensure(refreshed["nodes"] == self.credential["nodes"] and refreshed["machine"] == self.credential["machine"] and
               refreshed["node_token"] == self.credential["node_token"], "rotate_changed_other_fixture_credentials")
        new_uuid = refreshed["user"]["uuid"]
        ensure(new_uuid != old_uuid and refreshed["user"]["id"] == self.credential["user"]["id"], "fixture_uuid_not_rotated")
        write_json(self.base / "rotated-credentials.private.json", refreshed)
        until = time.monotonic() + 60
        for row in self.nodes:
            while True:
                try:
                    self.probe(row, new_uuid, 16384)
                    break
                except (LabError, OSError):
                    reason = ("native_delta_new_uuid_not_applied" if native_delta else
                              "observer_user_sync_new_uuid_not_applied" if observer_only else
                              "redis_full_sync_new_uuid_not_applied")
                    ensure(time.monotonic() < until, reason)
                    time.sleep(1)
            try:
                self.probe(row, old_uuid, 16384)
            except LabError:
                pass
            else:
                raise LabError("old_uuid_still_authenticated")
        ensure(pids == {service: self.unit_state(service)["MainPID"] for service in self.active_units()}, "node_restarted_during_redis_sync")
        messages = []
        for service, cursor in cursors.items():
            output = self.command(["journalctl", "--unit", service, "--after-cursor", cursor, "--no-pager", "--output=json"], "rotate_private_ws_journal").stdout
            ensure(len(output) <= 4 * 1024 * 1024, "rotation_private_journal_too_large")
            messages.extend(json.loads(line).get("MESSAGE") for line in output.decode().splitlines() if line.strip())
        if observer_only or native_delta:
            deltas, full, disconnected = observer_user_update_evidence(messages, self.nodes)
            ensure(deltas == {row["id"] for row in self.nodes if row["mode"] == "node"} and
                   full == {row["id"] for row in self.nodes if row["mode"] == "machine"} and not disconnected,
                   "native_observer_delta_and_machine_full_user_events_without_disconnect_not_observed")
            bindings = deltas | full
        else:
            bindings, disconnected = ws_user_update_evidence(messages, self.nodes)
            ensure(bindings == {row["id"] for row in self.nodes} and not disconnected, "four_exclusive_ws_user_events_without_disconnect_not_observed")
        self.ws_check()
        self.state["current_uuid"] = new_uuid
        self.save()
        details = {"binding_count": 4, "exclusive_go_ws_user_event_binding_count": len(bindings),
                   "ws_disconnect_or_rest_reconciliation_observed": False}
        if observer_only or native_delta:
            details.update(traditional_ws_delta_add_binding_count=len(deltas), machine_mailbox_full_user_binding_count=len(full))
        if native_delta:
            details["direct_native_node_sync_service_dispatch"] = True
        name = ("real-native-node-sync-service-redis-nodeworker-new-uuid-works-old-rejected-without-node-restart" if native_delta else
                "real-normal-observer-queue-redis-nodeworker-new-uuid-works-old-rejected-without-node-restart" if observer_only else
                "real-redis-nodeworker-full-sync-new-uuid-works-old-rejected-without-node-restart")
        self.record(name, **details)

    def restart_check(self):
        self.assert_owned_unit("xboard-node.service")
        before = digest_file(STANDARD_ROOT / "node-endpoint-trust.pem")
        old_leaf = self.state.get("last_leaf_sha256")
        self.command(["systemctl", "restart", "xboard-node.service"], "restart_standard")
        self.runtime_check()
        self.ws_check()
        ensure(digest_file(STANDARD_ROOT / "node-endpoint-trust.pem") == before, "trust_changed_during_certificate_restart")
        new_leaf = self.observe_tls_leaf()
        ensure(old_leaf is not None, "prior_panel_leaf_observation_missing")
        changed = old_leaf != new_leaf
        self.record("real-go-reconnected-changed-panel-certificate-existing-trust-unchanged" if changed else
                    "real-go-reconnected-existing-panel-certificate-existing-trust-unchanged",
                    leaf_sha256=new_leaf, leaf_changed=changed)

    def device_cycle(self):
        if self.state.get("phase") != "new_install":
            ensure(all(row["panel_url"] == self.new_url for row in ne.describe((STANDARD_ROOT / "config.yml").read_text())[1]),
                   "device_cycle_requires_both_https_fixture_instances")
        self.ws_check()
        active = self.active_units()
        ensure(active, "no_owned_node_services_active")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.callback, "proxy", {"host": self.node_ip, "target": "127.0.0.1", "port": row["port"],
                       "echo_port": self.state["echo_port"], "protocol": row["protocol"], "uuid": self.current_uuid(),
                       "bytes": 65536, "hold_seconds": 10, "node_id": row["id"], "expected_source": self.panel_ip}) for row in self.nodes]
            until = time.monotonic() + 9
            positive = None
            while time.monotonic() < until:
                value = self.inspect_panel()
                if all(row.get("ws_alive") and row.get("stored_device_entries", 0) > 0 for row in value["nodes"]):
                    positive = value
                    break
                time.sleep(0.5)
            ensure(positive is not None, "live_wss_device_cache_positive_not_observed")
            # Stop while authenticated held connections still populated every
            # node cache. Their real WS onClose must remove fields before TTL.
            self.stop_owned()
            until = time.monotonic() + 25
            while time.monotonic() < until:
                value = self.inspect_panel()
                if all(not row["ws_alive"] and row.get("stored_device_entries", -1) == 0 for row in value["nodes"]):
                    break
                time.sleep(1)
            else:
                raise LabError("stopped_nodeworker_device_cache_was_not_cleared")
            for future in futures:
                result = future.result()
                ensure(result.get("passed") is True and result.get("cross_vm_source_validated") is True, "held_cross_vm_proxy_failed")
        for service in active:
            self.assert_owned_unit(service)
            self.command(["systemctl", "start", service], "restart_after_device_clear")
        self.runtime_check()
        self.ws_check()
        self.record("real-held-cross-vm-connections-wss-device-cache-positive-onclose-zero-before-ttl-and-reconnected",
                    binding_count=4, positive_device_entries=[{"node_id": row["id"], "entries": row["stored_device_entries"]} for row in positive["nodes"]],
                    observed_user_online_count=positive["user"].get("online_count", 0), cleared_device_entries=0)

    def drop_old_ca(self):
        ensure(self.state.get("standard_created") and self.active_units() == ["xboard-node.service"], "drop_old_requires_sole_owned_standard_service")
        self.assert_owned_unit("xboard-node.service")
        ensure(not self.state.get("trust_change"), "fixture_trust_replacement_already_applied")
        ensure(all(row["panel_url"] == self.new_url for row in ne.describe((STANDARD_ROOT / "config.yml").read_text())[1]), "both_bindings_must_be_https")
        self.assert_preserved()
        ne.check_standard_service("xboard-node.service", STANDARD_ROOT / "config.yml", STANDARD_ROOT / "credentials.env")
        ca = ne.verify_ca(self.panel["ca_file"], self.panel["ca_fingerprint"])
        ensure(len(self.panel["ca_fingerprint"].split(",")) == 1, "new_only_requires_one_independent_root_pin")
        bundle = STANDARD_ROOT / "node-endpoint-trust.pem"
        before = ne.safe_path(bundle, private=True).read_bytes()
        ensure(self.state["backups"], "production_migration_snapshot_required")
        latest = private_json(Path(self.state["backups"][-1]) / "state.json")
        known = next(row for row in latest["files"] if row["path"] == str(bundle))["applied_sha256"]
        ensure(ne.digest(before) == known, "trust_changed_after_last_production_migration")
        new_bundle = ne.trust_bundle(ca)
        # Inspect only public roots. Keep unrelated system trust intact.
        def fingerprints(data):
            certs = re.findall(rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----", data)
            return {ne.digest(ssl.PEM_cert_to_DER_cert(cert.decode())) for cert in certs}
        selected_pin = next(iter(fingerprints(ca)))
        prior_pins = {part.replace(":", "").lower() for part in self.panel["dual_ca_fingerprint"].split(",")}
        ensure(len(prior_pins) == 2 and selected_pin in prior_pins and prior_pins <= fingerprints(before) and
               not (prior_pins - {selected_pin}) & fingerprints(new_bundle), "old_fixture_root_remains_in_new_only_bundle")
        # Prove the active leaf chains to the new pin before service mutation;
        # this TLS-only socket never authenticates against NodeWorker.
        from urllib.parse import urlsplit
        target = urlsplit(self.new_url)
        with socket.create_connection((target.hostname, target.port or 443), timeout=5) as raw:
            with ne.tls_context(ca).wrap_socket(raw, server_hostname=target.hostname):
                pass
        backup = self.base / "drop-old-trust-before.private.pem"
        ensure(not backup.exists(), "fixture_trust_backup_already_exists")
        write(backup, before)
        self.state["trust_change"] = {"backup": str(backup), "before_sha256": ne.digest(before), "after_sha256": ne.digest(new_bundle)}
        self.save()
        try:
            self.stop_owned()
            ensure(bundle.read_bytes() == before, "trust_changed_during_owned_stop")
            write(bundle, new_bundle)
            self.command(["systemctl", "start", "xboard-node.service"], "start_new_only_trust")
            self.runtime_check()
            self.ws_check()
            self.record("fixture-only-new-pinned-root-trust-replacement-real-go-wss-old-root-removed", current_root_sha256=selected_pin)
        except BaseException:
            self.restore_fixture_trust()
            raise

    def restore_fixture_trust(self):
        change = self.state.get("trust_change")
        if not change:
            return
        bundle = STANDARD_ROOT / "node-endpoint-trust.pem"
        backup = ne.safe_path(change["backup"], private=True)
        ensure(backup == self.base / "drop-old-trust-before.private.pem" and digest_file(backup) == change["before_sha256"], "fixture_trust_backup_corrupted")
        ensure(digest_file(bundle) in (change["before_sha256"], change["after_sha256"]), "fixture_trust_modified_after_replacement")
        self.stop_owned()
        write(bundle, backup.read_bytes())
        self.command(["systemctl", "start", "xboard-node.service"], "restore_before_production_rollback")
        self.state["trust_change"] = None
        self.save()

    def rollback(self):
        ensure(self.state["backups"], "no_fixture_migration_backup")
        # Restore fixture trust first: actual CLI applied-hash guards and
        # production backup metadata remain strict and entirely unmodified.
        self.restore_fixture_trust()
        for backup in reversed(self.state["backups"]):
            self.command([sys.executable, str(REPOSITORY / "tools/node/node_endpoint.py"), "rollback", "--backup", backup,
                          "--confirm-rollback", "ROLLBACK"], "rollback", timeout=120)
        self.state["backups"] = []
        self.state["phase"] = "rolled_back"
        self.save()
        ensure((STANDARD_ROOT / "config.yml").read_bytes() == (self.base / "original-config.private.yml").read_bytes(), "original_config_not_restored")
        ensure(private_json(STANDARD_ROOT / "install-meta.json") == private_json(self.base / "original-meta.private.json"), "original_metadata_not_restored")
        self.assert_preserved()
        self.runtime_check()
        self.record("actual-cli-reverse-rollback-original-urls-metadata-credentials-binary-and-bindings")

    def stop_owned(self):
        failures = []
        for service, owned in self.state["units"].items():
            try:
                unit = Path("/etc/systemd/system") / service
                if not unit.exists():
                    status = self.unit_state(service)
                    ensure(status.get("ActiveState") == "inactive" and status.get("MainPID") == "0", "missing_owned_unit_has_live_process")
                    continue
                ensure(digest_file(unit) == owned["sha256"], "owned_unit_changed_refuse_stop")
                ensure(self.unit_state(service).get("FragmentPath") == str(unit), "owned_unit_fragment_changed")
                self.command(["systemctl", "stop", service], "stop_owned")
                status = self.unit_state(service)
                ensure(status.get("ActiveState") == "inactive" and status.get("MainPID") == "0", "owned_unit_not_fully_stopped")
                self.command(["journalctl", "--unit", service, "--since", "@" + str(owned["since"]), "--no-pager", "--output=cat"], "journal_owned", allow_failure=True)
            except BaseException:
                failures.append(service)
        ensure(not failures, "one_or_more_owned_units_could_not_be_safely_stopped")

    def install_new(self):
        ensure(self.state.get("echo_port") and self.state.get("nft_table"), "network_and_echo_not_initialized")
        self.stop_owned()
        for mode, kernel in (("node", "xray"), ("machine", "singbox")):
            directory = self.base / ("new-install-" + mode)
            token_path = self.base / ("new-install-" + mode + "-token.private")
            token = self.credential["node_token"] if mode == "node" else self.credential["machine"]["token"]
            write(token_path, token + "\n")
            chosen = [row for row in self.nodes if row["mode"] == mode]
            argv = [sys.executable, str(REPOSITORY / "tools/node/node_endpoint.py"), "prepare", "--output-dir", str(directory),
                    "--panel-url", self.new_url, "--mode", mode, "--kernel", kernel, "--architecture", "amd64",
                    "--token-file", str(token_path), "--ca-file", self.panel.get("dual_ca_file", self.panel["ca_file"]),
                    "--ca-fingerprint", self.panel.get("dual_ca_fingerprint", self.panel["ca_fingerprint"]),
                    "--confirm-new-identity", "UNUSED"]
            if mode == "node":
                argv += ["--node-id", str(chosen[0]["id"]), "--node-type", "vless"]
            else:
                argv += ["--machine-id", str(self.credential["machine"]["id"])]
            prepared = json.loads(self.command(argv, "prepare_new_" + mode, timeout=180).stdout)
            write(directory / "xboard-node", self.binary.read_bytes(), 0o700)
            prepared["binary_downloaded"] = True
            prepared["files"]["xboard-node"] = self.binary_sha
            # Controlled fixture tuning, not a production-tool change. Add the
            # second traditional binding before CLI installation of the new unit.
            config = (directory / "config.yml").read_text()
            if mode == "node":
                config += "nodes:\n" + "".join("  - node_id: " + str(row["id"]) + "\n    node_type: vless\n" for row in chosen)
            config += "node:\n  push_interval: 5\n  pull_interval: 30\n  track_interval: 1\n  device_report_interval: 2\nws:\n  status_interval: 5\n  discovery_interval: 5\n"
            ne.parse(config)
            write(directory / "config.yml", config)
            prepared["files"]["config.yml"] = digest_file(directory / "config.yml")
            service = prepared["service"]
            unit = (directory / service).read_text().replace("\n[Service]\n", "\n[Service]\nIPAddressDeny=any\nIPAddressAllow=localhost\nIPAddressAllow=" + self.panel_ip + "/32\n", 1)
            write(directory / service, unit)
            prepared["files"][service] = digest_file(directory / service)
            prepared["fixture_asset_staging"] = "independent uploaded asset; CLI download=False"
            prepared["fixture_tuning"] = "push5/track1; traditional two bindings; own unit IP allow localhost+panelVM"
            write_json(directory / "prepared.json", prepared)
            self.add_unit(service, unit.encode(), [mode])
            self.command([sys.executable, str(REPOSITORY / "tools/node/node_endpoint.py"), "install-new", "--prepared-dir", str(directory),
                          "--confirm-install", "NEW"], "install_new_" + mode, timeout=180)
            properties = self.command(["systemctl", "show", service, "--property=IPAddressDeny,IPAddressAllow"], "new_unit_cgroup_filter").stdout.decode()
            ensure("IPAddressDeny=0.0.0.0/0 ::/0" in properties and self.panel_ip + "/32" in properties and
                   "127.0.0.0/8" in properties and "::1/128" in properties, "new_unit_cgroup_filter_not_applied")
        self.state["phase"] = "new_install"
        self.save()
        self.runtime_check()
        self.ws_check()
        self.record("actual-prepare-and-install-new-cli-independent-pin-stage-both-kernels-multiple-bindings")

    def start_new(self):
        # A separate cold-start acceptance path keeps user-update limitations
        # from obscuring new installation, device cleanup and traffic checks.
        ensure(not self.state.get("units") and not self.state.get("standard_created") and
               not self.state.get("phase"), "cold_install_requires_unused_fixture_state")
        for port in self.ports:
            with socket.socket() as probe:
                probe.bind(("0.0.0.0", port))
        before = self.inspect_panel()
        ensure(not any(row["ws_alive"] for row in before["nodes"]), "fixture_identities_already_connected")
        self.install_network()
        self.start_echo()
        self.install_new()

    def cleanup(self):
        failures = []
        try:
            self.stop_owned()
        except BaseException:
            failures.append("owned_services_stop_or_identity_verification_failed")
        if not failures:
            for service, owned in reversed(list(self.state["units"].items())):
                unit = Path("/etc/systemd/system") / service
                try:
                    if unit.exists():
                        ensure(digest_file(unit) == owned["sha256"], "unit_changed")
                        self.command(["systemctl", "disable", service], "disable_owned", allow_failure=True)
                        ensure(self.unit_state(service).get("ActiveState") == "inactive" and self.unit_state(service).get("MainPID") == "0", "unit_not_stopped")
                        unit.unlink()
                except BaseException:
                    failures.append("owned_unit_remove_failed")
            if self.state.get("standard_created") and not failures:
                try:
                    owner = private_json(STANDARD_ROOT / ".e2e-owner.json")
                    ensure(owner == {"run_id": self.run_id, "marker": self.state["marker"]}, "standard_owner_changed")
                    ensure(digest_file(STANDARD_BINARY) == self.binary_sha, "standard_binary_changed")
                    if STANDARD_DROPIN.exists():
                        expected = ("# NodeEndpoint 1.0.0 owned trust setting\n[Service]\nEnvironment=SSL_CERT_FILE=" + str(STANDARD_ROOT / "node-endpoint-trust.pem") + "\n").encode()
                        # The actual CLI rollback removes its file and may
                        # retain an empty directory. Ownership of this layout
                        # was verified above; foreign entries still fail closed.
                        remove_owned_standard_dropin(STANDARD_DROPIN, expected)
                    shutil.rmtree(STANDARD_ROOT)
                    STANDARD_BINARY.unlink()
                    self.state["standard_created"] = False
                except BaseException:
                    failures.append("own_standard_layout_cleanup_failed")
        try:
            self.command(["systemctl", "daemon-reload"], "cleanup_daemon_reload")
        except BaseException:
            failures.append("daemon_reload_failed")
        pid = self.state.get("echo_pid")
        if pid:
            try:
                proc = Path("/proc") / str(pid)
                if proc.exists():
                    cmdline = (proc / "cmdline").read_bytes().split(b"\0")
                    ensure(str(Path(__file__).resolve()).encode() in cmdline and str(self.manifest_path).encode() in cmdline,
                           "echo_process_owner_changed")
                    os.kill(pid, signal.SIGTERM)
                    until = time.monotonic() + 10
                    while proc.exists() and time.monotonic() < until:
                        # A zombie has released all sockets/resources; its
                        # original launching parent may no longer be alive.
                        status = (proc / "stat").read_text()
                        if status.split(") ", 1)[1].startswith("Z"):
                            break
                        time.sleep(0.1)
                    ensure(not proc.exists() or (proc / "stat").read_text().split(") ", 1)[1].startswith("Z"), "echo_process_not_stopped")
                self.state["echo_pid"] = None
            except BaseException:
                failures.append("owned_echo_cleanup_failed")
        table = self.state.get("nft_table")
        if table and not any("service" in item or "unit" in item or "layout" in item for item in failures):
            try:
                ensure(re.fullmatch(r"node_e2e_[a-f0-9]{16}", table), "nft_owner_name_changed")
                own = self.nft_json(table)
                ensure(ne.digest(json.dumps(stable_nft(own), sort_keys=True).encode()) == self.state.get("own_nft_sha256"), "own_nft_policy_changed")
                ensure(self.nft_foreign_hash(table) == self.state["foreign_nft_sha256"], "foreign_nft_policy_changed")
                self.command(["nft", "delete", "table", "inet", table], "nft_cleanup")
                ensure(self.nft_foreign_hash() == self.state["foreign_nft_sha256"], "foreign_nft_policy_changed_after_cleanup")
                self.state["nft_table"] = None
            except BaseException:
                failures.append("owned_nft_cleanup_failed_boundary_retained")
        try:
            ensure(self.protected_services() == self.state["protected_services"], "protected_services_changed")
        except BaseException:
            failures.append("protected_service_inventory_changed")
        self.state["cleanup_failures"] = failures
        self.state["result"] = "FAIL" if failures or self.state.get("failed") else "PASS"
        self.save()
        if failures:
            write(self.base / "RECOVERY_REQUIRED", "Owned lab resource cleanup needs review. See private state.json.\n")
            raise LabError("fixture_cleanup_requires_private_recovery_review")
        self.record("owned-units-pids-standard-layout-echo-network-cleaned-protected-services-unchanged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Root-owned 0600 JSON; contains paths, identities and callback argv")
    parser.add_argument("--action", required=True, choices=["init", "start-rest", "start-standard", "start-new", "runtime-check", "traffic", "migrate-node", "migrate-machine",
                        "migrate-machine-dual", "ws-check", "rotate-users", "rotate-users-observer", "rotate-users-delta", "restart-check", "device-cycle", "drop-old-ca", "rollback", "install-new", "stop", "cleanup", "report", "echo-daemon", "probe-stdin"])
    args = parser.parse_args()
    runner = None
    try:
        if args.action == "probe-stdin":
            ensure(sys.platform.startswith("linux") and os.geteuid() == 0, "probe_requires_linux_root_private_stdin")
            data = sys.stdin.buffer.read(65537)
            ensure(len(data) <= 65536, "private_proxy_input_too_large")
            result = proxy_probe(json.loads(data))
        else:
            ensure(args.manifest, "private_manifest_required")
            runner = Runner(args.manifest)
            if args.action == "init":
                runner.init()
            elif args.action == "echo-daemon":
                runner.echo_daemon()
                return 0
            elif args.action == "report":
                ensure(runner.state is not None, "runner_state_missing")
            else:
                with runner.lock():
                    actions = {"start-rest": runner.start_rest, "start-standard": lambda: runner.start_rest(require_stopped_worker=False),
                               "start-new": runner.start_new,
                               "runtime-check": runner.runtime_check, "traffic": runner.traffic,
                               "migrate-node": lambda: runner.migrate("node"), "migrate-machine": lambda: runner.migrate("machine"),
                               "migrate-machine-dual": lambda: runner.migrate("machine", dual=True), "ws-check": runner.ws_check,
                               "rotate-users": runner.rotate_users, "rotate-users-observer": lambda: runner.rotate_users(observer_only=True),
                               "rotate-users-delta": lambda: runner.rotate_users(native_delta=True),
                               "restart-check": runner.restart_check,
                               "device-cycle": runner.device_cycle, "drop-old-ca": runner.drop_old_ca,
                               "rollback": runner.rollback, "install-new": runner.install_new, "stop": runner.stop_owned,
                               "cleanup": runner.cleanup}
                    actions[args.action]()
            result = runner.public_report()
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except BaseException as error:
        reason = str(error) if isinstance(error, LabError) and re.fullmatch(r"[a-zA-Z0-9_]+", str(error)) else "private_fixture_error"
        if runner and runner.state:
            try:
                runner.state["failed"] = True
                runner.state["failure_reason"] = reason
                runner.state["result"] = "FAIL"
                # Full stack/exception arguments deliberately never printed.
                frames = traceback.extract_tb(error.__traceback__)
                write_json(runner.base / "failure-private.json", {"action": args.action, "class": type(error).__name__, "reason": reason,
                           "locations": [{"file": Path(frame.filename).name, "line": frame.lineno} for frame in frames]})
                runner.save()
                if args.action not in ("report", "cleanup", "echo-daemon"):
                    with runner.lock():
                        runner.cleanup()
            except BaseException:
                pass
        print(json.dumps({"result": "FAIL", "reason": reason}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
