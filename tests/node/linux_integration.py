#!/usr/bin/env python3
"""Real isolated Linux/systemd acceptance for the pinned independent Node.

Run only on an explicitly authorized disposable test VM, as Root. Uses private
synthetic identities, loopback REST/WSS and VLESS/SOCKS5-to-loopback-echo traffic.
Never contacts a real panel. The uploaded binary is checked against the release
lock; no upstream installer, rebuild, proxy-kernel patch or geodata is used.
"""
import argparse
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ipaddress
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools/node"))
import node_endpoint as ne


class FixtureError(RuntimeError):
    pass


def check(value, code):
    if not value:
        raise FixtureError(code)


def command(argv, timeout=45, check_exit=True):
    process = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=timeout, check=False)
    if check_exit:
        check(process.returncode == 0, "command_failed:" + Path(argv[0]).name)
    return process


def write(path, data, mode=0o600):
    ne.atomic_write(path, data.encode() if isinstance(data, str) else data, mode)


def available_port(reserved):
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        value = connection.getsockname()[1]
    if value in reserved:
        return available_port(reserved)
    reserved.add(value)
    return value


def exact(connection, size):
    result = b""
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            raise EOFError()
        result += part
    return result


def raw_frame(connection, opcode, data):
    if len(data) < 126:
        prefix = bytes([0x80 | opcode, len(data)])
    elif len(data) < 65536:
        prefix = bytes([0x80 | opcode, 126]) + struct.pack("!H", len(data))
    else:
        prefix = bytes([0x80 | opcode, 127]) + struct.pack("!Q", len(data))
    connection.sendall(prefix + data)


def frame(connection, payload):
    raw_frame(connection, 1, json.dumps(payload, separators=(",", ":")).encode())


def client_frames(buffer):
    """Consume complete masked frames only; retain partial data across timeout."""
    while len(buffer) >= 2:
        first, second = buffer[:2]
        check(not first & 0x70 and bool(second & 0x80), "invalid_client_ws_frame")
        length, offset = second & 127, 2
        if length in (126, 127):
            size = 2 if length == 126 else 8
            if len(buffer) < offset + size:
                return
            length = struct.unpack("!H" if size == 2 else "!Q", buffer[offset:offset + size])[0]
            offset += size
        check(length <= 1024 * 1024, "oversized_client_ws_frame")
        if len(buffer) < offset + 4 + length:
            return
        mask = buffer[offset:offset + 4]
        payload = buffer[offset + 4:offset + 4 + length]
        del buffer[:offset + 4 + length]
        opcode, final = first & 15, bool(first & 0x80)
        check(opcode < 8 or (final and length <= 125), "invalid_ws_control_frame")
        yield opcode, final, bytes(value ^ mask[index % 4] for index, value in enumerate(payload))


@dataclass
class Binding:
    node_id: int
    token: str
    port: int
    user_uuid: str
    machine_id: int = 0
    protocol: str = "socks"

    def config(self):
        return {"node_id": self.node_id, "protocol": self.protocol, "listen_ip": "127.0.0.1",
                "server_port": self.port, "network": "tcp", "tls": 0, "routes": [],
                # The unchanged kernels intentionally block private targets
                # by default. Use the audited panel route override solely for
                # this fixture's local echo destination; never alter the core.
                "custom_route_rules": [{"match": {"ip_cidrs": ["127.0.0.1/32"]}, "action": {"type": "direct"}}],
                "base_config": {"pull_interval": 5, "push_interval": 5}}

    def users(self):
        return [{"id": 200000 + self.node_id, "uuid": self.user_uuid, "speed_limit": 0, "device_limit": 0}]


