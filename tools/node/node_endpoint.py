#!/usr/bin/env python3
"""NodeEndpoint 1.0.0: isolated Node preparation and conservative migration.

Python 3.9+, stdlib only; openssl required for CA inspection. No upstream
installer execution and no production changes without an explicit CLI action.
"""
import argparse
import base64
import copy
from contextlib import contextmanager
import getpass
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlencode
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strict_yaml import parse, LayoutError

VERSION = "1.0.0"
LOCK_PATH = Path(__file__).with_name("release-lock.json")
NODE_REPOSITORY = "https://github.com/ksr-v/Xboard-Node--Custom-Source"
NODE_RELEASE_API = "https://api.github.com/repos/ksr-v/Xboard-Node--Custom-Source/releases/"
RELEASE_TAG = r"v[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-orphan\.[0-9]+)?"


class SafetyError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def safe_path(path, existing=True, private=False):
    path = Path(path).absolute()
    require(not path.is_symlink(), "symbolic-link targets are unsupported")
    for parent in path.parents:
        require(not parent.is_symlink(), "symbolic-link parent directories are unsupported")
        if os.name == "posix" and parent.exists():
            require(not (parent.stat().st_mode & 0o002) or bool(parent.stat().st_mode & stat.S_ISVTX),
                    "world-writable non-sticky parent directory is unsafe")
    if existing:
        require(path.is_file(), "required ordinary file is missing")
        st = path.stat()
        require(st.st_nlink == 1, "hard-linked files are unsupported")
        if private and os.name == "posix":
            require((st.st_mode & 0o077) == 0 and st.st_uid == os.geteuid(),
                    "private input must be owned by current user and mode 0600 or stricter")
    return path


def require_root_ancestry(path):
    if os.name != "posix":
        return  # Native mutation still requires Linux Root before reaching here.
    path = Path(path).absolute()
    directory = path if path.is_dir() else path.parent
    for parent in [directory] + list(directory.parents):
        if parent.exists():
            require(not parent.is_symlink() and parent.is_dir() and parent.stat().st_uid == 0 and
                    not parent.stat().st_mode & 0o022, "mutation/recovery path ancestry must be Root-owned and not group/world writable")


def atomic_write(path, data, mode=0o600):
    path = safe_path(path, existing=False)
    fd, temporary = tempfile.mkstemp(prefix=".node-endpoint-", dir=path.parent)
    try:
        os.fchmod(fd, mode) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def canonical_url(value, allow_http=False):
    require(isinstance(value, str) and not re.search(r"[\s\\\x00-\x1f]", value), "invalid panel URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:
        raise SafetyError("invalid panel URL") from exc
    require(parsed.scheme == "https" or (allow_http and parsed.scheme == "http"),
            "HTTPS is required; HTTP needs the explicit --allow-http acknowledgement")
    require(host and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            "URL must have one host and no credentials, query or fragment")
    require(parsed.path in ("", "/") and (port is None or 1 <= port <= 65535), "URL paths and invalid ports are unsupported")
    try:
        addr = ipaddress.ip_address(host)
        host = "[" + addr.compressed + "]" if addr.version == 6 else addr.compressed
    except ValueError:
        require(len(host) <= 253 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                        for label in host.split(".")), "invalid DNS host")
        host = host.lower()
    return parsed.scheme + "://" + host + (":" + str(port) if port is not None else "")


def run(args, check=True):
    process = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=30, check=False)
    if check:
        require(process.returncode == 0, "external validation failed: " + Path(args[0]).name)
    return process.stdout.decode("utf-8", errors="replace").strip()


def verify_ca(path, fingerprint):
    data = safe_path(path).read_bytes()
    pattern = rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----"
    certs = re.findall(pattern, data)
    require(b"PRIVATE KEY" not in data and len(certs) in (1, 2) and not re.sub(pattern, b"", data).strip(),
            "CA import requires one or two public root certificates and no other material")
    expected = [part.replace(":", "").lower() for part in (fingerprint or "").split(",")]
    require(len(expected) == len(certs) and len(set(expected)) == len(expected) and
            all(re.fullmatch(r"[0-9a-f]{64}", part) for part in expected),
            "provide an independent SHA-256 fingerprint for every root (comma-separated for dual trust)")
    canonical = []
    actual = []
    for cert in certs:
        try:
            der = ssl.PEM_cert_to_DER_cert(cert.decode("ascii") + "\n")
        except (ValueError, UnicodeError) as exc:
            raise SafetyError("invalid CA PEM") from exc
        actual.append(digest(der))
        canonical.append(ssl.DER_cert_to_PEM_cert(der).encode("ascii"))
    require(len(set(actual)) == len(actual) and set(actual) == set(expected),
            "CA fingerprint mismatch; obtain every expected fingerprint through an independent trusted channel")
    with tempfile.TemporaryDirectory(prefix="node-endpoint-ca-") as temporary:
        for index, cert in enumerate(canonical):
            path = Path(temporary) / (str(index) + ".pem")
            atomic_write(path, cert)
            text = run(["openssl", "x509", "-in", str(path), "-noout", "-text"])
            require(re.search(r"Basic Constraints[^\n]*\n\s*CA:TRUE", text) and
                    re.search(r"Key Usage[^\n]*\n\s*[^\n]*Certificate Sign", text), "certificate is not a signing CA")
            algorithm = re.search(r"Public Key Algorithm:\s*([^\n]+)", text)
            bits = re.search(r"Public-Key:\s*\((\d+) bit\)", text)
            name = algorithm[1].strip() if algorithm else ""
            signature = re.search(r"Signature Algorithm:\s*([^\n]+)", text)
            signature_name = signature[1].strip() if signature else ""
            rsa = name == "rsaEncryption" and bits and int(bits[1]) >= 2048
            ec = name == "id-ecPublicKey" and re.search(r"ASN1 OID:\s*(prime256v1|secp384r1|secp521r1)\s", text)
            require((rsa or ec) and re.fullmatch(r"sha(?:256|384|512)WithRSAEncryption|ecdsa-with-SHA(?:256|384|512)", signature_name),
                    "unsupported/weak CA key or signature; use RSA >= 2048 or standard P-256/P-384/P-521 with SHA-256 or stronger")
            identities = run(["openssl", "x509", "-in", str(path), "-noout", "-subject", "-issuer", "-nameopt", "RFC2253"]).splitlines()
            require(len(identities) == 2 and identities[0].split("=", 1)[1] == identities[1].split("=", 1)[1],
                    "only self-issued private root CA certificates are supported")
            run(["openssl", "verify", "-check_ss_sig", "-CAfile", str(path), str(path)])
            run(["openssl", "x509", "-in", str(path), "-checkend", "0", "-noout"])
    return b"".join(canonical)


