"""Isolated tool tests. Real TLS sockets, plus explicitly simulated systemd.

These are not production, Linux-systemd, Go runtime or traffic acceptance tests.
"""
import argparse
import base64
from contextlib import nullcontext, ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "node"))
import node_endpoint as ne
from strict_yaml import parse, LayoutError

OPENSSL = shutil.which("openssl")
if not OPENSSL and os.name == "nt":
    candidate = Path("C:/Program Files/Git/mingw64/bin/openssl.exe")
    if candidate.exists():
        OPENSSL = str(candidate)

MULTI = '''# production-like fixture; all values are fictitious
log:
  level: info
health_port: 0
instances:
  - panel:
      url: "https://old.example:443" # selected URL only
      node_id: 3
      token_env: INSTANCE_A
    kernel:
      type: xray
      config_dir: /etc/xboard-node/instances/preserved-a
    nodes:
      - node_id: 3
        node_type: vmess
      - node_id: 9
        node_type: trojan
  - panel:
      url: 'https://other.example:443'
    machine:
      machine_id: 12
      token_env: INSTANCE_B
    kernel:
      type: singbox
      config_dir: /etc/xboard-node/instances/preserved-b
'''


class LayoutTests(unittest.TestCase):
    def test_multibinding_byte_preservation(self):
        original, rows = ne.describe(MULTI)
        updated, selected, new_id = ne.migrate_text(MULTI, rows[0]["id"], "https://old.example:443", "https://[2001:db8::1]:8443")
        self.assertEqual(updated, MULTI.replace('"https://old.example:443"', '"https://[2001:db8::1]:8443"'))
        self.assertEqual(parse(updated).plain()["instances"][0]["nodes"], original.plain()["instances"][0]["nodes"])
        self.assertEqual(parse(updated).plain()["instances"][1], original.plain()["instances"][1])
        self.assertNotEqual(new_id, rows[0]["id"])
        self.assertEqual(selected["config_dir"], "/etc/xboard-node/instances/preserved-a")

    def test_machine_credentials_are_preserved(self):
        _, rows = ne.describe(MULTI)
        updated, _, _ = ne.migrate_text(MULTI, rows[1]["id"], "https://other.example:443", "https://192.0.2.2:8443")
        self.assertIn("machine_id: 12\n      token_env: INSTANCE_B", updated)
        self.assertEqual(ne.instance_auth(rows[1], {"INSTANCE_B": "fake-token"}), {"machine_id": 12, "token": "fake-token"})

    def test_all_node_bindings_are_checked(self):
        _, rows = ne.describe(MULTI)
        self.assertEqual([row["node_id"] for row in ne.auth_bindings(rows[0], {"INSTANCE_A": "fake"})], [3, 9])

    def test_unknown_yaml_is_rejected(self):
        invalid = ["panel: &p\n  url: https://old.example\n", "panel:\n  url: *p\n", "panel:\n  url: |\n    https://old.example\n",
                   "panel:\n  url: https://one\n  url: https://two\n", "panel: {url: https://old.example}\n", "---\npanel: {}\n",
                   "panel:\n\turl: https://old.example\n", "panel:\n  url: !!str https://old.example\n"]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(LayoutError):
                parse(source)

    def test_implicit_multi_instance_data_directory_is_rejected(self):
        source = MULTI.replace("      config_dir: /etc/xboard-node/instances/preserved-a\n", "")
        _, rows = ne.describe(source)
        with self.assertRaises(ne.SafetyError):
            ne.migrate_text(source, rows[0]["id"], rows[0]["panel_url"], "https://192.0.2.2:8443")

    def test_identity_and_original_url_must_match(self):
        _, rows = ne.describe(MULTI)
        for item, url in [("wrong-id", rows[0]["panel_url"]), (rows[0]["id"], "https://wrong.example")]:
            with self.assertRaises(ne.SafetyError):
                ne.migrate_text(MULTI, item, url, "https://192.0.2.2:8443")

    def test_endpoint_validation(self):
        self.assertEqual(ne.canonical_url("https://[2001:db8::1]:8443/"), "https://[2001:db8::1]:8443")
        self.assertEqual(ne.canonical_url("https://EXAMPLE.com:8443"), "https://example.com:8443")
        for value in ["http://example.com", "https://user:password@example.com", "https://example.com/path", "https://example.com:0",
                      "https://bad_name.test", "https://example.com?token=fake", "https://example.com\\evil"]:
            with self.subTest(value=value), self.assertRaises(ne.SafetyError):
                ne.canonical_url(value)
        self.assertEqual(ne.canonical_url("http://192.0.2.1:8080", True), "http://192.0.2.1:8080")

    def test_protected_file_credentials_and_no_argument_token(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.env"
            path.write_text("INSTANCE_A=not-real\nINSTANCE_B='also-fake'\n")
            os.chmod(path, 0o600)
            self.assertEqual(ne.credentials(path)["INSTANCE_A"], "not-real")
            path.write_text("INSTANCE_A=fake\nINSTANCE_A=other\n")
            with self.assertRaises(ne.SafetyError):
                ne.credentials(path)
        source = (ROOT / "tools/node/node_endpoint.py").read_text()
        self.assertNotIn('add_argument("--token")', source)

    def test_release_is_exactly_pinned(self):
        lock = json.loads(ne.LOCK_PATH.read_text())
        self.assertEqual(lock["version"], "v1.13-orphan.4")
        self.assertFalse(lock["release_is_immutable"])
        self.assertEqual(len(lock["assets"]["xboard-node-linux-arm64"]), 64)

    def test_credentials_reject_every_go_business_and_trust_override(self):
        keys = ["API_HOST", "API_KEY", "NODE_ID", "NODE_TYPE", "KERNEL_TYPE", "CERT_FILE", "KEY_FILE", "DOMAIN", "LOG_LEVEL",
                "MACHINE_ID", "MACHINE_TOKEN", "SSL_CERT_FILE", "SSL_CERT_DIR", "apiHost", "apiKey", "nodeID", "nodeType", "kernel", "certFile", "keyFile", "domain", "logLevel"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.env"
            for key in keys:
                path.write_text("INSTANCE_A=fake\n" + key + "=override\n")
                os.chmod(path, 0o600)
                with self.subTest(key=key), self.assertRaises(ne.SafetyError):
                    ne.credentials(path)

    def test_inspect_bindings_never_expose_nested_certificate_credentials(self):
        source = MULTI.replace("        node_type: vmess\n", "        node_type: vmess\n        cert:\n          dns_env:\n            CF_API_TOKEN: SECRET_CF_SENTINEL\n          key_content: 'SECRET_PRIVATE_KEY_SENTINEL'\n")
        _, rows = ne.describe(source)
        public = [{key: value for key, value in row.items() if key != "node"} for row in rows]
        self.assertNotIn("SECRET_CF_SENTINEL", json.dumps(public))
        self.assertNotIn("SECRET_PRIVATE_KEY_SENTINEL", json.dumps(public))
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.yml"
            config.write_text(source)
            result = subprocess.run([sys.executable, str(ROOT / "tools/node/node_endpoint.py"), "inspect", "--config", str(config)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn(b"SECRET_", result.stdout + result.stderr)

    def test_auto_identity_matches_go_raw_host_algorithm(self):
        raw = "https://[2001:0DB8:0:0:0:0:0:1]:8443/"
        value = {"panel": {"url": raw, "node_id": 3}}
        key = "https://[2001:0db8:0:0:0:0:0:1]:8443|node|3"
        expected = "2001-0db8-0-0-0-0-0-1-8443-node-3-" + hashlib.sha1(key.encode()).hexdigest()[:6]
        self.assertEqual(ne.identity(value), expected)

    def test_ws_authority_matches_ip_semantics_and_effective_port(self):
        ne.validate_ws_url("wss://[::ffff:192.0.2.1]:8443/ws", "https://[::ffff:c000:201]:8443")
        ne.validate_ws_url("wss://example.com:443/ws", "https://EXAMPLE.com")
        for bad in ["wss://[::ffff:192.0.2.2]:8443/ws", "wss://user:secret@[::ffff:192.0.2.1]:8443/ws",
                    "wss://[::ffff:192.0.2.1]:8443/ws?token=fake", "wss://[::ffff:192.0.2.1]:8443/ws#fragment"]:
            with self.subTest(url=bad), self.assertRaises(ne.SafetyError):
                ne.validate_ws_url(bad, "https://[::ffff:c000:201]:8443")

    def test_corrupt_binary_download_never_creates_executable(self):
        class CorruptResponse(io.BytesIO):
            def geturl(self):
                return "https://release-assets.githubusercontent.com/fake"
        with tempfile.TemporaryDirectory() as folder, patch.object(ne.urllib.request, "urlopen", return_value=CorruptResponse(b"not a verified ELF")):
            with self.assertRaises(ne.SafetyError):
                ne.download_binary("amd64", folder)
            self.assertFalse((Path(folder) / "xboard-node").exists())


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, value):
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if payload.get("token") != "fake-secret":
            self.send_error(403)
            return
        if self.path.endswith("handshake"):
            advertised = self.server.base.replace("https://", "wss://") + "/ws"
            if self.server.old_ws:
                advertised = "wss://old.invalid:8443/ws"
            self.send_json({"websocket": {"enabled": self.server.ws_enabled, "ws_url": advertised}, "settings": {}})
        elif self.path.endswith("machine/nodes") and payload.get("machine_id") == 12:
            self.send_json({"nodes": [{"id": 3, "type": "vmess"}, {"id": 9, "type": "trojan"}], "base_config": {}})
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlsplit(self.path)
        auth = parse_qs(parsed.query)
        if auth.get("token") != ["fake-secret"]:
            self.send_error(403)
            return
        if parsed.path == "/ws":
            self.server.ws_auth_count += 1
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", "wrong" if self.server.bad_upgrade else accept)
            self.end_headers()
            field = "machine_id" if "machine_id" in auth else "node_id"
            payload = json.dumps({"event": "auth.success" if not self.server.bad_auth else "error",
                                  "data": {field: 999 if self.server.wrong_identity else int(auth[field][0])}}).encode()
            self.wfile.write(bytes([0x81, len(payload)]) + payload)
            self.wfile.flush()
        elif parsed.path.endswith("/config"):
            node_id = int(auth.get("node_id", [0])[0])
            self.send_json(self.server.node_configs.get(node_id, {"protocol": "vmess", "server_port": 10000}))
        elif parsed.path.endswith("/user"):
            self.send_json({"users": []})
        else:
            self.send_error(404)


@unittest.skipUnless(OPENSSL, "openssl unavailable: real TLS tests NOT TESTED")
class RealTLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name)
        cls.create_ca("ca")
        cls.create_ca("wrong")
        cls.create_leaf("valid", "IP:127.0.0.1", 10)
        cls.create_leaf("valid-new-key", "IP:127.0.0.1", 10)
        cls.create_leaf("new-ca", "IP:127.0.0.1", 10, "wrong")
        cls.create_leaf("wrong-san", "DNS:wrong.example", 10)
        cls.create_leaf("expired", "IP:127.0.0.1", -1)
        cls.ca = (cls.path / "ca.pem").read_bytes()
        cls.fingerprint = ne.digest(ssl.PEM_cert_to_DER_cert(cls.ca.decode()))
        cls.auth = {"node_id": 3, "token": "fake-secret", "node_type": "vmess"}

    @classmethod
    def openssl(cls, args):
        subprocess.run([OPENSSL] + args, cwd=cls.path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def create_ca(cls, name):
        cls.openssl(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", name + ".key", "-out", name + ".pem", "-days", "3653",
                     "-subj", "/CN=NodeEndpoint Test " + name, "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"])

    @classmethod
    def create_leaf(cls, name, san, days, ca_name="ca"):
        cls.openssl(["req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", name + ".key", "-out", name + ".csr", "-subj", "/CN=NodeEndpoint TLS Fixture"])
        (cls.path / (name + ".ext")).write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=" + san)
        if days < 0:
            (cls.path / "index.txt").write_text("")
            (cls.path / "serial.txt").write_text("01\n")
            (cls.path / "ca.cnf").write_text("[ca]\ndefault_ca=test\n[test]\ndatabase=index.txt\nserial=serial.txt\nnew_certs_dir=.\ncertificate=ca.pem\nprivate_key=ca.key\ndefault_md=sha256\npolicy=policy\n[policy]\ncommonName=supplied\n[server]\n" + (cls.path / (name + ".ext")).read_text())
            cls.openssl(["ca", "-batch", "-config", "ca.cnf", "-in", name + ".csr", "-out", name + ".pem", "-startdate", "20000101000000Z", "-enddate", "20010101000000Z", "-extensions", "server"])
            cls.openssl(["x509", "-in", name + ".pem", "-out", name + ".pem"])
        else:
            cls.openssl(["x509", "-req", "-in", name + ".csr", "-CA", ca_name + ".pem", "-CAkey", ca_name + ".key", "-CAcreateserial", "-out", name + ".pem", "-days", str(days), "-extfile", name + ".ext"])

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def server(self, certificate="valid"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.path / (certificate + ".pem"), self.path / (certificate + ".key"))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.base = "https://127.0.0.1:" + str(server.server_port)
        server.old_ws = server.bad_upgrade = server.bad_auth = server.wrong_identity = False
        server.ws_enabled = True
        server.node_configs = {}
        server.ws_auth_count = 0
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_real_rest_and_authenticated_wss_by_ip(self):
        server = self.server()
        self.assertEqual(ne.preflight(server.base, self.auth, self.ca), {"rest": "passed", "websocket": "passed", "tls": "verified"})

    def test_real_machine_mode_multibinding_rest_and_wss(self):
        server = self.server()
        result = ne.preflight(server.base, {"token": "fake-secret", "machine_id": 12}, self.ca)
        self.assertEqual(result["websocket"], "passed")

    def test_prepare_rejects_incompatible_rest_protocol_before_wss_or_files(self):
        server = self.server()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            token = root / "token.txt"
            token.write_text("fake-secret")
            os.chmod(token, 0o600)
            for index, (mode, kernel, configs) in enumerate([
                ("node", "xray", {3: {"protocol": "tuic"}}),
                ("node", "xray", {3: {"protocol": "hysteria", "version": 1}}),
                ("machine", "xray", {3: {"protocol": "vmess"}, 9: {"protocol": "anytls"}}),
                ("node", "singbox", {3: {"protocol": "hysteria2", "version": 2}}),
            ]):
                server.node_configs = configs
                output = root / ("rejected-" + str(index))
                args = argparse.Namespace(output_dir=str(output), panel_url=server.base, mode=mode, kernel=kernel,
                                          architecture="amd64", node_id=3, machine_id=12, node_type="vmess", download=False,
                                          token_file=str(token), token_stdin=False, ca_file=str(self.path / "ca.pem"),
                                          ca_fingerprint=self.fingerprint, allow_http=False, allow_rest_fallback=False,
                                          confirm_new_identity="UNUSED")
                with patch.object(ne, "verify_ca", return_value=self.ca), self.assertRaises(ne.SafetyError):
                    ne.prepare(args)
                self.assertFalse(output.exists())
                self.assertEqual(server.ws_auth_count, 0)
            server.node_configs = {3: {"protocol": "hysteria", "version": 2}}
            self.assertEqual(ne.preflight(server.base, self.auth, self.ca, kernel="xray")["websocket"], "passed")
            server.node_configs = {3: {"protocol": "socks"}, 9: {"protocol": "anytls"}}
            self.assertEqual(ne.preflight(server.base, {"token": "fake-secret", "machine_id": 12}, self.ca, kernel="singbox")["websocket"], "passed")

    def test_wrong_ca_expiry_and_wrong_san_are_rejected(self):
        cases = [("valid", (self.path / "wrong.pem").read_bytes()), ("wrong-san", self.ca), ("expired", self.ca)]
        for certificate, ca in cases:
            with self.subTest(certificate=certificate), self.assertRaises(ne.SafetyError):
                ne.preflight(self.server(certificate).base, self.auth, ca)

    def test_same_ca_reissued_server_key_needs_no_reimport(self):
        for name in ("valid", "valid-new-key"):
            self.assertEqual(ne.preflight(self.server(name).base, self.auth, self.ca)["tls"], "verified")

    def test_old_advertised_ws_url_is_fatal_even_with_fallback(self):
        server = self.server()
        server.old_ws = True
        with self.assertRaises(ne.SafetyError):
            ne.preflight(server.base, self.auth, self.ca, True)

    def test_wss_auth_proof_and_rest_fallback_are_separate(self):
        server = self.server()
        server.bad_upgrade = True
        with self.assertRaises(ne.SafetyError):
            ne.preflight(server.base, self.auth, self.ca)
        self.assertIn("failed", ne.preflight(server.base, self.auth, self.ca, True)["websocket"])
        server.bad_upgrade = False
        server.bad_auth = True
        with self.assertRaises(ne.SafetyError):
            ne.preflight(server.base, self.auth, self.ca)

    def test_disabled_wss_is_explicit(self):
        server = self.server()
        server.ws_enabled = False
        self.assertEqual(ne.preflight(server.base, self.auth, self.ca)["websocket"], "disabled")

    def test_ca_fingerprint_and_ca_properties_are_real(self):
        original_run = ne.run
        def openssl_run(args, check=True):
            return original_run([OPENSSL] + args[1:], check)
        with patch.object(ne, "run", side_effect=openssl_run):
            self.assertEqual(ssl.PEM_cert_to_DER_cert(ne.verify_ca(self.path / "ca.pem", self.fingerprint).decode()), ssl.PEM_cert_to_DER_cert(self.ca.decode()))
            with self.assertRaises(ne.SafetyError):
                ne.verify_ca(self.path / "ca.pem", "0" * 64)
            fingerprint = ne.digest(ssl.PEM_cert_to_DER_cert((self.path / "valid.pem").read_text()))
            with self.assertRaises(ne.SafetyError):
                ne.verify_ca(self.path / "valid.pem", fingerprint)

    def test_dual_ca_requires_every_pin_and_accepts_both_server_chains(self):
        second_ca = (self.path / "wrong.pem").read_bytes()
        second_pin = ne.digest(ssl.PEM_cert_to_DER_cert(second_ca.decode()))
        bundle = self.ca + second_ca
        bundle_path = self.path / "dual.pem"
        bundle_path.write_bytes(bundle)
        original_run = ne.run
        with patch.object(ne, "run", side_effect=lambda args, check=True: original_run([OPENSSL] + args[1:], check)):
            verified = ne.verify_ca(bundle_path, self.fingerprint + "," + second_pin)
            with self.assertRaises(ne.SafetyError):
                ne.verify_ca(bundle_path, self.fingerprint)
            with self.assertRaises(ne.SafetyError):
                ne.verify_ca(bundle_path, self.fingerprint + "," + self.fingerprint)
        for name in ("valid", "new-ca"):
            self.assertEqual(ne.preflight(self.server(name).base, self.auth, verified)["tls"], "verified")

    def test_prepare_node_and_machine_with_both_kernels_keeps_token_off_command_line(self):
        server = self.server()
        original_run = ne.run
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            token = root / "token.txt"
            token.write_text("fake-secret")
            os.chmod(token, 0o600)
            for mode, kernel in [("node", "xray"), ("machine", "singbox")]:
                output = root / (mode + "-prepared")
                args = argparse.Namespace(output_dir=str(output), panel_url=server.base, mode=mode, kernel=kernel,
                                          architecture="amd64", node_id=3, machine_id=12, node_type="vmess", download=False,
                                          token_file=str(token), token_stdin=False, ca_file=str(self.path / "ca.pem"),
                                          ca_fingerprint=self.fingerprint, allow_http=False, allow_rest_fallback=False, confirm_new_identity="UNUSED")
                with patch.object(ne, "run", side_effect=lambda argv, check=True: original_run([OPENSSL] + argv[1:], check)), \
                     patch.object(ne, "trust_bundle", side_effect=lambda ca: ca):
                    result = ne.prepare(args)
                config = (output / "config.yml").read_text()
                self.assertNotIn("fake-secret", config)
                self.assertIn('type: "' + kernel + '"', config)
                self.assertEqual(result["preflight"]["websocket"], "passed")
                self.assertFalse(result["binary_downloaded"])
                self.assertIn("fake-secret", (output / "credentials.env").read_text())
                self.assertNotIn("fake-secret", json.dumps(result))
                with self.assertRaises(ne.SafetyError):
                    ne.prepare(args)

    def test_actual_cli_accepts_token_stdin_without_exposing_it(self):
        server = self.server()
        environment = os.environ.copy()
        environment["PATH"] = str(Path(OPENSSL).parent) + os.pathsep + environment.get("PATH", "")
        command = [sys.executable, str(ROOT / "tools/node/node_endpoint.py"), "preflight", "--panel-url", server.base,
                   "--mode", "node", "--node-id", "3", "--node-type", "vmess", "--token-stdin",
                   "--confirm-ws-probe", "AUTHENTICATE_STOPPED_INSTANCE",
                   "--ca-file", str(self.path / "ca.pem"), "--ca-fingerprint", self.fingerprint]
        process = subprocess.run(command, input=b"fake-secret\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(json.loads(process.stdout)["websocket"], "passed")
        self.assertNotIn(b"fake-secret", process.stdout + process.stderr)
        self.assertNotIn("fake-secret", " ".join(command))

    def test_default_cli_probe_never_registers_websocket_identity(self):
        server = self.server()
        environment = os.environ.copy()
        environment["PATH"] = str(Path(OPENSSL).parent) + os.pathsep + environment.get("PATH", "")
        command = [sys.executable, str(ROOT / "tools/node/node_endpoint.py"), "preflight", "--panel-url", server.base,
                   "--mode", "node", "--node-id", "3", "--token-stdin", "--ca-file", str(self.path / "ca.pem"),
                   "--ca-fingerprint", self.fingerprint]
        process = subprocess.run(command, input=b"fake-secret\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertIn("not tested", json.loads(process.stdout)["websocket"])
        self.assertEqual(server.ws_auth_count, 0)

    def test_wrong_websocket_node_and_machine_identity_is_rejected(self):
        server = self.server()
        server.wrong_identity = True
        for auth in (self.auth, {"machine_id": 12, "token": "fake-secret"}):
            with self.subTest(mode=auth.keys()), self.assertRaises(ne.SafetyError):
                ne.preflight(server.base, auth, self.ca)

    def test_tampered_root_self_signature_is_rejected_even_with_matching_pin(self):
        original = ssl.PEM_cert_to_DER_cert(self.ca.decode())
        tampered = original[:-1] + bytes([original[-1] ^ 1])
        path = self.path / "tampered-root.pem"
        path.write_text(ssl.DER_cert_to_PEM_cert(tampered))
        original_run = ne.run
        with patch.object(ne, "run", side_effect=lambda argv, check=True: original_run([OPENSSL] + argv[1:], check)):
            with self.assertRaises(ne.SafetyError):
                ne.verify_ca(path, ne.digest(tampered))

    def test_failed_download_leaves_only_private_uninstalled_stage_with_failure_marker(self):
        server = self.server()
        original_run = ne.run
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            token = root / "token.txt"
            token.write_text("fake-secret")
            os.chmod(token, 0o600)
            output = root / "failed-stage"
            args = argparse.Namespace(output_dir=str(output), panel_url=server.base, mode="node", kernel="xray",
                                      architecture="amd64", node_id=3, machine_id=None, node_type="vmess", download=True,
                                      token_file=str(token), token_stdin=False, ca_file=str(self.path / "ca.pem"),
                                      ca_fingerprint=self.fingerprint, allow_http=False, allow_rest_fallback=False, confirm_new_identity="UNUSED")
            with patch.object(ne, "run", side_effect=lambda argv, check=True: original_run([OPENSSL] + argv[1:], check)), \
                 patch.object(ne, "trust_bundle", side_effect=lambda ca: ca), \
                 patch.object(ne, "download_binary", side_effect=ne.SafetyError("checksum mismatch")):
                with self.assertRaises(ne.SafetyError):
                    ne.prepare(args)
            self.assertTrue((output / "PREPARATION_FAILED").is_file())
            self.assertFalse((output / "prepared.json").exists())
            self.assertFalse((output / "xboard-node").exists())
            self.assertNotIn("fake-secret", (output / "PREPARATION_FAILED").read_text())


class SimulatedTransactionTests(unittest.TestCase):
    """Only filesystem transactions/service state simulation, not deployment."""
    @staticmethod
    def private_temporary_directory():
        if os.name == 'posix' and os.geteuid() != 0:
            raise unittest.SkipTest('Linux transaction fixtures require Root-owned /var/lib; run with sudo')
        return tempfile.TemporaryDirectory(prefix='node-endpoint-unit-', dir='/var/lib' if os.name == 'posix' else None)

    def test_failed_postcheck_restores_every_file_and_active_state(self):
        with self.private_temporary_directory() as folder:
            directory = Path(folder)
            (directory / "systemd").mkdir()
            config, creds, meta = [directory / name for name in ("config.yml", "credentials.env", "install-meta.json")]
            config.write_text(MULTI)
            creds.write_text("INSTANCE_A=fake-secret\nINSTANCE_B=other-fake\n")
            _, rows = ne.describe(MULTI)
            metadata = {"version": "old-version", "latest_instance_id": rows[0]["id"], "instances": [
                {"id": row["id"], "panel_url": row["panel_url"], "custom_metadata": "preserve"} for row in rows]}
            meta.write_text(json.dumps(metadata))
            for file in (config, creds, meta):
                os.chmod(file, 0o600)
            before = {file: file.read_bytes() for file in (config, creds, meta)}
            args = argparse.Namespace(config=str(config), credentials=str(creds), meta=str(meta), service="xboard-node.service",
                                      instance_id=rows[0]["id"], from_url=rows[0]["panel_url"], panel_url="https://192.0.2.2:8443",
                                      confirm_migrate="MIGRATE:" + rows[0]["id"], ca_file="fake-ca", ca_fingerprint="f" * 64,
                                      allow_http=False, allow_rest_fallback=False)
            calls = []
            def fake_run(argv, check=True):
                calls.append(argv)
                return "active" if "is-active" in argv else ""
            results = [{"rest": "passed"}, {"rest": "passed"}, ne.SafetyError("postcheck failed")]
            with patch.object(ne, "require_linux_root"), patch.object(ne, "check_standard_service"), \
                 patch.object(ne, "migration_lock", return_value=nullcontext()), \
                 patch.object(ne, "service_provenance", return_value={"unit_sha256": "test", "binary_sha256": "test"}), \
                 patch.object(ne, "systemd_directory", return_value=directory / "systemd"), \
                 patch.object(ne, "verify_ca", return_value=b"fake-ca"), patch.object(ne, "trust_bundle", return_value=b"system+ca"), \
                 patch.object(ne, "run", side_effect=fake_run), patch.object(ne, "health_check"), patch.object(ne.time, "sleep"), \
                 patch.object(ne, "preflight", side_effect=results):
                with self.assertRaises(ne.SafetyError):
                    ne.perform_migration(args)
            for file in (config, creds, meta):
                self.assertEqual(file.read_bytes(), before[file])
            self.assertFalse((directory / "node-endpoint-trust.pem").exists())
            self.assertFalse((directory / "systemd/xboard-node.service.d/90-node-endpoint.conf").exists())
            self.assertIn(["systemctl", "start", "xboard-node.service"], calls)

    def test_inactive_snapshot_is_not_started_by_restore(self):
        with self.private_temporary_directory() as folder:
            directory = Path(folder)
            file = directory / "config.yml"
            file.write_bytes(b"original")
            state = ne.snapshot([file], directory / "backup", "xboard-node.service", False)
            file.write_bytes(b"changed")
            with patch.object(ne, "run") as run:
                ne.restore(state, directory / "backup")
                self.assertFalse(any("start" in call.args[0] for call in run.call_args_list))
            self.assertEqual(file.read_bytes(), b"original")

    def migration_fixture(self):
        temporary = self.private_temporary_directory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        (directory / "systemd").mkdir()
        config, creds, meta = [directory / name for name in ("config.yml", "credentials.env", "install-meta.json")]
        config.write_text(MULTI)
        creds.write_text("INSTANCE_A=fake-secret\nINSTANCE_B=other-fake\n")
        _, rows = ne.describe(MULTI)
        metadata = {"version": "preserve-version", "latest_instance_id": rows[0]["id"], "custom_field": 42,
                    "instances": [{"id": row["id"], "panel_url": row["panel_url"], "custom": "preserve"} for row in rows]}
        meta.write_text(json.dumps(metadata))
        for file in (config, creds, meta):
            os.chmod(file, 0o600)
        args = argparse.Namespace(config=str(config), credentials=str(creds), meta=str(meta), service="xboard-node.service",
                                  instance_id=rows[0]["id"], from_url=rows[0]["panel_url"], panel_url="https://192.0.2.2:8443",
                                  confirm_migrate="MIGRATE:" + rows[0]["id"], ca_file="fake-ca", ca_fingerprint="f" * 64,
                                  allow_http=False, allow_rest_fallback=False)
        return directory, args, metadata

    def transaction_mocks(self, directory, active=False):
        stack = ExitStack()
        stack.enter_context(patch.object(ne, "require_linux_root"))
        stack.enter_context(patch.object(ne, "check_standard_service"))
        stack.enter_context(patch.object(ne, "service_provenance", return_value={"unit_sha256": "test", "binary_sha256": "test"}))
        stack.enter_context(patch.object(ne, "migration_lock", return_value=nullcontext()))
        stack.enter_context(patch.object(ne, "systemd_directory", return_value=directory / "systemd"))
        stack.enter_context(patch.object(ne, "verify_ca", return_value=b"fake-ca"))
        stack.enter_context(patch.object(ne, "trust_bundle", return_value=b"system+ca"))
        stack.enter_context(patch.object(ne, "health_check"))
        stack.enter_context(patch.object(ne.time, "sleep"))
        mocked_run = stack.enter_context(patch.object(ne, "run", side_effect=lambda argv, check=True: "active" if active and "is-active" in argv else ""))
        return stack, mocked_run

    def test_successful_selected_migration_keeps_other_metadata_credentials_and_inactive_state(self):
        directory, args, original_meta = self.migration_fixture()
        original_credentials = (directory / "credentials.env").read_bytes()
        stack, mocked_run = self.transaction_mocks(directory)
        with stack, patch.object(ne, "preflight", return_value={"rest": "passed", "websocket": "disabled"}):
            result = ne.perform_migration(args)
        self.assertEqual((directory / "config.yml").read_text(), MULTI.replace('"https://old.example:443"', '"https://192.0.2.2:8443"'))
        self.assertEqual((directory / "credentials.env").read_bytes(), original_credentials)
        updated_meta = json.loads((directory / "install-meta.json").read_text())
        self.assertEqual(updated_meta["instances"][1], original_meta["instances"][1])
        self.assertEqual(updated_meta["version"], "preserve-version")
        self.assertEqual(updated_meta["custom_field"], 42)
        self.assertFalse(result["service_was_active"])
        self.assertFalse(any("start" in call.args[0] for call in mocked_run.call_args_list))
        state = json.loads((Path(result["backup"]) / "state.json").read_text())
        self.assertTrue(all("applied_sha256" in item for item in state["files"]))

    def test_concurrent_credentials_change_during_preflight_aborts_before_stop(self):
        directory, args, _ = self.migration_fixture()
        stack, mocked_run = self.transaction_mocks(directory)
        def concurrent_change(*_, **__):
            (directory / "credentials.env").write_text("INSTANCE_A=external-change\nINSTANCE_B=other-fake\n")
            return {"rest": "passed"}
        with stack, patch.object(ne, "preflight", side_effect=concurrent_change):
            with self.assertRaises(ne.SafetyError):
                ne.perform_migration(args)
        self.assertFalse(any("stop" in call.args[0] for call in mocked_run.call_args_list))
        self.assertIn("external-change", (directory / "credentials.env").read_text())
        self.assertEqual((directory / "config.yml").read_text(), MULTI)

    def test_damaged_backup_never_stops_service_or_partly_restores_files(self):
        with self.private_temporary_directory() as folder:
            directory = Path(folder)
            first, second = directory / "first", directory / "second"
            first.write_bytes(b"original-one")
            second.write_bytes(b"original-two")
            state = ne.snapshot([first, second], directory / "backup", "xboard-node.service", True)
            first.write_bytes(b"current-one")
            second.write_bytes(b"current-two")
            (directory / "backup/1").write_bytes(b"corrupted-backup")
            with patch.object(ne, "run") as mocked_run:
                with self.assertRaises(ne.SafetyError):
                    ne.restore(state, directory / "backup")
                mocked_run.assert_not_called()
            self.assertEqual(first.read_bytes(), b"current-one")
            self.assertEqual(second.read_bytes(), b"current-two")

    def test_install_start_and_cleanup_stop_failure_retains_unit_and_private_recovery_evidence(self):
        with self.private_temporary_directory() as folder:
            directory = Path(folder)
            prepared = directory / "prepared"
            prepared.mkdir(mode=0o700)
            systemd = directory / "systemd"
            systemd.mkdir()
            service = "node-endpoint-aabbccddeeff.service"
            config = 'panel:\n  url: "http://192.0.2.2:8080"\n  node_id: 3\n  token_env: NODE_ENDPOINT_TOKEN\n'
            files = {"config.yml": config.encode(), "credentials.env": b"NODE_ENDPOINT_TOKEN=fake\n",
                     "xboard-node": b"fake-binary-never-executed", service: b"# private simulated service fixture\n"}
            for name, content in files.items():
                (prepared / name).write_bytes(content)
                os.chmod(prepared / name, 0o600)
            manifest = {"schema": 1, "binary_downloaded": True, "new_identity_acknowledged": True, "architecture": "amd64",
                        "service": service, "panel_url": "http://192.0.2.2:8080", "files": {key: ne.digest(data) for key, data in files.items()}}
            (prepared / "prepared.json").write_text(json.dumps(manifest))
            os.chmod(prepared / "prepared.json", 0o600)
            lock = directory / "test-release-lock.json"
            lock.write_text(json.dumps({"assets": {"xboard-node-linux-amd64": ne.digest(files["xboard-node"])}}))
            calls = []
            def service_failure(argv, check=True):
                calls.append(argv)
                if "start" in argv or "stop" in argv:
                    raise ne.SafetyError("simulated service failure")
                return "not-found" if "--property=LoadState" in argv else ""
            args = argparse.Namespace(prepared_dir=str(prepared), confirm_install="NEW", enable=False, allow_rest_fallback=False)
            with patch.object(ne, "require_linux_root"), patch.object(ne, "migration_lock", return_value=nullcontext()), \
                 patch.object(ne, "systemd_directory", return_value=systemd), patch.object(ne, "LOCK_PATH", lock), \
                 patch.object(ne.os, "uname", create=True, return_value=SimpleNamespace(machine="x86_64")), \
                 patch.object(ne, "preflight", return_value={"rest": "passed"}), patch.object(ne, "run", side_effect=service_failure):
                with self.assertRaises(ne.SafetyError):
                    ne.install_new(args)
            self.assertTrue((systemd / service).is_file())
            self.assertTrue((prepared / "INSTALL_RECOVERY_REQUIRED").is_file())
            self.assertIn(["systemctl", "disable", service], calls)
            self.assertGreaterEqual(calls.count(["systemctl", "daemon-reload"]), 2)
            self.assertNotIn("NODE_ENDPOINT_TOKEN", (prepared / "INSTALL_RECOVERY_REQUIRED").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
