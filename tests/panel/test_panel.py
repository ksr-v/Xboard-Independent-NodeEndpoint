"""Real OpenSSL/TLS tests plus explicitly isolated Nginx transaction unit tests.
Run: python3 tests/panel/test_panel.py (OPENSSL_BINARY may select a local binary).
"""
import datetime as dt
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools/panel'))
from pki import PKI, SafetyError, UTC, atomic_write, ten_years, validate_host, write_json
from nginx import API_METHODS, check_port, dump_files, expanded, http_include_patterns, literal_loopback_backend, parse, render, sha256, verify_routes, verify_proxy_trust
from manager import Manager, STATE_MARKER, active_master, assert_export_destination, initialize_state, local_probe, trusted_file

OPENSSL = os.environ.get('OPENSSL_BINARY', 'openssl')
PASSWORD = 'test-only-random-like-long-passphrase'


class ValidationTests(unittest.TestCase):
    def test_bare_addresses(self):
        for host, kind in [('192.0.2.1', 'IP'), ('2001:db8::10', 'IP'), ('node.example', 'DNS'),
                           ('singlelabel', 'DNS'), ('xn--fiqs8s.example', 'DNS')]:
            self.assertEqual(validate_host(host)[1], kind)
        self.assertEqual(validate_host('NODE.EXAMPLE.'), ('node.example', 'DNS'))

    def test_bad_hosts(self):
        for host in ('https://example.com', '[::1]', '999.1.1.1', '1.2.3', 'bad_host', '-bad.com',
                     'example.com:8443', 'node.example/path', 'a..b', 'node.example..', 'a b', '中文.example', ''):
            with self.subTest(host=host), self.assertRaises(SafetyError):
                validate_host(host)

    def test_calendar_ten_years(self):
        value = dt.datetime(2028, 2, 29, 12, tzinfo=UTC)
        self.assertEqual(ten_years(value), dt.datetime(2038, 2, 28, 12, tzinfo=UTC))


class StateOwnershipUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_claim_new_state_and_repeat_preserves_marker(self):
        state = initialize_state(self.root / 'dedicated')
        marker = (state / STATE_MARKER).read_bytes()
        initialize_state(state)
        self.assertEqual((state / STATE_MARKER).read_bytes(), marker)
        self.assertTrue((state / 'pki').is_dir())
        self.assertTrue((state / 'backups').is_dir())
    def test_foreign_directory_refused_before_chmod(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        (foreign / 'original.conf').write_text('unchanged foreign bytes')
        with patch('manager.os.chmod') as chmod:
            with self.assertRaises(SafetyError):
                initialize_state(foreign)
            chmod.assert_not_called()
        self.assertEqual((foreign / 'original.conf').read_text(), 'unchanged foreign bytes')
        self.assertFalse((foreign / STATE_MARKER).exists())
    def test_copied_state_marker_refused(self):
        first = initialize_state(self.root / 'first')
        second = self.root / 'second'
        second.mkdir()
        atomic_write(second / STATE_MARKER, (first / STATE_MARKER).read_bytes())
        with self.assertRaises(SafetyError):
            initialize_state(second)
        self.assertFalse((second / 'pki').exists())
    def test_existing_private_object_must_be_directory(self):
        state = initialize_state(self.root / 'dedicated')
        (state / 'pki').rmdir()
        (state / 'pki').write_text('foreign file')
        with self.assertRaises(SafetyError):
            initialize_state(state)
        self.assertEqual((state / 'pki').read_text(), 'foreign file')
    def test_secure_config_below_writable_ancestor_refused(self):
        original, selected, ancestor = Mock(), Mock(), Mock()
        original.lstat.return_value = Mock(st_uid=0, st_mode=0o120777)
        original.is_symlink.return_value = True
        original.parents = []
        original.resolve.return_value = selected
        selected.stat.return_value = Mock(st_uid=0, st_mode=0o100644)
        ancestor.stat.return_value = Mock(st_uid=0, st_mode=0o40777)
        selected.parents = [ancestor]
        with patch('manager.Path') as path:
            path.return_value.absolute.return_value = original
            with self.assertRaises(SafetyError):
                trusted_file('/fixture/secure.conf')
    def test_secure_symlink_target_cannot_hide_replaceable_original_path(self):
        original, ancestor, selected = Mock(), Mock(), Mock()
        original.lstat.return_value = Mock(st_uid=0, st_mode=0o120777)
        original.is_symlink.return_value = True
        original.parents = [ancestor]
        original.resolve.return_value = selected
        ancestor.lstat.return_value = Mock(st_uid=0, st_mode=0o40770)
        ancestor.is_symlink.return_value = False
        selected.stat.return_value = Mock(st_uid=0, st_mode=0o100600)
        selected.parents = []
        with patch('manager.Path') as path:
            path.return_value.absolute.return_value = original
            with self.assertRaises(SafetyError):
                trusted_file('/replaceable/root-owned-link.conf')
        original.resolve.assert_not_called()


class MasterSelectionUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.website = self.root / 'nginx'
        self.panel = self.root / 'webserver'
        self.website.write_bytes(b'isolated website executable identity')
        self.panel.write_bytes(b'isolated control-panel executable identity')
    def tearDown(self):
        self.temp.cleanup()
    def proc(self, rows):
        class Folder:
            def __init__(self, pid, executable):
                self.name, self.executable = str(pid), executable
            def __truediv__(self, name):
                if name == 'exe':
                    return self.executable
                return Mock(read_bytes=Mock(return_value=('nginx: master process ' + str(self.executable)).encode()))
        return Mock(iterdir=Mock(return_value=[Folder(pid, executable) for pid, executable in rows]))
    def select(self, rows, selected=None):
        proc, real_path = self.proc(rows), Path
        with patch('manager.Path', side_effect=lambda value: proc if str(value) == '/proc' else real_path(value)), \
                patch('manager.command') as command:
            result = active_master(selected)
            command.assert_not_called()  # Inspection never stops/reloads either master.
            return result
    def test_explicit_website_master_preserves_control_panel(self):
        rows = [(22578, self.panel), (41071, self.website)]
        self.assertEqual(self.select(rows, self.website)['pid'], 41071)
        self.assertEqual(self.select(rows, self.panel)['pid'], 22578)
        self.assertEqual(self.panel.read_bytes(), b'isolated control-panel executable identity')
    def test_multiple_unselected_or_same_executable_masters_refused(self):
        with self.assertRaises(SafetyError):
            self.select([(22578, self.panel), (41071, self.website)])
        with self.assertRaises(SafetyError):
            self.select([(41071, self.website), (41072, self.website)], self.website)
        alias = self.root / 'nginx-hardlink-alias'
        alias.hardlink_to(self.website)
        with self.assertRaises(SafetyError):
            self.select([(41071, self.website), (41072, alias)], self.website)


class PKITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='node-endpoint-pki-test-')
        cls.path = Path(cls.temp.name)
        cls.pki = PKI(cls.path / 'state', OPENSSL)
        cls.initial = cls.pki.create('127.0.0.1', PASSWORD)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_ten_year_dates_aligned(self):
        root, leaf = self.initial['ca'], self.initial['server']
        self.assertEqual(root['not_after'], leaf['not_after'])
        issued = dt.datetime.fromisoformat(root['not_before']) + dt.timedelta(minutes=5)
        self.assertEqual(dt.datetime.fromisoformat(root['not_after']), ten_years(issued))
        self.assertGreaterEqual(root['remaining_days'], 3651)

    def test_ca_extensions_and_encrypted_key(self):
        text = self.pki.openssl.run(['x509', '-in', self.pki.ca_dir() / 'ca.pem', '-noout', '-text']).decode()
        self.assertIn('CA:TRUE, pathlen:0', text)
        self.assertIn('Certificate Sign, CRL Sign', text)
        self.assertIn('ENCRYPTED PRIVATE KEY', (self.pki.ca_dir() / 'ca.key.pem').read_text())

    def test_leaf_extensions_ip_san(self):
        text = self.pki.openssl.run(['x509', '-in', self.pki.leaf_dir() / 'server.pem', '-noout', '-text']).decode()
        for expected in ('CA:FALSE', 'TLS Web Server Authentication', 'IP Address:127.0.0.1', 'Digital Signature'):
            self.assertIn(expected, text)
        self.pki.openssl.verify(self.pki.leaf_dir() / 'server.pem', self.pki.ca_dir() / 'ca.pem', '127.0.0.1')

    def test_wrong_san_refused(self):
        with self.assertRaises(SafetyError):
            self.pki.openssl.verify(self.pki.leaf_dir() / 'server.pem', self.pki.ca_dir() / 'ca.pem', '127.0.0.2')

    def test_expired_and_future_refused(self):
        cert, ca = self.pki.leaf_dir() / 'server.pem', self.pki.ca_dir() / 'ca.pem'
        for date in (dt.datetime.fromisoformat(self.initial['server']['not_after']) + dt.timedelta(seconds=1),
                     dt.datetime.fromisoformat(self.initial['server']['not_before']) - dt.timedelta(seconds=1)):
            with self.subTest(date=date), self.assertRaises(SafetyError):
                self.pki.openssl.verify(cert, ca, '127.0.0.1', date.timestamp())

    def test_wrong_ca_refused(self):
        other = PKI(self.path / 'wrong-ca', OPENSSL)
        other.create('127.0.0.1', PASSWORD)
        with self.assertRaises(SafetyError):
            self.pki.openssl.verify(self.pki.leaf_dir() / 'server.pem', other.ca_dir() / 'ca.pem', '127.0.0.1')

    def test_reminders_180_90_30(self):
        cert = self.pki.leaf_dir() / 'server.pem'
        end = dt.datetime.fromisoformat(self.initial['server']['not_after'])
        for days in (180, 90, 30):
            info = self.pki.openssl.info(cert, end - dt.timedelta(days=days))
            self.assertEqual(info['reminder_days'], days)

    def test_dns_ipv6_and_renewal_under_same_ca(self):
        ca_fp = self.initial['ca']['sha256']
        old_leaf = self.pki.meta['active_leaf']
        old_key = (self.pki.leaf_dir(old_leaf) / 'server.key.pem').read_bytes()
        for host in ('node.example', '2001:db8::10'):
            leaf = self.pki._new_leaf(self.pki.meta['active_ca'], host, PASSWORD)
            self.pki.openssl.verify(self.pki.leaf_dir(leaf) / 'server.pem', self.pki.ca_dir() / 'ca.pem', host)
        self.pki.renew('127.0.0.1', PASSWORD)
        self.assertEqual(self.pki.status()['ca']['sha256'], ca_fp)
        self.assertNotEqual(old_key, (self.pki.leaf_dir() / 'server.key.pem').read_bytes())
        self.assertLessEqual(self.pki.status()['server']['not_after'], self.initial['ca']['not_after'])

    def test_offline_export_validation_and_renewal(self):
        pki = PKI(self.path / 'offline-state', OPENSSL)
        info = pki.create('127.0.0.1', PASSWORD)
        destination = self.path / 'offline-export'
        pki.export_ca(destination, PASSWORD)
        with self.assertRaises(SafetyError):
            pki.remove_online_key(destination, PASSWORD, '0' * 64)
        self.assertTrue((pki.ca_dir() / 'ca.key.pem').exists())
        key = (destination / 'ca.key.pem').read_bytes()
        atomic_write(destination / 'ca.key.pem', key + b'\n')
        with self.assertRaises(SafetyError):
            pki.verify_export(destination, PASSWORD, info['ca']['sha256'])
        atomic_write(destination / 'ca.key.pem', key)
        pki.remove_online_key(destination, PASSWORD, info['ca']['sha256'])
        self.assertFalse((pki.ca_dir() / 'ca.key.pem').exists())
        with self.assertRaises(SafetyError):
            pki.renew('127.0.0.1', PASSWORD)
        pki.renew('127.0.0.1', PASSWORD, destination / 'ca.key.pem')
        self.assertFalse((pki.ca_dir() / 'ca.key.pem').exists())

    def test_dual_ca_rotation_real_chain(self):
        pki = PKI(self.path / 'rotation-state', OPENSSL)
        initial = pki.create('127.0.0.1', PASSWORD)
        old_ca, old_leaf = pki.ca_dir(), pki.leaf_dir()
        pki.prepare_rotation(PASSWORD)
        rotation = pki.meta['rotation']
        new_fp = pki.openssl.info(pki.ca_dir(rotation['new_ca']) / 'ca.pem')['sha256']
        self.assertEqual(pki.meta['active_ca'], old_ca.name)
        self.assertEqual((pki.root / 'trust-bundle.pem').read_text().count('BEGIN CERTIFICATE'), 2)
        with self.assertRaises(SafetyError):
            pki.activate_rotation('0' * 64)
        pki.activate_rotation(new_fp)
        pki.openssl.verify(pki.leaf_dir() / 'server.pem', pki.root / 'trust-bundle.pem', '127.0.0.1')
        pki.openssl.verify(old_leaf / 'server.pem', pki.root / 'trust-bundle.pem', '127.0.0.1')
        with self.assertRaises(SafetyError):
            pki.openssl.verify(pki.leaf_dir() / 'server.pem', old_ca / 'ca.pem', '127.0.0.1')
        pki.finish_rotation(new_fp)
        self.assertEqual((pki.root / 'trust-bundle.pem').read_text().count('BEGIN CERTIFICATE'), 1)
        self.assertIsNone(pki.meta['rotation'])

    def test_actual_verified_tls_http_and_ws(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                if self.path == '/ws' and self.headers.get('Upgrade', '').lower() == 'websocket':
                    import base64, hashlib
                    accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] +
                        '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    self.send_response(101)
                    self.send_header('Upgrade', 'websocket')
                    self.send_header('Connection', 'Upgrade')
                    self.send_header('Sec-WebSocket-Accept', accept)
                else:
                    self.send_response(401)
                    self.send_header('Content-Type', 'application/json')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.pki.leaf_dir() / 'server.pem', self.pki.leaf_dir() / 'server.key.pem')
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            plan = {'host': '127.0.0.1', 'port': server.server_port, 'https': True}
            self.assertEqual(local_probe(plan, self.pki)['tls'], 'verified')
            client_context = ssl.create_default_context(cafile=str(self.pki.ca_dir() / 'ca.pem'))
            client = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=client_context)
            client.request('GET', '/ws', headers={'Upgrade': 'websocket', 'Connection': 'Upgrade',
                           'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', 'Sec-WebSocket-Version': '13'})
            response = client.getresponse()
            self.assertEqual(response.status, 101)
            self.assertEqual(response.getheader('Sec-WebSocket-Accept'), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
            client.close()
            with self.assertRaises(ssl.SSLCertVerificationError):
                http.client.HTTPSConnection('localhost', server.server_port, context=client_context).request('GET', '/')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class NginxUnitTests(unittest.TestCase):
    def test_quoted_json_punctuation_remains_literal_arguments(self):
        nodes = parse('http { log_format panel escape=json \'{\' \'"time":"$time_iso8601"\' \'}\' \';\'; '
                      'include /etc/nginx/sites/*.conf; server { listen 80; } }')
        children = nodes[0].children
        self.assertEqual(children[0].words, ['log_format', 'panel', 'escape=json', '{', '"time":"$time_iso8601"', '}', ';'])
        self.assertEqual(children[1].words, ['include', '/etc/nginx/sites/*.conf'])
        self.assertEqual(children[2].words, ['server'])
        self.assertEqual(children[2].children[0].words, ['listen', '80'])
    def test_http_include_context_and_quoted_comment(self):
        main = 'events {} http { include /etc/nginx/sites/*.conf; server { include /etc/nginx/private/*.conf; } }'
        files = {'/etc/nginx/nginx.conf': main, '/etc/nginx/sites/original.conf': 'server { listen 443 ssl; server_name original.example; }'}
        includes = http_include_patterns(parse(main), '/etc/nginx', files)
        self.assertEqual(includes, ['/etc/nginx/sites/*.conf'])
        nodes = expanded(parse(main), files, '/etc/nginx')
        with self.assertRaises(SafetyError):
            check_port(443, nodes, '')
        parse('http { log_format x "literal # quote"; } # comment\n')
        with self.assertRaises(SafetyError):
            parse('http { invalid')

    def test_loaded_dump_and_listeners(self):
        text = '# configuration file /etc/nginx/nginx.conf:\nevents {}\n# configuration file /etc/nginx/sites/site.conf:\nserver {}\n'
        self.assertEqual(len(dump_files(text)), 2)
        for address in ('0.0.0.0:8443', '[::]:8443'):
            with self.assertRaises(SafetyError):
                check_port(8443, [], 'LISTEN 0 100 ' + address + ' *:* users:(("other",pid=1,fd=2))')

    def test_route_profile_is_exact(self):
        profile = [{'uri': path[1:], 'method': '|'.join(methods)} for path, methods in API_METHODS.items()]
        verify_routes(profile)
        with self.assertRaises(SafetyError):
            verify_routes(profile[:-1])
        with self.assertRaises(SafetyError):
            verify_routes(profile + [profile[0]])
        self.assertNotIn('/api/v2/server/machine/config', API_METHODS)

    def test_only_loopback_literal_upstreams(self):
        self.assertEqual(literal_loopback_backend('http://127.0.0.1:8000')[1], 8000)
        for value in ('http://public.example:8000', 'http://[::1]:8000', 'http://localhost:8000',
                      'https://127.0.0.1:8000', 'http://127.0.0.1:8000/path', 'http://user:pass@127.0.0.1:8000'):
            with self.assertRaises(SafetyError):
                literal_loopback_backend(value)
    def test_unknown_trust_proxy_profile_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            path = app / 'app/Http/Middleware/TrustProxies.php'
            path.parent.mkdir(parents=True)
            path.write_text('<?php unknown_middleware();')
            with self.assertRaises(SafetyError):
                verify_proxy_trust(app)
        reference = REPO / '.audit/Xboard'
        if reference.exists():
            self.assertEqual(verify_proxy_trust(reference), 'f259274043cc68d609bb826d5efac4c2f547c734585dbd43462635304a18f5f3')

    def test_precise_route_and_websocket_configuration(self):
        plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': '2001:db8::1', 'port': 8443,
                'https': True, 'ipv6': True, 'deployment': 'octane', 'backend': 'http://127.0.0.1:8000',
                'ws_backend': 'http://127.0.0.1:8080'}
        text = render(plan, '/private/server.pem', '/private/key.pem')
        parse(text)
        self.assertEqual(text.count('location = /api/'), len(API_METHODS))
        self.assertIn('location = /ws', text)
        self.assertIn('location / { return 404; }', text)
        self.assertIn('access_log off;', text)
        self.assertNotIn('listen 443', text)
        self.assertIn('proxy_set_header Host $http_host;', text)
        self.assertIn('proxy_set_header X-Forwarded-Host $http_host;', text)
        self.assertIn('proxy_set_header Forwarded "";', text)
        self.assertIn('proxy_set_header X-Forwarded-Prefix "";', text)
        fpm = render({**plan, 'deployment': 'fpm', 'backend': 'unix:/run/php.sock', 'app': '/srv/xboard'},
                     '/private/server.pem', '/private/key.pem')
        self.assertIn('fastcgi_param HTTP_X_FORWARDED_HOST $http_host;', fpm)
        self.assertIn('fastcgi_param HTTP_X_FORWARDED_PROTO $scheme;', fpm)
        self.assertIn('fastcgi_param HTTP_FORWARDED "";', fpm)