def tls_context(ca):
    context = ssl.create_default_context(cadata=ca.decode("ascii") if ca else None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def request_json(base, path, auth, ca, method="POST"):
    parsed = urlsplit(base)
    connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=12, context=tls_context(ca))
                  if parsed.scheme == "https" else http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=12))
    body = json.dumps(auth).encode() if method == "POST" else None
    route = path if method == "POST" else path + "?" + urlencode(auth)
    try:
        connection.request(method, route, body, {"Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        require(response.status == 200, "REST endpoint rejected request (status " + str(response.status) + "); redirects are forbidden")
        payload = response.read(4 * 1024 * 1024 + 1)
        require(len(payload) <= 4 * 1024 * 1024, "REST response exceeds safety limit")
        parsed_body = json.loads(payload)
        require(isinstance(parsed_body, (dict, list)), "REST returned an unexpected JSON shape")
        return parsed_body
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, SafetyError):
            raise
        raise SafetyError("REST/TLS preflight failed; verify network, certificate chain, validity and SAN") from exc
    finally:
        connection.close()


def recv_exact(connection, size):
    result = b""
    while len(result) < size:
        part = connection.recv(size - len(result))
        require(part, "WebSocket closed before authentication completed")
        result += part
    return result


def validate_ws_url(ws_url, base):
    try:
        expected = urlsplit(base)
        target = urlsplit(ws_url)
        try:
            same_host = ipaddress.ip_address(target.hostname) == ipaddress.ip_address(expected.hostname)
        except ValueError:
            same_host = bool(target.hostname) and target.hostname.lower() == expected.hostname.lower()
        safe = (target.scheme == ("wss" if expected.scheme == "https" else "ws") and same_host and
                (target.port or (443 if target.scheme == "wss" else 80)) ==
                (expected.port or (443 if expected.scheme == "https" else 80)) and
                target.path == "/ws" and not target.query and not target.fragment and
                target.username is None and target.password is None and not re.search(r"[\s\\]", ws_url))
    except (ValueError, TypeError):
        safe = False
    require(safe, "handshake advertised an unsafe/old WebSocket endpoint; fix request-scoped handshake or use REST-only handshake")
    return target


def ws_auth(ws_url, base, auth, ca):
    target = validate_ws_url(ws_url, base)
    key = base64.b64encode(os.urandom(16)).decode()
    port = target.port or (443 if target.scheme == "wss" else 80)
    connection = None
    try:
        connection = socket.create_connection((target.hostname, port), timeout=12)
        if target.scheme == "wss":
            connection = tls_context(ca).wrap_socket(connection, server_hostname=target.hostname)
        path = "/ws?" + urlencode({k: v for k, v in auth.items() if k != "node_type"})
        request = ("GET " + path + " HTTP/1.1\r\nHost: " + target.netloc +
                   "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n")
        connection.sendall(request.encode("ascii"))
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            require(len(header) < 16384, "WebSocket response headers exceed safety limit")
            header += recv_exact(connection, 1)
        lines = header.decode("iso-8859-1").split("\r\n")
        require(re.fullmatch(r"HTTP/1\.[01] 101(?: .*)?", lines[0]), "WebSocket upgrade rejected")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                require(name.lower() not in headers, "duplicate WebSocket response header")
                headers[name.lower()] = value.strip()
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        require(headers.get("sec-websocket-accept") == accept and headers.get("upgrade", "").lower() == "websocket" and
                "upgrade" in headers.get("connection", "").lower(), "invalid WebSocket upgrade proof")
        for _ in range(5):
            first, second = recv_exact(connection, 2)
            require(first & 0x80 and not first & 0x70 and not second & 0x80, "unsupported WebSocket frame")
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", recv_exact(connection, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", recv_exact(connection, 8))[0]
            require(length <= 65536, "WebSocket auth frame exceeds safety limit")
            payload = recv_exact(connection, length)
            opcode = first & 15
            if opcode == 9:
                require(length <= 125, "invalid WebSocket ping")
                mask = os.urandom(4)
                connection.sendall(bytes([0x8a, 0x80 | length]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
                continue
            require(opcode == 1, "WebSocket did not return a text authentication event")
            event = json.loads(payload)
            require(isinstance(event, dict) and event.get("event") == "auth.success", "WebSocket authentication was rejected")
            field = "machine_id" if "machine_id" in auth else "node_id"
            require(isinstance(event.get("data"), dict) and type(event["data"].get(field)) is int and
                    event["data"][field] == auth[field], "WebSocket authenticated a different Node/machine identity")
            return
        raise SafetyError("WebSocket authentication timed out")
    except (OSError, ValueError) as exc:
        if isinstance(exc, SafetyError):
            raise
        raise SafetyError("WebSocket/TLS preflight failed") from exc
    finally:
        if connection is not None:
            connection.close()


def validate_kernel_protocol(config, kernel):
    # Match the audited pinned release's runtime Protocols(), not its broader
    # config-builder switches. In particular Xray rejects SOCKS/HTTP at runtime.
    supported = {
        "xray": {"vmess", "vless", "trojan", "shadowsocks", "hysteria"},
        # Hysteria v2 arrives as protocol=hysteria, version=2. The release's
        # sing-box builder does not handle the literal hysteria2 wire protocol.
        "singbox": {"vmess", "vless", "trojan", "shadowsocks", "hysteria",
                    "tuic", "naive", "socks", "http", "anytls", "mieru"},
    }
    require(kernel in supported and isinstance(config.get("protocol"), str) and config["protocol"] in supported[kernel],
            "REST node protocol is not supported by the selected pinned kernel")
    if kernel == "xray" and config.get("protocol") == "hysteria":
        require(type(config.get("version")) is int and config["version"] == 2,
                "the selected pinned Xray kernel supports only Hysteria version 2")


def preflight(base, auth, ca, allow_rest_fallback=False, authenticate_ws=True, kernel=None):
    require(bool(auth.get("token")), "an authentication token is required")
    handshake = request_json(base, "/api/v2/server/handshake", auth, ca)
    require(isinstance(handshake, dict) and isinstance(handshake.get("websocket"), dict), "invalid handshake response")
    machine = "machine_id" in auth
    if machine:
        response = request_json(base, "/api/v2/server/machine/nodes", auth, ca)
        require(isinstance(response, dict) and isinstance(response.get("nodes"), list), "invalid machine nodes response")
        for binding in response["nodes"]:
            require(isinstance(binding, dict) and isinstance(binding.get("id"), int) and binding["id"] > 0, "invalid discovered machine binding")
            per_node = dict(auth, node_id=binding["id"])
            config = request_json(base, "/api/v2/server/config", per_node, ca, "GET")
            require(isinstance(config, dict) and config.get("protocol"), "machine binding REST config lacks a protocol")
            if kernel is not None:
                validate_kernel_protocol(config, kernel)
            users = request_json(base, "/api/v2/server/user", per_node, ca, "GET")
            require(isinstance(users, dict) and isinstance(users.get("users"), list), "invalid machine users response")
    else:
        response = request_json(base, "/api/v1/server/UniProxy/config", auth, ca, "GET")
        require(isinstance(response, dict) and response.get("protocol"), "node REST config lacks a protocol")
        if kernel is not None:
            validate_kernel_protocol(response, kernel)
        users = request_json(base, "/api/v1/server/UniProxy/user", auth, ca, "GET")
        require(isinstance(users, dict) and isinstance(users.get("users"), list), "invalid REST users response")
    ws = handshake["websocket"]
    result = {"rest": "passed", "websocket": "disabled", "tls": "verified" if base.startswith("https:") else "plaintext"}
    if ws.get("enabled"):
        require(isinstance(ws.get("ws_url"), str) and ws["ws_url"], "enabled WebSocket has no URL")
        # Endpoint mismatch must never send the token to a different authority,
        # and remains fatal even when REST fallback is explicitly permitted.
        validate_ws_url(ws["ws_url"], base)
        if not authenticate_ws:
            result["websocket"] = "not tested (REST-only phase; authenticated probe deferred)"
            return result
        try:
            ws_auth(ws["ws_url"], base, auth, ca)
            result["websocket"] = "passed"
        except SafetyError:
            require(allow_rest_fallback, "WSS preflight failed; migration stopped (REST alone is not WSS success)")
            result["websocket"] = "failed; explicit REST fallback accepted"
    return result


def read_token(args):
    if getattr(args, "token_file", None):
        token = safe_path(args.token_file, private=True).read_text("utf-8").strip()
    elif getattr(args, "token_stdin", False):
        token = sys.stdin.readline(4097).strip()
    else:
        require(sys.stdin.isatty(), "noninteractive invocation needs --token-stdin or --token-file")
        token = getpass.getpass("Node / machine token (hidden): ")
    require(0 < len(token) <= 4096 and not re.search(r"[\r\n\x00]", token), "invalid token")
    return token


def credentials(path):
    result = {}
    for line in safe_path(path, private=True).read_text("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z_][A-Z_0-9]*)=(.*)", line)
        require(match and match[1] not in result, "unsupported or duplicate EnvironmentFile entry")
        raw = match[2]
        require(not re.search(r"[\r\n\x00]", raw), "invalid credential value")
        if raw.startswith('"'):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise SafetyError("unsupported credential quoting") from exc
            require(isinstance(raw, str) and not re.search(r"[\\\r\n]", raw), "unsupported systemd escape sequence")
        elif raw.startswith("'"):
            require(raw.endswith("'") and "'" not in raw[1:-1], "unsupported credential quoting")
            raw = raw[1:-1]
        else:
            require(not re.search(r"[\s'\"\\]", raw), "unsupported unquoted credential value")
        result[match[1]] = raw
    overrides = {"API_HOST", "API_KEY", "NODE_ID", "NODE_TYPE", "KERNEL_TYPE", "CERT_FILE", "KEY_FILE", "DOMAIN", "LOG_LEVEL",
                 "MACHINE_ID", "MACHINE_TOKEN", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    require(not overrides.intersection(result), "business/environment/trust overrides require manual migration")
    return result


def identity(instance):
    panel = instance.get("panel", {})
    raw = panel.get("url")
    canonical_url(raw, allow_http=True)  # validate, without normalizing the hash input
    parsed = urlsplit(raw.strip())
    # Match Go normalizeBaseURL: preserve original IP spelling and explicit port.
    base = parsed.scheme.lower() + "://" + parsed.netloc.lower()
    if instance.get("machine"):
        mode, target = "machine", str(instance["machine"].get("machine_id", 0))
    elif instance.get("nodes"):
        ids = [str(row.get("node_id", 0)) for row in instance["nodes"]]
        require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in ids) and len(set(ids)) == len(ids), "invalid multi-node binding")
        mode, target = "node", ",".join(sorted(ids))
    else:
        mode, target = "node", str(panel.get("node_id", 0))
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in target.split(",")), "invalid Node/machine identity")
    host = parsed.netloc.lower()
    slug = re.sub("[^a-z0-9]+", "-", host).strip("-")[:48].rstrip("-") or "panel"
    short = hashlib.sha1((base + "|" + mode + "|" + target).encode()).hexdigest()[:6]
    return slug + "-" + mode + "-" + target + "-" + short