class PanelState:
    def __init__(self, bindings):
        self.bindings = {binding.node_id: binding for binding in bindings}
        self.lock = threading.RLock()
        self.rest = {}
        self.ws = {}
        self.status = {}
        self.pongs = {}
        self.active = {}
        self.sockets = set()
        self.enabled = True

    def authenticated(self, auth):
        try:
            token = auth.get("token")
            machine = int(auth.get("machine_id", 0))
            node = int(auth.get("node_id", 0))
            if machine:
                bindings = [binding for binding in self.bindings.values() if binding.machine_id == machine]
                if node:
                    bindings = [binding for binding in bindings if binding.node_id == node]
                if bindings and all(secrets.compare_digest(binding.token, token or "") for binding in bindings):
                    return "machine:" + str(machine), bindings
            elif node in self.bindings and not self.bindings[node].machine_id:
                binding = self.bindings[node]
                if secrets.compare_digest(binding.token, token or ""):
                    return "node:" + str(node), [binding]
        except (ValueError, TypeError):
            pass
        return None, []

    def record(self, route):
        with self.lock:
            self.rest[route] = self.rest.get(route, 0) + 1

    def close_ws(self):
        with self.lock:
            sockets = list(self.sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass  # Never log request queries or authentication payloads.

    def json_response(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authentication(self, auth):
        identity, bindings = self.server.state.authenticated(auth)
        if not identity:
            self.send_error(403)
        return identity, bindings

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 1024 * 1024:
                self.send_error(400)
                return
            auth = json.loads(self.rfile.read(length))
            identity, bindings = self.authentication(auth)
            if not identity:
                return
            self.server.state.record(self.path)
            if self.path == "/api/v2/server/handshake":
                self.json_response({"websocket": {"enabled": self.server.state.enabled,
                    "ws_url": self.server.base.replace("https://", "wss://").replace("http://", "ws://") + "/ws"},
                    "settings": {"pull_interval": 5, "push_interval": 5}})
            elif self.path == "/api/v2/server/machine/nodes" and identity.startswith("machine:"):
                self.json_response({"nodes": [{"id": binding.node_id, "type": binding.protocol, "name": "synthetic-fixture"} for binding in bindings],
                                    "base_config": {"pull_interval": 5, "push_interval": 5}})
            elif self.path in ("/api/v2/server/report", "/api/v2/server/machine/status", "/api/v1/server/UniProxy/push",
                              "/api/v1/server/UniProxy/alive", "/api/v1/server/UniProxy/status", "/api/v2/flowscope/node-address"):
                self.json_response({"data": True})
            else:
                self.send_error(404)
        except (OSError, ValueError, TypeError):
            self.close_connection = True

    def do_GET(self):
        route = urlsplit(self.path)
        auth = {key: values[0] for key, values in parse_qs(route.query).items()}
        identity, bindings = self.authentication(auth)
        if not identity:
            return
        if route.path == "/ws":
            self.websocket(identity, bindings)
            return
        self.server.state.record(route.path)
        if route.path in ("/api/v1/server/UniProxy/config", "/api/v2/server/config") and len(bindings) == 1:
            self.json_response(bindings[0].config())
        elif route.path in ("/api/v1/server/UniProxy/user", "/api/v2/server/user") and len(bindings) == 1:
            self.json_response({"users": bindings[0].users()})
        else:
            self.send_error(404)

    def websocket(self, identity, bindings):
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(400)
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        import base64
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode())
        self.end_headers()
        state = self.server.state
        connection = self.connection
        connection.settimeout(1)
        with state.lock:
            state.ws[identity] = state.ws.get(identity, 0) + 1
            state.active[identity] = state.active.get(identity, 0) + 1
            state.sockets.add(connection)
        try:
            field, identifier = identity.split(":")
            data = {field + "_id": int(identifier)}
            if field == "machine":
                data["node_ids"] = [binding.node_id for binding in bindings]
            frame(connection, {"event": "auth.success", "data": data})
            for binding in bindings:
                frame(connection, {"event": "sync.config", "data": {"node_id": binding.node_id, "config": binding.config(), "timestamp": int(time.time())}})
                frame(connection, {"event": "sync.users", "data": {"node_id": binding.node_id, "users": binding.users(), "timestamp": int(time.time())}})
            last_ping = 0
            buffer = bytearray()
            fragments = None
            while True:
                if time.monotonic() - last_ping > 2:
                    frame(connection, {"event": "ping", "timestamp": int(time.time())})
                    last_ping = time.monotonic()
                try:
                    received = connection.recv(65536)
                except socket.timeout:
                    continue
                if not received:
                    return
                buffer.extend(received)
                check(len(buffer) <= 1024 * 1024 + 65550, "oversized_ws_receive_buffer")
                for opcode, final, payload in client_frames(buffer):
                    if opcode == 8:
                        raw_frame(connection, 8, payload)
                        return
                    if opcode == 9:
                        raw_frame(connection, 10, payload)
                        continue
                    if opcode == 10:
                        continue
                    if opcode == 1:
                        check(fragments is None, "nested_ws_fragments")
                        fragments = bytearray(payload)
                    elif opcode == 0:
                        check(fragments is not None, "unexpected_ws_continuation")
                        fragments.extend(payload)
                    else:
                        raise FixtureError("unexpected_ws_opcode")
                    check(len(fragments) <= 1024 * 1024, "oversized_ws_message")
                    if final:
                        message = json.loads(fragments)
                        fragments = None
                        if message.get("event") == "node.status":
                            with state.lock:
                                state.status[identity] = state.status.get(identity, 0) + 1
                        elif message.get("event") == "pong":
                            with state.lock:
                                state.pongs[identity] = state.pongs.get(identity, 0) + 1
        except (OSError, EOFError, ValueError, FixtureError):
            pass
        finally:
            with state.lock:
                state.active[identity] -= 1
                state.sockets.discard(connection)
            self.close_connection = True


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    break
                self.request.sendall(data)
        except OSError:
            pass


def socks_echo(binding, echo_port):
    with socket.create_connection(("127.0.0.1", binding.port), timeout=8) as connection:
        connection.sendall(b"\x05\x01\x02")
        check(exact(connection, 2) == b"\x05\x02", "socks_method_failed")
        value = binding.user_uuid.encode()
        connection.sendall(b"\x01" + bytes([len(value)]) + value + bytes([len(value)]) + value)
        check(exact(connection, 2) == b"\x01\x00", "socks_auth_failed")
        connection.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", echo_port))
        reply = exact(connection, 4)
        check(reply[:2] == b"\x05\x00", "socks_connect_failed")
        if reply[3] == 1:
            exact(connection, 6)
        elif reply[3] == 4:
            exact(connection, 18)
        elif reply[3] == 3:
            exact(connection, exact(connection, 1)[0] + 2)
        else:
            raise FixtureError("invalid_socks_address")
        payload = (b"NodeEndpoint isolated kernel echo acceptance\n" * 1024)
        connection.sendall(payload)
        check(exact(connection, len(payload)) == payload, "socks_echo_mismatch")
        return len(payload)


def proxy_echo(binding, echo_port):
    if binding.protocol == "socks":
        return socks_echo(binding, echo_port)
    check(binding.protocol == "vless", "unsupported_fixture_client_protocol")
    # VLESS v0 plaintext TCP: UUID authentication, empty addons, TCP command,
    # destination port, IPv4 address. Send payload before waiting for the
    # response because the core writes its response header with downlink data.
    with socket.create_connection(("127.0.0.1", binding.port), timeout=8) as connection:
        request = (b"\x00" + uuid.UUID(binding.user_uuid).bytes + b"\x00\x01" +
                   struct.pack("!H", echo_port) + b"\x01" + socket.inet_aton("127.0.0.1"))
        payload = b"NodeEndpoint isolated VLESS kernel acceptance\n" * 1024
        connection.sendall(request + payload)
        version, addons_length = exact(connection, 2)
        check(version == 0, "vless_response_version_failed")
        exact(connection, addons_length)
        check(exact(connection, len(payload)) == payload, "vless_echo_mismatch")
        return len(payload)


class Integration:
    def __init__(self, args):
        ne.require_linux_root()
        check(os.uname().machine == "x86_64", "native_amd64_required")
        self.args = args
        self.started_at = int(time.time())
        self.base = ne.safe_path(args.base, existing=False)
        ne.require_root_ancestry(self.base)
        check(not self.base.exists() and self.base != self.base.parent and self.base.parent.is_dir(), "new_private_child_directory_required")
        check(not (self.base.parent / (self.base.name + "-report.json")).exists(), "report_output_already_exists")
        self.base.mkdir(mode=0o700)
        self.marker = secrets.token_hex(16)
        write(self.base / "fixture-owner.json", json.dumps({"marker": self.marker, "schema": 1}))
        self.binary = ne.safe_path(args.binary)
        lock = json.loads(ne.LOCK_PATH.read_text())
        check(ne.digest(self.binary.read_bytes()) == lock["assets"]["xboard-node-linux-amd64"], "uploaded_binary_pin_mismatch")
        check(self.binary.read_bytes()[:6] == b"\x7fELF\x02\x01", "uploaded_binary_not_amd64_elf")
        self.units = {}
        self.servers = []
        self.standard_created = False
        self.checks = []
        self.protected = self.protected_services()
        self.non_loopback = self.non_loopback_address()
        self.bindings = []
        reserved = set()
        for node, machine, token_key in [(11101, 0, "new-node"), (11201, 11200, "new-machine"), (11209, 11200, "new-machine"),
                                         (12101, 0, "old-nodes"), (12109, 0, "old-nodes"), (12201, 12200, "old-machine"), (12209, 12200, "old-machine")]:
            existing = next((binding.token for binding in self.bindings if getattr(binding, "token_key", None) == token_key), None)
            protocol = "socks" if token_key == "new-machine" else "vless"
            binding = Binding(node, existing or secrets.token_hex(24), available_port(reserved), str(uuid.uuid4()), machine, protocol)
            binding.token_key = token_key
            self.bindings.append(binding)
        self.pki()
        self.old_state = PanelState(self.bindings)
        self.old_state.enabled = False
        self.new_state = PanelState(self.bindings)
        self.old_panel = self.panel(self.old_state)
        self.new_panel = self.panel(self.new_state, "server")
        self.echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
        self.echo.daemon_threads = True
        threading.Thread(target=self.echo.serve_forever, daemon=True).start()
        self.servers.append(self.echo)

    def protected_services(self):
        result = {}
        for name in ("docker.service", "flowscope-collector.service"):
            process = command(["systemctl", "show", name, "--property=ActiveState,MainPID,FragmentPath,UnitFileState"], check_exit=False)
            result[name] = process.stdout.decode()
        return result

    def non_loopback_address(self):
        inventory = json.loads(command(["ip", "-j", "address", "show"]).stdout)
        for interface in inventory:
            for address in interface.get("addr_info", []):
                if address.get("family") == "inet" and address.get("scope") == "global":
                    value = ipaddress.ip_address(address["local"])
                    if not value.is_loopback and not value.is_unspecified:
                        return str(value)
        raise FixtureError("non_loopback_interface_required_for_network_negative_check")

    def assert_network_scope(self, service, bindings, kernel):
        pid = int(command(["systemctl", "show", service, "--property=MainPID", "--value"]).stdout)
        check(pid > 0, "live_node_pid_missing")
        for binding in bindings:
            listeners = command(["ss", "-H", "-ltnp", "sport", "=", ":" + str(binding.port)]).stdout.decode()
            check("pid=" + str(pid) + "," in listeners, "proxy_listener_not_owned_by_fixture_node")
            if kernel == "xray":
                check("127.0.0.1:" + str(binding.port) in listeners, "xray_listener_not_loopback")
        if kernel == "singbox":
            deny = command(["systemctl", "show", service, "--property=IPAddressDeny", "--value"]).stdout.decode()
            allow = command(["systemctl", "show", service, "--property=IPAddressAllow", "--value"]).stdout.decode()
            check("0.0.0.0/0" in deny and "::/0" in deny and "127.0.0.0/8" in allow and "::1/128" in allow,
                  "singbox_fixture_network_policy_missing")
            for binding in bindings:
                try:
                    with socket.create_connection((self.non_loopback, binding.port), timeout=2):
                        pass
                except OSError:
                    continue
                raise FixtureError("singbox_non_loopback_connection_unexpectedly_allowed")

    def openssl(self, argv):
        command(["openssl"] + argv, timeout=45)

    def pki(self):
        for name in ("ca", "ca-next"):
            self.openssl(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(self.base / (name + ".key")),
                          "-out", str(self.base / (name + ".pem")), "-days", "3653", "-subj", "/CN=NodeEndpoint isolated " + name,
                          "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"])
        for name, ca in (("server", "ca"), ("server-rekey", "ca"), ("server-next-ca", "ca-next")):
            self.openssl(["req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(self.base / (name + ".key")),
                          "-out", str(self.base / (name + ".csr")), "-subj", "/CN=NodeEndpoint isolated TLS fixture"])
            write(self.base / (name + ".ext"), "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n")
            self.openssl(["x509", "-req", "-in", str(self.base / (name + ".csr")), "-CA", str(self.base / (ca + ".pem")),
                          "-CAkey", str(self.base / (ca + ".key")), "-CAcreateserial", "-out", str(self.base / (name + ".pem")),
                          "-days", "3652", "-extfile", str(self.base / (name + ".ext"))])
        for path in self.base.glob("*"):
            if path.is_file():
                path.chmod(0o600)
        self.ca = (self.base / "ca.pem").read_bytes()
        self.ca_pin = ne.digest(ssl.PEM_cert_to_DER_cert(self.ca.decode()))

    def panel(self, state, certificate=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler)
        server.daemon_threads = True
        server.state = state
        if certificate:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.base / (certificate + ".pem"), self.base / (certificate + ".key"))
            server.socket = context.wrap_socket(server.socket, server_side=True)
            server.context = context
        server.base = ("https" if certificate else "http") + "://127.0.0.1:" + str(server.server_port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server

    def record(self, name, passed=True, **details):
        self.checks.append(dict(name=name, passed=bool(passed), **details))
        print(json.dumps({"check": name, "passed": bool(passed)}), flush=True)

    def add_unit(self, service, expected):
        check(service not in self.units, "duplicate_fixture_unit")
        self.units[service] = ne.digest(expected)

    def wait_node(self, service, bindings, state, ws_identities=(), timeout=35):
        start = time.monotonic()
        last_error = "runtime_not_ready"
        while time.monotonic() - start < timeout:
            try:
                check(command(["systemctl", "is-active", service], check_exit=False).stdout.strip() == b"active", "systemd_not_active")
                for binding in bindings:
                    proxy_echo(binding, self.echo.server_address[1])
                with state.lock:
                    # The audited machine orchestrator has onPing=nil and its
                    # virtual PushClient sends device events, not node.status.
                    # Its pong proves a live Go reader; actual proxy traffic
                    # proves every discovered child kernel started correctly.
                    check(all(state.pongs.get(identity, 0) > 0 and state.active.get(identity, 0) == 1 and
                              (identity.startswith("machine:") or state.status.get(identity, 0) > 0)
                              for identity in ws_identities), "live_go_wss_event_missing")
                return
            except (OSError, EOFError, FixtureError) as error:
                last_error = str(error) if isinstance(error, FixtureError) else "runtime_socket_not_ready:" + type(error).__name__
            time.sleep(1)
        raise FixtureError(last_error)

    def stopped_ws(self, state, identities, timeout=10):
        """Wait until stop/probe sockets really close before reusing an identity."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            with state.lock:
                if all(state.active.get(identity, 0) == 0 for identity in identities):
                    for identity in identities:
                        state.status[identity] = 0
                        state.pongs[identity] = 0
                    return
            time.sleep(0.1)
        raise FixtureError("stopped_identity_ws_still_connected")

    def prepare_local(self, label, mode, kernel, selected):
        directory = self.base / label
        token_file = self.base / (label + "-token.txt")
        write(token_file, selected[0].token + "\n")
        args = SimpleNamespace(output_dir=str(directory), panel_url=self.new_panel.base, mode=mode, kernel=kernel, architecture="amd64",
                               node_id=selected[0].node_id if mode == "node" else None,
                               machine_id=selected[0].machine_id if mode == "machine" else None,
                               node_type=selected[0].protocol, download=False, token_file=str(token_file), token_stdin=False,
                               ca_file=str(self.base / "ca.pem"), ca_fingerprint=self.ca_pin, allow_http=False,
                               allow_rest_fallback=False, confirm_new_identity="UNUSED")
        prepared = ne.prepare(args)
        # No network downloader substitution: explicitly stage the independently
        # pinned uploaded asset into a real production-generated configuration.
        write(directory / "xboard-node", self.binary.read_bytes(), 0o700)
        prepared["binary_downloaded"] = True
        prepared["files"]["xboard-node"] = ne.digest((directory / "xboard-node").read_bytes())
        prepared["fixture_staging"] = "uploaded release asset independently verified; network download not exercised here"
        service = prepared["service"]
        if kernel == "singbox":
            # This locked source ignores listen_ip and binds ::. Constrain
            # only our random fixture service with native systemd cgroup BPF;
            # production-generated unit/data and the binary remain auditable.
            # Runtime negative checks below fail if the host cannot enforce it.
            unit = (directory / service).read_text().replace("\n[Service]\n", "\n[Service]\nIPAddressDeny=any\nIPAddressAllow=localhost\n", 1)
            write(directory / service, unit)
            prepared["files"][service] = ne.digest((directory / service).read_bytes())
            prepared["fixture_network"] = "own Singbox unit adds IPAddressDeny=any and IPAddressAllow=localhost"
        write(directory / "prepared.json", json.dumps(prepared, indent=2))
        self.add_unit(service, (directory / service).read_bytes())
        return directory, service

    def new_install(self, label, mode, kernel, selected):
        directory, service = self.prepare_local(label, mode, kernel, selected)
        result = ne.install_new(SimpleNamespace(prepared_dir=str(directory), confirm_install="NEW", enable=False, allow_rest_fallback=False))
        identities = ["machine:" + str(selected[0].machine_id)] if mode == "machine" else ["node:" + str(selected[0].node_id)]
        self.wait_node(service, selected, self.new_state, identities)
        self.assert_network_scope(service, selected, kernel)
        self.record(label + ":real-systemd-kernel-rest-wss-proxy", proxy_bytes=sum(proxy_echo(binding, self.echo.server_address[1]) for binding in selected),
                    mode=mode, kernel=kernel, node_ids=[binding.node_id for binding in selected],
                    proxy_protocols=[binding.protocol for binding in selected],
                    wss_runtime_evidence="authenticated live application pong" if mode == "machine" else "authenticated live application pong and node.status")
        if kernel == "singbox":
            self.record(label + ":non-loopback-network-refused-real-connect", passed=True)
        command(["systemctl", "stop", service])
        self.stopped_ws(self.new_state, identities)
        row = ne.describe((directory / "config.yml").read_text())[1][0]
        auth = ne.instance_auth(row, ne.credentials(directory / "credentials.env"))
        self.record(label + ":stopped-authenticated-wss-probe", passed=ne.preflight(self.new_panel.base, auth, self.ca)["websocket"] == "passed")
        self.stopped_ws(self.new_state, identities)
        if mode == "node":
            trust_before = ne.digest((directory / "trust.pem").read_bytes())
            self.new_panel.context.load_cert_chain(self.base / "server-rekey.pem", self.base / "server-rekey.key")
            command(["systemctl", "start", service])
            self.wait_node(service, selected, self.new_state, identities)
            self.assert_network_scope(service, selected, kernel)
            self.record(label + ":same-ca-server-rekey-no-trust-change", passed=ne.digest((directory / "trust.pem").read_bytes()) == trust_before)
            command(["systemctl", "stop", service])
            self.stopped_ws(self.new_state, identities)
            self.new_panel.context.load_cert_chain(self.base / "server.pem", self.base / "server.key")
        else:
            next_ca = (self.base / "ca-next.pem").read_bytes()
            next_pin = ne.digest(ssl.PEM_cert_to_DER_cert(next_ca.decode()))
            write(self.base / "dual-ca.pem", self.ca + next_ca)
            verified = ne.verify_ca(self.base / "dual-ca.pem", self.ca_pin + "," + next_pin)
            write(directory / "trust.pem", ne.trust_bundle(verified))
            self.new_panel.context.load_cert_chain(self.base / "server-next-ca.pem", self.base / "server-next-ca.key")
            command(["systemctl", "start", service])
            self.wait_node(service, selected, self.new_state, identities)
            self.assert_network_scope(service, selected, kernel)
            self.record(label + ":dual-ca-new-chain-real-go", passed=True)
            command(["systemctl", "stop", service])
            self.stopped_ws(self.new_state, identities)
            self.new_panel.context.load_cert_chain(self.base / "server.pem", self.base / "server.key")
        self.capture_log(service, label)

    def standard_migration(self):
        root = Path("/etc/xboard-node")
        binary = Path("/usr/local/bin/xboard-node")
        unit = Path("/etc/systemd/system/xboard-node.service")
        dropin = Path("/etc/systemd/system/xboard-node.service.d")
        check(not any(path.exists() or path.is_symlink() for path in (root, binary, unit, dropin)), "standard_layout_already_exists_refused")
        check(command(["systemctl", "show", "xboard-node.service", "--property=LoadState", "--value"], check_exit=False).stdout.strip() == b"not-found", "standard_unit_already_loaded_refused")
        root.mkdir(mode=0o700)
        self.standard_created = True
        write(root / ".fixture-owner.json", json.dumps({"marker": self.marker}))
        write(binary, self.binary.read_bytes(), 0o700)
        nodes = [binding for binding in self.bindings if binding.token_key == "old-nodes"]
        machines = [binding for binding in self.bindings if binding.token_key == "old-machine"]
        text = ("log:\n  level: info\nhealth_port: 0\ninstances:\n  - panel:\n      url: " + json.dumps(self.old_panel.base) +
                "\n      token_env: FIXTURE_NODES\n    kernel:\n      type: xray\n      config_dir: /etc/xboard-node/preserved-nodes\n    nodes:\n" +
                "".join("      - node_id: " + str(binding.node_id) + "\n        node_type: " + binding.protocol + "\n" for binding in nodes) +
                "  - panel:\n      url: " + json.dumps(self.old_panel.base) + "\n    machine:\n      machine_id: 12200\n      token_env: FIXTURE_MACHINE\n" +
                "    kernel:\n      type: xray\n      config_dir: /etc/xboard-node/preserved-machine\n")
        write(root / "config.yml", text)
        write(root / "credentials.env", "FIXTURE_NODES=" + nodes[0].token + "\nFIXTURE_MACHINE=" + machines[0].token + "\n")
        rows = ne.describe(text)[1]
        metadata = {"version": "v1.13-orphan.4", "config_mode": "instances", "instance_count": len(rows),
                    "latest_instance_id": rows[0]["id"], "fixture_preserve": self.marker,
                    "instances": [{"id": row["id"], "panel_url": row["panel_url"], "mode": row["mode"], "fixture_metadata": "preserve"} for row in rows]}
        write(root / "install-meta.json", json.dumps(metadata), 0o644)
        for name in ("preserved-nodes", "preserved-machine"):
            (root / name).mkdir(mode=0o700)
            write(root / name / "sentinel.txt", self.marker)
        service_text = """[Unit]
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
        write(unit, service_text, 0o644)
        self.add_unit("xboard-node.service", service_text.encode())
        command(["systemctl", "daemon-reload"])
        command(["systemctl", "enable", "xboard-node.service"])
        command(["systemctl", "start", "xboard-node.service"])
        self.wait_node("xboard-node.service", nodes + machines, self.old_state)
        self.assert_network_scope("xboard-node.service", nodes + machines, "xray")
        credentials_before = ne.digest((root / "credentials.env").read_bytes())
        original_binary = ne.digest(binary.read_bytes())
        args = SimpleNamespace(config=str(root / "config.yml"), credentials=str(root / "credentials.env"),
                               meta=str(root / "install-meta.json"), service="xboard-node.service", instance_id=rows[0]["id"],
                               from_url=self.old_panel.base, panel_url=self.new_panel.base, ca_file=str(self.base / "ca.pem"),
                               ca_fingerprint=self.ca_pin, confirm_migrate="MIGRATE:" + rows[0]["id"], allow_http=False, allow_rest_fallback=False)
        result = ne.perform_migration(args)
        self.wait_node("xboard-node.service", nodes + machines, self.new_state, ["node:" + str(binding.node_id) for binding in nodes])
        self.assert_network_scope("xboard-node.service", nodes + machines, "xray")
        current = (root / "config.yml").read_text()
        changed = ne.describe(current)[1]
        check(changed[1]["panel_url"] == self.old_panel.base, "other_instance_url_changed")
        check(current == text.replace(json.dumps(self.old_panel.base), json.dumps(self.new_panel.base), 1), "non_target_yaml_changed")
        check(ne.digest((root / "credentials.env").read_bytes()) == credentials_before and ne.digest(binary.read_bytes()) == original_binary, "credentials_or_binary_changed")
        check(command(["systemctl", "is-enabled", "xboard-node.service"]).stdout.strip() == b"enabled", "enablement_changed")
        for name in ("preserved-nodes", "preserved-machine"):
            check((root / name / "sentinel.txt").read_text() == self.marker, "instance_data_sentinel_changed")
        self.record("standard-multibinding-selected-migration-real-go", passed=True)
        snapshot = Path(result["backup"])
        state = json.loads((snapshot / "state.json").read_text())
        for record in state["files"]:
            path = Path(record["path"])
            check((ne.digest(path.read_bytes()) if path.exists() else None) == record["applied_sha256"], "post_migration_digest_changed")
        # Exercise the actual CLI rollback, including its schema/allowlist,
        # ownership, provenance, applied-hash and full backup-hash checks.
        command([sys.executable, str(REPOSITORY / "tools/node/node_endpoint.py"), "rollback", "--backup", str(snapshot), "--confirm-rollback", "ROLLBACK"])
        self.wait_node("xboard-node.service", nodes + machines, self.old_state)
        self.assert_network_scope("xboard-node.service", nodes + machines, "xray")
        check((root / "config.yml").read_text() == text and json.loads((root / "install-meta.json").read_text()) == metadata, "rollback_bytes_not_restored")
        self.record("standard-migration-explicit-rollback-real-systemd", passed=True)
        self.capture_log("xboard-node.service", "standard-migration")

    def capture_log(self, service, label):
        process = command(["journalctl", "--unit", service, "--since", "@" + str(self.started_at), "--no-pager", "--output=cat"], check_exit=False)
        write(self.base / (label + "-private-journal.log"), process.stdout)

    def cleanup(self):
        failures = []
        for service, expected in reversed(list(self.units.items())):
            path = Path("/etc/systemd/system") / service
            try:
                if path.exists():
                    ne.safe_path(path)
                    check(ne.digest(path.read_bytes()) == expected, "owned_unit_changed_cleanup_refused")
                    fragment = command(["systemctl", "show", service, "--property=FragmentPath", "--value"], check_exit=False).stdout.decode().strip()
                    check(fragment in ("", str(path)), "unit_fragment_changed_cleanup_refused")
                    command(["systemctl", "stop", service])
                    properties = command(["systemctl", "show", service, "--property=ActiveState,MainPID"]).stdout.decode().splitlines()
                    check("ActiveState=inactive" in properties and "MainPID=0" in properties, "unit_stop_not_confirmed_cleanup_refused")
                    command(["systemctl", "disable", service])
                    path.unlink()
                else:
                    properties = command(["systemctl", "show", service, "--property=ActiveState,MainPID"], check_exit=False).stdout.decode().splitlines()
                    check("ActiveState=active" not in properties and "MainPID=0" in properties,
                          "owned_unit_missing_while_running_cleanup_refused")
            except (OSError, FixtureError, subprocess.SubprocessError):
                failures.append("unit_cleanup_failed:" + service)
        if self.standard_created and not any("xboard-node.service" in failure for failure in failures):
            try:
                root = Path("/etc/xboard-node")
                check(json.loads((root / ".fixture-owner.json").read_text())["marker"] == self.marker, "standard_directory_marker_changed")
                binary = Path("/usr/local/bin/xboard-node")
                if binary.exists():
                    check(ne.digest(binary.read_bytes()) == ne.digest(self.binary.read_bytes()), "standard_binary_changed")
                dropin = Path("/etc/systemd/system/xboard-node.service.d")
                if dropin.exists():
                    owned = dropin / "90-node-endpoint.conf"
                    if owned.exists():
                        check(owned.read_text().startswith("# NodeEndpoint 1.0.0 owned trust setting\n"), "trust_dropin_changed")
                        owned.unlink()
                    check(not list(dropin.iterdir()), "foreign_dropin_cleanup_refused")
                    dropin.rmdir()
                shutil.rmtree(root)
                if binary.exists():
                    binary.unlink()
            except (OSError, ValueError, FixtureError):
                failures.append("standard_layout_cleanup_failed")
        try:
            command(["systemctl", "daemon-reload"])
        except (OSError, FixtureError, subprocess.SubprocessError):
            failures.append("daemon_reload_cleanup_failed")
        self.new_state.close_ws()
        self.old_state.close_ws()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        if self.protected_services() != self.protected:
            failures.append("protected_service_inventory_changed")
        return failures

    def run(self):
        failure = None
        try:
            self.record("uploaded-native-amd64-release-pin", passed=True)
            self.new_install("node-xray", "node", "xray", [binding for binding in self.bindings if binding.token_key == "new-node"])
            self.new_install("machine-singbox", "machine", "singbox", [binding for binding in self.bindings if binding.token_key == "new-machine"])
            if self.args.include_standard_migration:
                self.standard_migration()
        except (OSError, EOFError, ValueError, FixtureError, ne.SafetyError, subprocess.SubprocessError) as error:
            failure = str(error) if isinstance(error, (FixtureError, ne.SafetyError)) else "runtime_fixture_failure"
            for service in self.units:
                try:
                    self.capture_log(service, "failure-" + service)
                except (OSError, FixtureError, subprocess.SubprocessError):
                    pass
        finally:
            cleanup_failures = self.cleanup()
        self.record("fixture-resources-cleaned-and-protected-services-unchanged", passed=not cleanup_failures)
        failed_checks = [row["name"] for row in self.checks if not row["passed"]]
        report = {"schema": 1, "passed": failure is None and not cleanup_failures and not failed_checks, "checks": self.checks,
                  "failure": failure, "cleanup_failures": cleanup_failures,
                  "failed_checks": failed_checks,
                  "scope": "real native AMD64 pinned Go binary, systemd, TLS REST/WSS, both unchanged embedded kernels, synthetic VLESS/SOCKS5 loopback traffic",
                  "preparation": "production prepare without network download, then explicitly stage uploaded SHA-256-pinned release asset; own Singbox unit gains a tested localhost-only systemd IP filter; real install-new",
                  "network_boundary": "panel/echo/Xray bind loopback; locked Singbox binds :: but our random service cgroup permits localhost only, verified by real rejected non-loopback connections; standard fixture starts both instances as Xray with no kernel changes; no latest/geodata URLs are used; Xray may perform its optional credential-free public-IP observation",
                  "not_tested": ["real Xboard database/controller/worker integration", "real subscription/client protocol deployments",
                                 "native ARM64 execution", "public network and production continuity", "CRL/OCSP revocation"]}
        report_path = self.base.parent / (self.base.name + "-report.json")
        check(not report_path.exists(), "report_output_already_exists")
        write(report_path, json.dumps(report, indent=2))
        if report["passed"] and not self.args.keep_private_evidence:
            check(json.loads((self.base / "fixture-owner.json").read_text())["marker"] == self.marker, "private_directory_marker_changed")
            shutil.rmtree(self.base)
        else:
            write(self.base / "RECOVERY_EVIDENCE", "Private fixture evidence retained. Review report and cleanup failures before manual removal.\n")
        print(json.dumps({"passed": report["passed"], "report": str(report_path), "private_evidence_retained": self.base.exists()}), flush=True)
        return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="new private Root-owned child under an existing Root-owned directory, e.g. /opt/node-endpoint-lab-20261010/node-run-001")
    parser.add_argument("--binary", required=True, help="uploaded pinned xboard-node-linux-amd64 binary; never an arbitrary build")
    parser.add_argument("--include-standard-migration", action="store_true", help="create standard layout ONLY if every relevant path/unit is absent")
    parser.add_argument("--keep-private-evidence", action="store_true")
    args = parser.parse_args()
    try:
        return Integration(args).run()
    except (OSError, ValueError, FixtureError, ne.SafetyError, subprocess.SubprocessError):
        # Never print request URLs, command stderr, fixture credentials or UUIDs.
        print(json.dumps({"passed": False, "failure": "fixture_initialization_or_finalization_failed", "private_base": args.base}), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