class FakeEnvironment:
    """Unit fixture only; never claim that this exercises an actual Nginx process."""
    def __init__(self, path):
        self.path = path
        self.files = {}
        self.fail_reload_once = False
    def nginx(self, arguments):
        if arguments == ['-s', 'reload'] and self.fail_reload_once:
            self.fail_reload_once = False
            raise SafetyError('Unit injected reload failure')
    def refresh(self):
        self.files = {str(self.path): self.path.read_text()} if self.path.exists() else {}


class TransactionUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / 'original-site.conf'
        self.original.write_text('original unrelated website bytes\n')
        self.config = self.root / 'node-endpoint.conf'
        self.env = FakeEnvironment(self.config)
        self.manager = Manager(self.root / 'state', self.env, OPENSSL)
        self.plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': 'node.example', 'port': 8443,
                     'https': False, 'ipv6': False, 'deployment': 'octane', 'backend': 'http://127.0.0.1:8000',
                     'ws_backend': None, 'config_path': str(self.config)}
        self.probe = patch('manager.local_probe', return_value={'fixture': 'unit_only'})
        self.probe.start()
    def tearDown(self):
        self.probe.stop()
        self.temp.cleanup()
    def test_install_repeat_uninstall_and_restore_preserve_original(self):
        self.manager.transaction(lambda: None, self.plan)
        first = self.config.read_bytes()
        result = self.manager.transaction(lambda: None, self.plan)
        self.assertFalse(result['changed'])
        self.assertEqual(first, self.config.read_bytes())
        self.manager.transaction(lambda: None, remove=True)
        self.assertFalse(self.config.exists())
        self.manager.rollback()
        self.assertEqual(first, self.config.read_bytes())
        self.assertEqual(self.original.read_text(), 'original unrelated website bytes\n')
    def test_failed_reload_restores_config_metadata_and_certificate_pointer(self):
        self.manager.pki.status = lambda: {'initialized': False}  # Unit metadata fixture, not a PKI deployment.
        write_json(self.manager.pki.meta_path, {'test_certificate_pointer': 'old'})
        self.manager.transaction(lambda: None, self.plan)
        before = self.config.read_bytes()
        installed = (self.manager.state / 'installation.json').read_bytes()
        plan = {**self.plan, 'port': 9443}
        self.env.fail_reload_once = True
        with self.assertRaises(SafetyError):
            self.manager.transaction(lambda: write_json(self.manager.pki.meta_path, {'test_certificate_pointer': 'new'}), plan)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual((self.manager.state / 'installation.json').read_bytes(), installed)
        self.assertEqual(json.loads(self.manager.pki.meta_path.read_text())['test_certificate_pointer'], 'old')
    def test_external_config_change_refused(self):
        self.manager.transaction(lambda: None, self.plan)
        self.config.write_text('unrelated external replacement')
        with self.assertRaises(SafetyError):
            self.manager.transaction(lambda: None, self.plan)
        self.assertEqual(self.config.read_text(), 'unrelated external replacement')
    def test_noop_manual_backup_and_failure_preserve_last_change_rollback(self):
        self.manager.transaction(lambda: None, self.plan)
        original = self.config.read_bytes()
        second = {**self.plan, 'port': 9443}
        self.manager.transaction(lambda: None, second)
        pointer = (self.manager.state / 'last-backup').read_bytes()
        self.manager.transaction(lambda: None, second)
        self.manager.backup()
        self.env.fail_reload_once = True
        with self.assertRaises(SafetyError):
            self.manager.transaction(lambda: None, {**second, 'port': 9543})
        self.assertEqual((self.manager.state / 'last-backup').read_bytes(), pointer)
        self.manager.rollback()
        self.assertEqual(self.config.read_bytes(), original)
    def test_corrupt_late_backup_file_does_not_partially_restore(self):
        self.manager.transaction(lambda: None, self.plan)
        self.manager.transaction(lambda: None, {**self.plan, 'port': 9443})
        pointer = (self.manager.state / 'last-backup').read_text().strip()
        backup = self.manager.state / 'backups' / pointer
        (backup / 'public_status').write_text('corrupt later snapshot file')
        config, installation = self.config.read_bytes(), (self.manager.state / 'installation.json').read_bytes()
        with self.assertRaises(SafetyError):
            self.manager.restore(backup)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual((self.manager.state / 'installation.json').read_bytes(), installation)

    def test_uninstalled_application_still_protected_from_private_export(self):
        application = self.root / 'xboard-app'
        self.manager.transaction(lambda: None, {**self.plan, 'app': str(application)})
        self.manager.transaction(lambda: None, remove=True)
        self.assertFalse((self.manager.state / 'installation.json').exists())
        with self.assertRaises(SafetyError):
            assert_export_destination(self.manager.state, application / 'public' / 'offline-ca')
        assert_export_destination(self.manager.state, self.root / 'offline')

    def test_failed_rollback_and_rescue_preserve_recovery_marker(self):
        self.manager.transaction(lambda: None, self.plan)
        self.manager.transaction(lambda: None, {**self.plan, 'port': 9443})
        def failed_reload(arguments):
            if arguments == ['-s', 'reload']:
                raise SafetyError('Unit injected persistent reload failure')
        with patch.object(self.env, 'nginx', side_effect=failed_reload):
            with self.assertRaises(SafetyError):
                self.manager.rollback()
        self.assertTrue(list((self.manager.state / 'backups').glob('*/RECOVERY_REQUIRED.json')))
        self.assertEqual(self.manager.installed['plan']['port'], 9443)


if __name__ == '__main__':
    unittest.main(verbosity=2)