def describe(source):
    root = parse(source)
    rows = root.get("instances")
    instances = rows.value if rows else [root]
    require(isinstance(instances, list) and instances, "empty instances are unsupported")
    result = []
    ids = set()
    for index, item in enumerate(instances):
        value = item.plain()
        require(isinstance(value, dict) and not value.get("standalone"), "standalone configurations cannot be migrated")
        item_id = identity(value)
        require(item_id not in ids, "duplicate instance identity")
        ids.add(item_id)
        result.append({"id": item_id, "index": index, "panel_url": value["panel"]["url"],
                       "mode": "machine" if value.get("machine") else "node",
                       "bindings": [{key: binding[key] for key in ("node_id", "node_type") if key in binding}
                                    for binding in value.get("nodes", [])],
                       "kernel": value.get("kernel", {}).get("type", "inherited/default"),
                       "config_dir": value.get("kernel", {}).get("config_dir"), "node": item})
    return root, result


def migrate_text(source, item_id, old_url, new_url):
    root, rows = describe(source)
    selected = [row for row in rows if row["id"] == item_id]
    require(len(selected) == 1, "target instance identity not found")
    row = selected[0]
    require(row["panel_url"] == old_url, "original panel URL did not match exactly")
    require(len(rows) == 1 or row["config_dir"], "multi-instance implicit config_dir derives from URL; manual pinning is required")
    node = row["node"]
    url_node = node.get("panel").get("url")
    require(url_node.start >= 0 and isinstance(url_node.value, str), "panel.url must be an explicit scalar")
    replacement = json.dumps(new_url)
    updated = source[:url_node.start] + replacement + source[url_node.end:]
    expected = root.plain()
    selected_plain = expected["instances"][row["index"]] if "instances" in expected else expected
    selected_plain["panel"]["url"] = new_url
    require(parse(updated).plain() == expected, "migration changed fields outside selected panel.url")
    new_id = identity(selected_plain)
    require(not any(other["id"] == new_id and other["index"] != row["index"] for other in rows), "destination instance identity collides")
    return updated, row, new_id


def instance_auth(row, env):
    value = row["node"].plain()
    block = value.get("machine") or value["panel"]
    token = block.get("token") or env.get(block.get("token_env"))
    require(isinstance(token, str) and token, "target instance token cannot be resolved from protected credentials")
    if value.get("machine"):
        return {"token": token, "machine_id": block["machine_id"]}
    panel = value["panel"]
    auth = {"token": token, "node_id": panel.get("node_id")}
    if value.get("nodes"):
        auth["node_id"] = value["nodes"][0]["node_id"]
        auth["node_type"] = value["nodes"][0].get("node_type", panel.get("node_type", ""))
    elif panel.get("node_type"):
        auth["node_type"] = panel["node_type"]
    return auth


