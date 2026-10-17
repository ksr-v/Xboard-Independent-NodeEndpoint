#!/usr/bin/env python3
"""Real isolated Nginx integration fixture, for a disposable Linux CI machine.

Requires root, nginx, openssl and ss already installed. Refuses any existing
Nginx master. This fixture is NOT a full Xboard/PHP/Docker acceptance test.
"""
import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from pki import PKI, SafetyError, read_json, write_json
from nginx import check_port
from manager import Environment, Manager, local_probe, command


def free_port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


def process_info(pid):
    """Read stable Linux process identity; never print arbitrary command lines."""
    folder = Path('/proc') / str(pid)
    try:
        fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
        result = {'pid': int(pid), 'state': fields[0], 'parent': int(fields[1]), 'start': fields[19]}
        if result['state'] == 'Z':
            return result
        executable = (folder / 'exe').stat()
        result['exe'] = (executable.st_dev, executable.st_ino)
        result['command'] = (folder / 'cmdline').read_bytes().replace(b'\x00', b' ').decode(errors='replace').strip()
        return result
    except (FileNotFoundError, ProcessLookupError):
        return None


def existing_proxy_pids():
    found = []
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            comm = (folder / 'comm').read_text().strip().lower()
            if 'nginx' in comm or 'openresty' in comm:
                info = process_info(folder.name)
                if info and info['state'] != 'Z':
                    found.append(info['pid'])
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