def auth_bindings(row, env):
    auth = instance_auth(row, env)
    value = row["node"].plain()
    if value.get("machine") or not value.get("nodes"):
        return [auth]
    return [dict(auth, node_id=item["node_id"], node_type=item.get("node_type", value["panel"].get("node_type", "")))
            for item in value["nodes"]]


def release_download(url, limit, api=False):
    request = urllib.request.Request(url, headers={"User-Agent": "NodeEndpoint", "Accept": "application/vnd.github+json" if api else "application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = urlsplit(response.geturl())
            hosts = ("api.github.com",) if api else ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")
            require(final.scheme == "https" and final.hostname in hosts and final.port in (None, 443) and
                    not final.username and not final.password, "release download redirected outside trusted HTTPS hosts")
            payload = response.read(limit + 1)
        require(payload and len(payload) <= limit, "release response is empty or oversized")
        return payload
    except (OSError, ValueError) as exc:
        if isinstance(exc, SafetyError):
            raise
        raise SafetyError("official release download failed; no older release fallback is allowed") from exc


def resolve_release_lock(release, architecture):
    require(architecture in ("amd64", "arm64"), "unsupported architecture")
    require(isinstance(release, str) and (release == "latest" or re.fullmatch(RELEASE_TAG, release)), "invalid stable release tag")
    endpoint = "latest" if release == "latest" else "tags/" + release
    try:
        metadata = json.loads(release_download(NODE_RELEASE_API + endpoint, 1024 * 1024, api=True))
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SafetyError):
            raise
        raise SafetyError("invalid official release metadata") from exc
    require(isinstance(metadata, dict), "invalid official release metadata")
    tag = metadata.get("tag_name")
    require(isinstance(tag, str) and re.fullmatch(RELEASE_TAG, tag) and metadata.get("draft") is False and
            metadata.get("prerelease") is False and (release == "latest" or tag == release), "release must be the selected published stable tag")
    base = NODE_REPOSITORY + "/releases/download/" + tag + "/"
    assets = metadata.get("assets")
    require(isinstance(assets, list), "release assets are missing")
    selected = {}
    name = "xboard-node-linux-" + architecture
    for asset_name in (name, "SHA256SUMS"):
        matches = [item for item in assets if isinstance(item, dict) and item.get("name") == asset_name]
        require(len(matches) == 1 and matches[0].get("state") == "uploaded" and
                matches[0].get("browser_download_url") == base + asset_name, "release asset is missing, duplicated or foreign")
        asset_digest = matches[0].get("digest")
        require(asset_digest is None or isinstance(asset_digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", asset_digest), "invalid release asset digest")
        selected[asset_name] = matches[0]
    sums = release_download(base + "SHA256SUMS", 256 * 1024)
    require(selected["SHA256SUMS"].get("digest") in (None, "sha256:" + digest(sums)), "release checksums API digest mismatch")
    try:
        rows = [match[1].lower() for line in sums.decode("ascii").splitlines()
                if (match := re.fullmatch(r"([a-fA-F0-9]{64}) [ *](.+)", line)) and match[2] == name]
    except UnicodeError as exc:
        raise SafetyError("invalid release checksum file") from exc
    require(len(rows) == 1, "release binary checksum is missing or duplicated")
    require(selected[name].get("digest") in (None, "sha256:" + rows[0]), "binary checksum disagrees with official API digest")
    return {"schema": 1, "repository": NODE_REPOSITORY, "version": tag,
            "assets": {name: rows[0], "SHA256SUMS": digest(sums)}}


def verify_elf(payload, architecture):
    require(payload[:6] == b"\x7fELF\x02\x01" and len(payload) >= 20 and
            struct.unpack("<H", payload[18:20])[0] == (62 if architecture == "amd64" else 183), "binary ELF architecture mismatch")


def prepared_release_lock(directory, prepared, architecture):
    if prepared.get("release_selection", "pinned") == "pinned":
        return json.loads(LOCK_PATH.read_text("utf-8"))
    require(prepared.get("release_selection") == "latest", "unsupported prepared release selection")
    selected = json.loads(safe_path(directory / "selected-release-lock.json", private=True).read_text("utf-8"))
    require(isinstance(selected, dict) and selected.get("version") == prepared.get("upstream_version"), "prepared release tag changed")
    # Resolve the exact prepared tag again, never latest: a newer publication
    # between prepare and install must not replace the already checked binary.
    official = resolve_release_lock(prepared["upstream_version"], architecture)
    require(selected == official, "prepared release checksums changed; prepare again in a new directory")
    return official


def verify_prepared_binary(directory, prepared, architecture):
    lock = prepared_release_lock(directory, prepared, architecture)
    binary = safe_path(directory / "xboard-node", private=True)
    payload = binary.read_bytes()
    require(digest(payload) == lock["assets"]["xboard-node-linux-" + architecture], "prepared binary no longer matches release lock")
    if prepared.get("release_selection") == "latest":
        verify_elf(payload, architecture)
        version = run([str(binary), "-v"])
        require(re.match(r"^xboard-node\s+" + re.escape(lock["version"]) + r"(?:\s|$)", version), "prepared binary version does not match the selected official tag")


def download_binary(architecture, directory, release="pinned"):
    require(architecture in ("amd64", "arm64"), "unsupported architecture")
    require(release in ("pinned", "latest"), "unsupported release selection")
    lock = resolve_release_lock("latest", architecture) if release == "latest" else json.loads(LOCK_PATH.read_text("utf-8"))
    name = "xboard-node-linux-" + architecture
    url = lock["repository"] + "/releases/download/" + lock["version"] + "/" + name
    try:
        if release == "latest":
            payload = release_download(url, 100 * 1024 * 1024)
        else:
            with urllib.request.urlopen(url, timeout=30) as response:
                require(response.geturl().startswith("https://"), "download redirected outside HTTPS")
                payload = response.read(100 * 1024 * 1024 + 1)
        require(len(payload) <= 100 * 1024 * 1024 and digest(payload) == lock["assets"][name], "binary SHA-256 mismatch")
        verify_elf(payload, architecture)
        atomic_write(Path(directory) / "xboard-node", payload, 0o700)
    except (OSError, ValueError) as exc:
        if isinstance(exc, SafetyError):
            raise
        raise SafetyError("verified download failed; no executable was installed") from exc
    return lock


def trust_bundle(ca):
    paths = ssl.get_default_verify_paths()
    path = Path(paths.cafile) if paths.cafile else None
    require(path and path.is_file(), "system CA bundle could not be identified; manual trust integration is required")
    existing = path.read_bytes()
    require(b"PRIVATE KEY" not in existing and b"BEGIN CERTIFICATE" in existing, "invalid system CA bundle")
    return existing.rstrip() + b"\n" + ca


def require_linux_root():
    require(sys.platform.startswith("linux") and os.geteuid() == 0, "this mutation requires explicit Linux Root execution")
    require(Path("/run/systemd/system").is_dir(), "only native systemd deployments are supported")


def check_standard_service(service, config, credential_file):
    require(service == "xboard-node.service" and str(config) == "/etc/xboard-node/config.yml" and
            str(credential_file) == "/etc/xboard-node/credentials.env", "only the audited standard installer unit is supported")
    unit = run(["systemctl", "cat", service])
    lines = [line.strip() for line in unit.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    starts = [line for line in lines if line.startswith("ExecStart=")]
    env_files = [line for line in lines if line.startswith("EnvironmentFile=")]
    owned_environment = "Environment=SSL_CERT_FILE=" + str(config.parent / "node-endpoint-trust.pem")
    trust_dropin = systemd_directory() / (service + ".d") / "90-node-endpoint.conf"
    owned_dropin = ("# NodeEndpoint 1.0.0 owned trust setting\n[Service]\n" + owned_environment + "\n").encode()
    if trust_dropin.exists():
        require(safe_path(trust_dropin).read_bytes() == owned_dropin, "existing NodeEndpoint trust drop-in changed; manual recovery required")
    dropins = run(["systemctl", "show", service, "--property=DropInPaths", "--value"])
    require(dropins == (str(trust_dropin) if trust_dropin.exists() else ""), "unowned/custom service drop-ins are unsupported")
    expected = ["[Unit]", "Description=Xboard Node Backend", "After=network-online.target", "Wants=network-online.target",
                "[Service]", "Type=simple", "WorkingDirectory=/etc/xboard-node",
                "EnvironmentFile=-/etc/xboard-node/credentials.env",
                "ExecStart=/usr/local/bin/xboard-node -c /etc/xboard-node/config.yml", "Restart=always", "RestartSec=5",
                "KillMode=control-group", "TimeoutStopSec=30", "LimitNOFILE=1048576", "NoNewPrivileges=true",
                "StandardOutput=journal", "StandardError=journal", "[Install]", "WantedBy=multi-user.target"]
    if trust_dropin.exists():
        expected += ["[Service]", owned_environment]
    require(lines == expected, "unit directives differ from the audited standard installer; manual migration required")
    env_lines = [line for line in lines if line.startswith("Environment=")]
    require(starts == ["ExecStart=/usr/local/bin/xboard-node -c /etc/xboard-node/config.yml"] and
            env_files == ["EnvironmentFile=-/etc/xboard-node/credentials.env"] and
            env_lines == ([owned_environment] if trust_dropin.exists() else []) and
            not any(line.startswith(("PassEnvironment=", "RootDirectory=", "RootImage=", "User=")) for line in lines),
            "custom unit/environment overrides are unsupported; no files changed")
    environment = run(["systemctl", "show", service, "--property=Environment", "--value"])
    require(environment == (owned_environment.split("=", 1)[1] if trust_dropin.exists() else ""),
            "existing service Environment settings require manual trust integration")
    binary = safe_path("/usr/local/bin/xboard-node")
    require_root_ancestry(binary)
    require(binary.stat().st_uid == 0 and not binary.stat().st_mode & 0o022, "Node binary ownership or permissions are unsafe")


def systemd_directory():
    return Path("/etc/systemd/system")


def service_provenance(service):
    fragment = run(["systemctl", "show", service, "--property=FragmentPath", "--value"])
    require(fragment == str(systemd_directory() / service), "service fragment differs from audited installer ownership")
    unit = safe_path(fragment)
    require_root_ancestry(unit)
    binary = safe_path("/usr/local/bin/xboard-node")
    require(unit.stat().st_uid == 0 and not unit.stat().st_mode & 0o022, "service fragment ownership is unsafe")
    return {"unit_sha256": digest(unit.read_bytes()), "binary_sha256": digest(binary.read_bytes())}


@contextmanager
def migration_lock(config):
    """Hold one Root-owned nonblocking flock for the entire read/write transaction."""
    import fcntl
    path = safe_path(Path(config).parent / ".node-endpoint.lock", existing=False)
    require_root_ancestry(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077,
                "unsafe migration lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SafetyError("another NodeEndpoint migration/rollback holds this configuration lock") from exc
        yield
    finally:
        os.close(fd)


def health_check(service, port):
    require(run(["systemctl", "is-active", service], check=False) == "active", "Node service is not active after change")
    if port:
        require(isinstance(port, int) and 1 <= port <= 65535, "invalid local health port")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            require(response.status == 200 and json.loads(response.read(4096)).get("status") == "ok", "Node health endpoint failed")
        finally:
            connection.close()


def snapshot(files, directory, service, was_active):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = []
    for index, path in enumerate(files):
        path = safe_path(path, existing=False)
        record = {"path": str(path), "backup": str(index), "existed": path.exists()}
        if path.exists():
            safe_path(path)
            st = path.stat()
            record.update(mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)
            original = path.read_bytes()
            record["backup_sha256"] = digest(original)
            atomic_write(directory / str(index), original)
        records.append(record)
    state = {"schema": 1, "service": service, "was_active": was_active, "files": records}
    atomic_write(directory / "state.json", json.dumps(state).encode())
    return state


def restore(state, directory):
    require_root_ancestry(directory)
    require(state.get("schema") == 1 and isinstance(state.get("files"), list) and
            re.fullmatch(r"(?:xboard-node|node-endpoint-[0-9a-f]{12})\.service", state.get("service", "")), "invalid recovery snapshot")
    # Validate every snapshot first. A damaged later backup must not leave an
    # earlier file restored and the service started with a partial configuration.
    for row in state["files"]:
        require(isinstance(row, dict) and Path(row.get("backup", "")).name == row.get("backup") and
                re.fullmatch(r"[0-9]+", row.get("backup", "")), "invalid backup file reference")
        safe_path(row["path"], existing=False)
        require_root_ancestry(row["path"])
        if row["existed"]:
            original = safe_path(directory / row["backup"], private=True).read_bytes()
            require(digest(original) == row.get("backup_sha256"), "private recovery backup is damaged; service/files were not changed")
    if state.get("service_provenance"):
        require(service_provenance(state["service"]) == state["service_provenance"], "service unit/binary changed; manual recovery required")
    run(["systemctl", "stop", state["service"]])
    for row in state["files"]:
        path = safe_path(row["path"], existing=False)
        if row["existed"]:
            atomic_write(path, (directory / row["backup"]).read_bytes(), row["mode"])
            if hasattr(os, "chown"):
                os.chown(path, row["uid"], row["gid"])
        elif path.exists():
            require(path.is_file(), "rollback target changed type")
            path.unlink()
    run(["systemctl", "daemon-reload"])
    if state["was_active"]:
        run(["systemctl", "start", state["service"]])


def perform_migration(args):
    require_linux_root()
    config = safe_path(args.config, private=True)
    with migration_lock(config):
        return _perform_migration(args)


def _perform_migration(args):
    require_linux_root()
    config = safe_path(args.config, private=True)
    credential_file = safe_path(args.credentials, private=True)
    meta = safe_path(args.meta)
    for target in (config, credential_file, meta, systemd_directory()):
        require_root_ancestry(target)
    require(meta == config.parent / "install-meta.json", "only the audited install metadata path is supported")
    require(os.name != "posix" or (meta.stat().st_uid == 0 and not meta.stat().st_mode & 0o022), "install metadata ownership or permissions are unsafe")
    check_standard_service(args.service, config, credential_file)
    provenance = service_provenance(args.service)
    require(args.confirm_migrate == "MIGRATE:" + args.instance_id, "explicit --confirm-migrate MIGRATE:<instance-id> is required")
    new_url = canonical_url(args.panel_url, args.allow_http)
    source = config.read_bytes().decode("utf-8")
    credential_hash = digest(credential_file.read_bytes())
    meta_hash = digest(meta.read_bytes())
    updated, row, new_id = migrate_text(source, args.instance_id, args.from_url, new_url)
    require(row["node"].plain().get("log", {}).get("level", "info") != "debug" and
            parse(source).plain().get("log", {}).get("level", "info") != "debug", "upstream debug logging exposes WS tokens; reduce log level manually first")
    env = credentials(credential_file)
    auths = auth_bindings(row, env)
    ca = verify_ca(args.ca_file, args.ca_fingerprint) if new_url.startswith("https:") else b""
    result = [preflight(new_url, auth, ca, args.allow_rest_fallback, authenticate_ws=False) for auth in auths]
    metadata = json.loads(meta.read_text("utf-8"))
    matching = [item for item in metadata.get("instances", []) if item.get("id") == args.instance_id and item.get("panel_url") == args.from_url]
    require(len(matching) == 1, "install metadata does not match the selected instance")
    matching[0].update(id=new_id, panel_url=new_url)
    if metadata.get("latest_instance_id") == args.instance_id:
        metadata["latest_instance_id"] = new_id
    bundle = config.parent / "node-endpoint-trust.pem"
    dropin_dir = systemd_directory() / (args.service + ".d")
    dropin = dropin_dir / "90-node-endpoint.conf"
    safe_path(bundle, existing=False)
    require(bundle.exists() == dropin.exists(), "unpaired trust bundle/drop-in needs manual recovery")
    if bundle.exists():
        safe_path(bundle, private=True)
    merged_trust = (bundle.read_bytes().rstrip() + b"\n" + ca if bundle.exists() else trust_bundle(ca)) if ca else None
    was_active = run(["systemctl", "is-active", args.service], check=False) == "active"
    state_parent = config.parent / ".node-endpoint-backups"
    state_parent.mkdir(mode=0o700, exist_ok=True)
    require(not state_parent.is_symlink() and (os.name != "posix" or (state_parent.stat().st_mode & 0o077) == 0), "unsafe backup directory")
    backup = state_parent / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4))
    require(config.read_bytes().decode("utf-8") == source and digest(credential_file.read_bytes()) == credential_hash and
            digest(meta.read_bytes()) == meta_hash, "config/credentials/metadata changed during preflight; no service change performed")
    require(service_provenance(args.service) == provenance, "service unit/binary changed during preflight; no service change performed")
    state = snapshot([config, credential_file, meta, bundle, dropin], backup, args.service, was_active)
    state["service_provenance"] = provenance
    atomic_write(backup / "state.json", json.dumps(state, indent=2).encode())
    try:
        run(["systemctl", "stop", args.service])
        require(config.read_bytes().decode("utf-8") == source and digest(credential_file.read_bytes()) == credential_hash and
                digest(meta.read_bytes()) == meta_hash, "config/credentials/metadata changed since preflight; restore and retry")
        # Xboard WS authentication registers the identity and clears device
        # state. Test only while the target process is stopped, before starting
        # its replacement; never displace the newly connected production Node.
        result = [preflight(new_url, auth, ca, args.allow_rest_fallback) for auth in auths]
        if ca:
            atomic_write(bundle, merged_trust, 0o600)
            dropin_dir.mkdir(mode=0o755, exist_ok=True)
            safe_path(dropin, existing=False)
            atomic_write(dropin, ("# NodeEndpoint 1.0.0 owned trust setting\n[Service]\nEnvironment=SSL_CERT_FILE=" + str(bundle) + "\n").encode(), 0o600)
        atomic_write(config, updated.encode(), stat.S_IMODE(config.stat().st_mode))
        atomic_write(meta, (json.dumps(metadata, indent=2) + "\n").encode(), stat.S_IMODE(meta.stat().st_mode))
        run(["systemctl", "daemon-reload"])
        if was_active:
            run(["systemctl", "start", args.service])
            time.sleep(1)
            port = parse(source).plain().get("health_port", row["node"].plain().get("health_port", 0))
            health_check(args.service, port)
        for auth in auths:
            preflight(new_url, auth, ca, args.allow_rest_fallback, authenticate_ws=False)
        for item in state["files"]:
            path = Path(item["path"])
            item["applied_sha256"] = digest(path.read_bytes()) if path.exists() else None
        atomic_write(backup / "state.json", json.dumps(state, indent=2).encode())
        return {"result": result, "backup": str(backup), "old_id": args.instance_id, "new_id": new_id,
                "service_was_active": was_active, "bindings_preserved": True}
    except BaseException:
        try:
            restore(state, backup)
        except BaseException as failure:
            raise SafetyError("rollback incomplete; service recovery requires the private backup at " + str(backup)) from failure
        raise


def prepare(args):
    release = getattr(args, "release", "pinned")
    require(release in ("pinned", "latest") and (release == "pinned" or args.download), "latest preparation requires --download")
    directory = safe_path(args.output_dir, existing=False)
    require(re.fullmatch(r"/[A-Za-z0-9_./-]+", str(directory)) if os.name == "posix" else True,
            "prepared directory requires a simple absolute Linux path")
    require(not directory.exists(), "prepare requires a new directory; existing instances are never replaced")
    require(args.confirm_new_identity == "UNUSED", "prepare requires --confirm-new-identity UNUSED; authenticated WSS probes can displace a running identity")
    base = canonical_url(args.panel_url, args.allow_http)
    require(args.kernel in ("singbox", "xray") and args.architecture in ("amd64", "arm64"), "unsupported kernel/architecture")
    token = read_token(args)
    require(re.fullmatch(r"[A-Za-z0-9_.~+/=-]{1,4096}", token), "new systemd credentials require an unambiguous token alphabet")
    identifier = args.machine_id if args.mode == "machine" else args.node_id
    require(isinstance(identifier, int) and identifier > 0, "a positive node-id or machine-id is required")
    ca = verify_ca(args.ca_file, args.ca_fingerprint) if base.startswith("https:") else b""
    auth = {"token": token, "machine_id" if args.mode == "machine" else "node_id": identifier}
    if args.mode == "node" and args.node_type:
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,31}", args.node_type), "invalid node-type")
        auth["node_type"] = args.node_type
    result = preflight(base, auth, ca, args.allow_rest_fallback, kernel=args.kernel)
    unit_name = "node-endpoint-" + secrets.token_hex(6) + ".service"
    directory.mkdir(parents=True, mode=0o700)
    try:
        body = "panel:\n  url: " + json.dumps(base) + "\n"
        if args.mode == "machine":
            body += "machine:\n  machine_id: " + str(identifier) + "\n  token_env: NODE_ENDPOINT_TOKEN\n"
        else:
            body += "  node_id: " + str(identifier) + "\n  token_env: NODE_ENDPOINT_TOKEN\n"
            if args.node_type:
                body += "  node_type: " + json.dumps(args.node_type) + "\n"
        body += "kernel:\n  type: " + json.dumps(args.kernel) + "\n  config_dir: " + json.dumps(str(directory / "data"))
        body += "\nlog:\n  level: info\n  output: stdout\nhealth_port: 0\n"
        parse(body)
        atomic_write(directory / "config.yml", body.encode())
        atomic_write(directory / "credentials.env", ("NODE_ENDPOINT_TOKEN=" + token + "\n").encode())
        if ca:
            atomic_write(directory / "ca.pem", ca)
            atomic_write(directory / "trust.pem", trust_bundle(ca))
        service = ("# NodeEndpoint 1.0.0 owned unit\n[Unit]\nDescription=NodeEndpoint isolated independent Node\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory=" + str(directory) +
                   "\nEnvironmentFile=" + str(directory / "credentials.env") +
                   ("\nEnvironment=SSL_CERT_FILE=" + str(directory / "trust.pem") if ca else "") +
                   "\nExecStart=" + str(directory / "xboard-node") + " -c " + str(directory / "config.yml") +
                   "\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=multi-user.target\n")
        atomic_write(directory / unit_name, service.encode())
        if release == "latest":
            lock = download_binary(args.architecture, directory, release="latest")
        else:
            lock = download_binary(args.architecture, directory) if args.download else json.loads(LOCK_PATH.read_text("utf-8"))
        if release == "latest":
            atomic_write(directory / "selected-release-lock.json", json.dumps(lock, indent=2).encode())
        prepared = {"schema": 1, "version": VERSION, "upstream_version": lock["version"], "service": unit_name,
                    "architecture": args.architecture, "mode": args.mode, "panel_url": base, "preflight": result,
                    "binary_downloaded": args.download, "new_identity_acknowledged": True, "release_selection": release}
        prepared["files"] = {file.name: digest(file.read_bytes()) for file in directory.iterdir() if file.is_file()}
        atomic_write(directory / "prepared.json", json.dumps(prepared, indent=2).encode())
        return prepared
    except BaseException:
        # Failed preparation remains private for inspection; never run or trust it.
        atomic_write(directory / "PREPARATION_FAILED", b"No service was installed. Review or securely remove this private directory.\n")
        raise


def install_new(args):
    require_linux_root()
    config = safe_path(Path(args.prepared_dir) / "config.yml", private=True)
    with migration_lock(config):
        return _install_new(args)


def _install_new(args):
    require_linux_root()
    require(args.confirm_install == "NEW", "explicit --confirm-install NEW is required")
    directory = safe_path(args.prepared_dir, existing=False)
    require_root_ancestry(directory)
    require_root_ancestry(systemd_directory())
    require(directory.is_dir() and (os.name != "posix" or (directory.stat().st_uid == 0 and not directory.stat().st_mode & 0o077)),
            "prepared directory must be private and Root-owned")
    prepared = json.loads(safe_path(directory / "prepared.json", private=True).read_text("utf-8"))
    require(prepared.get("schema") == 1 and prepared.get("binary_downloaded") and prepared.get("new_identity_acknowledged") and
            not (directory / "PREPARATION_FAILED").exists() and not (directory / "INSTALL_RECOVERY_REQUIRED").exists(),
            "successful --download preparation is required")
    architecture = "amd64" if os.uname().machine == "x86_64" else "arm64" if os.uname().machine == "aarch64" else "unsupported"
    require(prepared["architecture"] == architecture, "prepared binary architecture does not match host")
    require(re.fullmatch(r"node-endpoint-[0-9a-f]{12}\.service", prepared["service"]), "invalid prepared unit name")
    for name, expected in prepared["files"].items():
        require(Path(name).name == name and digest(safe_path(directory / name, private=True).read_bytes()) == expected,
                "prepared file changed after verification")
    verify_prepared_binary(directory, prepared, architecture)
    unit = systemd_directory() / prepared["service"]
    safe_path(unit, existing=False)
    require(not unit.exists() and run(["systemctl", "show", prepared["service"], "--property=LoadState", "--value"], check=False) == "not-found",
            "unit already exists; no replacement is allowed")
    _, rows = describe((directory / "config.yml").read_text("utf-8"))
    auth = instance_auth(rows[0], credentials(directory / "credentials.env"))
    ca = (directory / "ca.pem").read_bytes() if (directory / "ca.pem").exists() else b""
    result = preflight(prepared["panel_url"], auth, ca, args.allow_rest_fallback)
    try:
        atomic_write(unit, (directory / prepared["service"]).read_bytes(), 0o600)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", prepared["service"]])
        time.sleep(1)
        health_check(prepared["service"], 0)
        preflight(prepared["panel_url"], auth, ca, args.allow_rest_fallback, authenticate_ws=False)
        if args.enable:
            run(["systemctl", "enable", prepared["service"]])
        return {"service": prepared["service"], "enabled": args.enable, "result": result}
    except BaseException:
        failures = []
        stopped = False
        try:
            run(["systemctl", "stop", prepared["service"]])
            stopped = True
        except BaseException:
            failures.append("stop failed; unit retained for manual service recovery")
        try:
            run(["systemctl", "disable", prepared["service"]])
        except BaseException:
            failures.append("disable failed")
        if stopped:
            try:
                if unit.exists():
                    require(digest(unit.read_bytes()) == prepared["files"][prepared["service"]], "new unit changed during failed install")
                    unit.unlink()
            except BaseException:
                failures.append("owned unit removal failed")
        try:
            run(["systemctl", "daemon-reload"])
        except BaseException:
            failures.append("daemon-reload failed")
        if failures:
            marker = ("NodeEndpoint installation recovery required\nService: " + prepared["service"] + "\n" + "\n".join(failures) + "\n")
            atomic_write(directory / "INSTALL_RECOVERY_REQUIRED", marker.encode())
            raise SafetyError("new installation cleanup incomplete; review private INSTALL_RECOVERY_REQUIRED in " + str(directory))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="read-only supported block YAML identity inventory")
    inspect.add_argument("--config", required=True)
    inspect.add_argument("--service", help="optional read-only systemd deployment/state inventory")
    actions.add_parser("release-lock")
    download = actions.add_parser("download", help="download fixed hash-verified Node binary, without executing it")
    download.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    download.add_argument("--directory", required=True)
    ca_import = actions.add_parser("ca-import", help="independently verify public CA into a file; no system trust change")
    ca_import.add_argument("--output", required=True)
    for command in (ca_import,):
        command.add_argument("--ca-file", required=True)
        command.add_argument("--ca-fingerprint", required=True)
    prep = actions.add_parser("prepare", help="preflight and stage a completely new Node installation")
    prep.add_argument("--output-dir", required=True)
    prep.add_argument("--download", action="store_true")
    prep.add_argument("--release", choices=["pinned", "latest"], default="pinned",
                      help="latest resolves a stable official tag once; the private preparation pins it for install-new")
    prep.add_argument("--confirm-new-identity", choices=["UNUSED"], required=True,
                      help="declare target identity unused and exclusive; WS authentication registers/clears its device state")
    prep.add_argument("--kernel", choices=["singbox", "xray"], default="singbox")
    prep.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    check = actions.add_parser("preflight", help="real authenticated REST and separate TLS WebSocket test")
    check.add_argument("--confirm-ws-probe", choices=["AUTHENTICATE_STOPPED_INSTANCE"],
                       help="authorize a real WS auth probe only for an exclusive stopped identity; it changes registry/device state")
    for command in (prep, check):
        command.add_argument("--mode", choices=["node", "machine"], required=True)
        command.add_argument("--node-id", type=int)
        command.add_argument("--machine-id", type=int)
        command.add_argument("--node-type")
        tokens = command.add_mutually_exclusive_group()
        tokens.add_argument("--token-file")
        tokens.add_argument("--token-stdin", action="store_true")
    migration = actions.add_parser("migrate", help="explicit migration of one audited standard-systemd binding")
    for key in ("config", "credentials", "meta", "service", "instance-id", "from-url", "confirm-migrate"):
        migration.add_argument("--" + key, required=True)
    for command in (prep, check, migration):
        command.add_argument("--panel-url", required=True)
        command.add_argument("--ca-file")
        command.add_argument("--ca-fingerprint")
        command.add_argument("--allow-http", action="store_true", help="acknowledge plaintext token/content transport")
        command.add_argument("--allow-rest-fallback", action="store_true", help="explicitly permit failed expected WSS; old endpoint remains forbidden")
    install = actions.add_parser("install-new", help="Linux Root/systemd: install only a completely new prepared unit")
    install.add_argument("--prepared-dir", required=True)
    install.add_argument("--confirm-install", required=True)
    install.add_argument("--enable", action="store_true")
    install.add_argument("--allow-rest-fallback", action="store_true")
    rollback = actions.add_parser("rollback", help="Linux Root: restore one private migration snapshot if no later changes")
    rollback.add_argument("--backup", required=True)
    rollback.add_argument("--confirm-rollback", required=True)
    args = parser.parse_args()
    if args.action in ("prepare", "preflight", "migrate") and args.panel_url.startswith("https:"):
        require(args.ca_file and args.ca_fingerprint, "HTTPS requires --ca-file and an independently verified --ca-fingerprint")
    if args.action == "inspect":
        _, rows = describe(safe_path(args.config).read_bytes().decode("utf-8"))
        result = [{k: v for k, v in row.items() if k != "node"} for row in rows]
        if args.service:
            require(sys.platform.startswith("linux") and re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", args.service), "service inspection requires a Linux systemd unit name")
            result = {"instances": result, "service": {"unit": args.service,
                      "active_state": run(["systemctl", "show", args.service, "--property=ActiveState", "--value"], check=False),
                      "enablement": run(["systemctl", "show", args.service, "--property=UnitFileState", "--value"], check=False),
                      "fragment": run(["systemctl", "show", args.service, "--property=FragmentPath", "--value"], check=False),
                      "supported_for_automatic_migration": args.service == "xboard-node.service" and str(Path(args.config).absolute()) == "/etc/xboard-node/config.yml"}}
    elif args.action == "release-lock":
        result = json.loads(LOCK_PATH.read_text("utf-8"))
    elif args.action == "ca-import":
        require(not Path(args.output).exists(), "CA output already exists; use a separate file for dual-trust rotation")
        ca = verify_ca(args.ca_file, args.ca_fingerprint)
        atomic_write(args.output, ca)
        certs = re.findall(rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----", ca)
        result = {"ca_sha256": [digest(ssl.PEM_cert_to_DER_cert(cert.decode())) for cert in certs], "installed_into_system_trust": False}
    elif args.action == "download":
        directory = safe_path(args.directory, existing=False)
        require(not directory.exists(), "download target must be a new directory")
        directory.mkdir(mode=0o700, parents=True)
        result = download_binary(args.architecture, directory)
    elif args.action == "prepare":
        result = prepare(args)
    elif args.action == "preflight":
        base = canonical_url(args.panel_url, args.allow_http)
        identifier = args.machine_id if args.mode == "machine" else args.node_id
        require(isinstance(identifier, int) and identifier > 0, "positive identity is required")
        auth = {"token": read_token(args), "machine_id" if args.mode == "machine" else "node_id": identifier}
        if args.node_type:
            auth["node_type"] = args.node_type
        ca = verify_ca(args.ca_file, args.ca_fingerprint) if base.startswith("https:") else b""
        result = preflight(base, auth, ca, args.allow_rest_fallback, bool(args.confirm_ws_probe))
    elif args.action == "migrate":
        result = perform_migration(args)
    elif args.action == "install-new":
        result = install_new(args)
    else:
        require_linux_root()
        require(args.confirm_rollback == "ROLLBACK", "explicit --confirm-rollback ROLLBACK is required")
        directory = safe_path(args.backup, existing=False)
        state = json.loads(safe_path(directory / "state.json", private=True).read_text("utf-8"))
        require(state.get("schema") == 1 and state.get("service") == "xboard-node.service", "unsupported snapshot")
        require(state.get("files") and state["files"][0].get("path") == "/etc/xboard-node/config.yml", "unsupported recovery configuration")
        with migration_lock(state["files"][0]["path"]):
            expected_paths = ["/etc/xboard-node/config.yml", "/etc/xboard-node/credentials.env", "/etc/xboard-node/install-meta.json",
                              "/etc/xboard-node/node-endpoint-trust.pem", "/etc/systemd/system/xboard-node.service.d/90-node-endpoint.conf"]
            require([row.get("path") for row in state["files"]] == expected_paths and state.get("service_provenance"),
                    "recovery snapshot file allowlist/schema mismatch")
            check_standard_service(state["service"], Path(expected_paths[0]), Path(expected_paths[1]))
            for row in state["files"]:
                path = safe_path(row["path"], existing=False)
                actual = digest(path.read_bytes()) if path.exists() else None
                require("applied_sha256" in row and actual == row["applied_sha256"], "later changes detected; manual recovery required")
            restore(state, directory)
        result = {"restored": True, "service_was_active": state["was_active"]}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (SafetyError, LayoutError) as error:
        print("NodeEndpoint refused: " + str(error), file=sys.stderr)
        sys.exit(2)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Do not print exceptions that might contain authenticated URLs or tokens.
        print("NodeEndpoint failed safely. Check protected inputs, prerequisites and private recovery files.", file=sys.stderr)
        sys.exit(3)