class Backend(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_):
        pass
    def do_GET(self):
        if self.path == '/ws' and self.headers.get('Upgrade', '').lower() == 'websocket':
            import base64
            accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] +
                '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            return
        status, payload = (401, b'{"fixture":"node-api-auth-required"}') if self.path.startswith('/api/') else (200, b'original-website')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class RealNginxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise unittest.SkipTest('NOT TESTED: requires disposable Linux Root machine')
        if not all(shutil.which(x) for x in ('nginx', 'openssl', 'ss')):
            raise unittest.SkipTest('NOT TESTED: nginx/openssl/ss are not installed')
        if existing_proxy_pids():
            raise SafetyError('Refusing fixture: an existing Nginx/OpenResty process is running')
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise SafetyError('Fixture requires Linux pidfd support for safe process cleanup')
        # The manager deliberately rejects writable ancestors such as /tmp.
        # Avoid an automatic TemporaryDirectory finalizer: failed cleanup must
        # retain the PID/configuration/logs rather than delete recovery evidence.
        cls.root = Path(tempfile.mkdtemp(prefix='node-endpoint-nginx-integration-', dir='/var/lib')).resolve()
        cls.root_identity = (cls.root.stat().st_dev, cls.root.stat().st_ino)
        cls.fixture_token = secrets.token_hex(16)
        cls.owned_processes = {}
        cls.cleaned = False
        cls.addClassCleanup(cls.cleanup_fixture)
        write_json(cls.root / '.fixture-owner.json', {'tool': 'NodeEndpoint Linux integration',
                   'token': cls.fixture_token, 'path': str(cls.root)})
        cls.sites = cls.root / 'sites'
        cls.sites.mkdir()
        cls.site = cls.sites / 'original.conf'
        cls.config = cls.sites / 'node-endpoint.conf'
        cls.original_port, cls.endpoint_port = free_port(), free_port()
        while cls.endpoint_port == cls.original_port:
            cls.endpoint_port = free_port()
        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        cls.site.write_text(f'server {{ listen 127.0.0.1:{cls.original_port}; server_name original.example; '
                            f'location / {{ proxy_pass http://127.0.0.1:{cls.backend.server_port}; }} }}\n')
        cls.original_bytes = cls.site.read_bytes()
        cls.main = cls.root / 'nginx.conf'
        cls.main.write_text(f'pid {cls.root}/nginx.pid; error_log {cls.root}/nginx-error.log; '
                            f'events {{}} http {{ access_log off; include {cls.sites}/*.conf; }}\n')
        cls.nginx = shutil.which('nginx')
        binary = Path(cls.nginx).resolve().stat()
        cls.nginx_identity = (binary.st_dev, binary.st_ino)
        command([cls.nginx, '-p', str(cls.root) + '/', '-c', cls.main, '-t'])
        command([cls.nginx, '-p', str(cls.root) + '/', '-c', cls.main])
        for _ in range(30):
            cls.capture_fixture_processes()
            if cls.owned_processes:
                break
            time.sleep(0.1)
        if not cls.owned_processes:
            raise SafetyError('Started fixture Nginx could not be identified; recovery directory retained')
        cls.env = Environment(cls.nginx)
        cls.manager = Manager(cls.root / 'state', cls.env)
        cls.pki = cls.manager.pki
        cls.password = 'isolated-nginx-fixture-long-passphrase'
        cls.pki.create('127.0.0.1', cls.password)
        cls.plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': '127.0.0.1', 'port': cls.endpoint_port,
                    'https': True, 'ipv6': False, 'deployment': 'octane',
                    'backend': f'http://127.0.0.1:{cls.backend.server_port}',
                    'ws_backend': f'http://127.0.0.1:{cls.backend.server_port}', 'config_path': str(cls.config)}

    @classmethod
    def workspace_owned(cls):
        root = cls.root
        if root.is_symlink() or root.resolve() != root or root.parent != Path('/var/lib') \
                or not root.name.startswith('node-endpoint-nginx-integration-'):
            raise SafetyError('Refusing cleanup: fixture path ownership differs')
        item = root.stat()
        if (item.st_dev, item.st_ino) != cls.root_identity or item.st_uid != 0 or item.st_mode & 0o077:
            raise SafetyError('Refusing cleanup: fixture directory identity/permissions differ')
        marker = root / '.fixture-owner.json'
        if marker.is_symlink() or read_json(marker) != {'tool': 'NodeEndpoint Linux integration',
                'token': cls.fixture_token, 'path': str(root)}:
            raise SafetyError('Refusing cleanup: fixture ownership marker differs')

    @classmethod
    def fixture_master(cls, info):
        if not info or info['state'] == 'Z' or info.get('exe') != getattr(cls, 'nginx_identity', None):
            return False
        if not re.match(r'^(nginx|openresty): master process ', info.get('command', '')):
            return False
        words = shlex.split(re.sub(r'^(nginx|openresty): master process ', '', info['command']))
        try:
            config = words[words.index('-c') + 1]
            prefix = words[words.index('-p') + 1]
            return Path(config).resolve() == cls.main.resolve() and Path(prefix).resolve() == cls.root
        except (ValueError, IndexError):
            return False

    @classmethod
    def capture_fixture_processes(cls):
        infos = {}
        for folder in Path('/proc').iterdir():
            if folder.name.isdigit():
                info = process_info(folder.name)
                if info and info['state'] != 'Z' and info.get('exe') == getattr(cls, 'nginx_identity', None):
                    infos[info['pid']] = info
        own = {pid for pid, info in infos.items() if cls.fixture_master(info)}
        # A worker's config is not in its process title. Capture it only as an
        # observed descendant of the verified master, with the same executable.
        while True:
            children = {pid for pid, info in infos.items() if info['parent'] in own}
            if children.issubset(own):
                break
            own.update(children)
        cls.owned_processes.update({pid: infos[pid] for pid in own})

    @classmethod
    def identity_alive(cls, expected):
        current = process_info(expected['pid'])
        if not current or current['state'] == 'Z' or current['start'] != expected['start']:
            return False
        if current.get('exe') != expected['exe']:
            raise SafetyError('Fixture process changed executable identity; retaining evidence')
        return True

    @classmethod
    def signal_owned(cls, expected, action):
        if not cls.identity_alive(expected):
            return
        try:
            fd = os.pidfd_open(expected['pid'])
        except ProcessLookupError:
            return
        try:
            # Recheck after opening the pidfd. Signals then target that exact
            # process instance even if a numerical PID is subsequently reused.
            if cls.identity_alive(expected):
                signal.pidfd_send_signal(fd, action)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)

    @classmethod
    def wait_fixture_exit(cls, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            cls.capture_fixture_processes()
            if not any(cls.identity_alive(info) for info in cls.owned_processes.values()):
                return True
            time.sleep(0.1)
        return not any(cls.identity_alive(info) for info in cls.owned_processes.values())

    @classmethod
    def cleanup_fixture(cls):
        """Registered before startup, so setup failures also invoke cleanup."""
        if cls.cleaned:
            return
        error = None
        try:
            cls.workspace_owned()
            cls.capture_fixture_processes()
            for info in list(cls.owned_processes.values()):
                if cls.fixture_master(info):
                    cls.signal_owned(info, signal.SIGQUIT)
            if not cls.wait_fixture_exit(5):
                for info in list(cls.owned_processes.values()):
                    cls.signal_owned(info, signal.SIGTERM)
                if not cls.wait_fixture_exit(5):
                    for info in list(cls.owned_processes.values()):
                        cls.signal_owned(info, signal.SIGKILL)
                    if not cls.wait_fixture_exit(3):
                        raise SafetyError('Fixture processes did not exit; retaining PID/config/logs')
        except Exception as exc:
            error = exc
        finally:
            try:
                if hasattr(cls, 'backend'):
                    if hasattr(cls, 'thread') and cls.thread.is_alive():
                        cls.backend.shutdown()
                    cls.backend.server_close()
                    if hasattr(cls, 'thread'):
                        cls.thread.join(timeout=3)
                        if cls.thread.is_alive():
                            raise SafetyError('Fixture HTTP server thread did not exit')
            except Exception as exc:
                error = error or exc
        if error:
            # Write only after revalidating ownership. An altered directory is
            # left completely untouched and its path is reported to the operator.
            try:
                cls.workspace_owned()
                write_json(cls.root / 'CLEANUP_REQUIRED.json', {'error_type': type(error).__name__,
                           'fixture_pids': list(cls.owned_processes), 'path': str(cls.root)})
            except Exception:
                pass
            raise SafetyError('Fixture cleanup failed; evidence retained at ' + str(cls.root)) from error
        cls.workspace_owned()
        try:
            shutil.rmtree(cls.root)
        except OSError as exc:
            raise SafetyError('Fixture processes stopped but file cleanup failed; remaining evidence at ' + str(cls.root)) from exc
        cls.cleaned = True

    def test_01_actual_environment_and_install_idempotence(self):
        self.assertIn(str(self.site), self.env.files)
        self.assertIn(str(self.sites) + '/*.conf', self.env.includes)
        self.manager.transaction(lambda: None, self.plan)
        self.assertEqual(local_probe(self.plan, self.pki)['tls'], 'verified')
        self.assertFalse(self.manager.transaction(lambda: None, self.plan)['changed'])
        self.assertEqual(self.site.read_bytes(), self.original_bytes)
        request = http.client.HTTPConnection('127.0.0.1', self.original_port, timeout=5)
        request.request('GET', '/')
        response = request.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b'original-website')
        request.close()

    def test_02_precise_restrictions_and_verified_wss(self):
        context = ssl.create_default_context(cafile=str(self.pki.ca_dir() / 'ca.pem'))
        request = http.client.HTTPSConnection('127.0.0.1', self.endpoint_port, context=context, timeout=5)
        request.request('GET', '/admin')
        response = request.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        request.request('GET', '/api/v2/server/machine/nodes')
        response = request.getresponse()
        self.assertEqual(response.status, 405)
        response.read()
        request.request('GET', '/ws', headers={'Upgrade': 'websocket', 'Connection': 'Upgrade',
                        'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', 'Sec-WebSocket-Version': '13'})
        response = request.getresponse()
        self.assertEqual(response.status, 101)
        self.assertEqual(response.getheader('Sec-WebSocket-Accept'), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
        request.close()

    def test_03_actual_listener_conflict(self):
        self.env.refresh()
        with self.assertRaises(SafetyError):
            check_port(self.original_port, self.env.nodes, self.env.ss)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.env.refresh()
            with self.assertRaises(SafetyError):
                check_port(listener.getsockname()[1], [], self.env.ss)

    def test_04_real_nginx_t_failure_restores_pointer_and_config(self):
        original_meta = self.pki.meta
        original_config = self.config.read_bytes()
        with patch('manager.render', return_value='invalid-node-endpoint-directive;\n'):
            with self.assertRaises(SafetyError):
                self.manager.transaction(lambda: self.pki.renew('127.0.0.1', self.password))
        self.assertEqual(self.pki.meta, original_meta)
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual(local_probe(self.plan, self.pki)['tls'], 'verified')
        self.assertEqual(self.site.read_bytes(), self.original_bytes)

    def test_05_real_reload_failure_preserves_recovery_evidence(self):
        original_meta = self.pki.meta
        original_config = self.config.read_bytes()
        pid_file = self.root / 'nginx.pid'
        pid_bytes = pid_file.read_bytes()
        pid_file.write_text('99999999\n')  # Actual nginx -s reload fails; master itself stays running.
        try:
            with self.assertRaisesRegex(SafetyError, 'recovery evidence'):
                self.manager.transaction(lambda: self.pki.renew('127.0.0.1', self.password))
            self.assertEqual(self.pki.meta, original_meta)
            self.assertEqual(self.config.read_bytes(), original_config)
            self.assertTrue(list((self.manager.state / 'backups').glob('*/RECOVERY_REQUIRED.json')))
        finally:
            pid_file.write_bytes(pid_bytes)
            self.env.nginx(['-t'])
            self.env.nginx(['-s', 'reload'])
            time.sleep(0.3)
        self.assertEqual(local_probe(self.plan, self.pki)['tls'], 'verified')

    def test_06_same_ca_key_replacement_uninstall_and_restore(self):
        ca = self.pki.status()['ca']['sha256']
        previous_key = (self.pki.leaf_dir() / 'server.key.pem').read_bytes()
        self.manager.transaction(lambda: self.pki.renew('127.0.0.1', self.password))
        self.assertEqual(self.pki.status()['ca']['sha256'], ca)
        self.assertNotEqual((self.pki.leaf_dir() / 'server.key.pem').read_bytes(), previous_key)
        self.assertEqual(local_probe(self.plan, self.pki)['tls'], 'verified')
        self.manager.transaction(lambda: None, remove=True)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.site.read_bytes(), self.original_bytes)
        self.manager.rollback()
        for _ in range(20):
            try:
                self.assertEqual(local_probe(self.plan, self.pki)['tls'], 'verified')
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail('Independent entry not restored after rollback')


if __name__ == '__main__':
    unittest.main(verbosity=2)
