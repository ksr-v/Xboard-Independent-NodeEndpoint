"""Real CA metadata checks and isolated execute tests for unchanged entry refreshes.

OpenSSL, metadata, certificate verification and signing stay real. The Nginx
environment and panel bridge are explicit local fixtures, with no host reload.
"""
from contextlib import ExitStack, contextmanager
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import manager
import nginx
import panel_passphrase as vault
from pki import PKI, SafetyError, write_json
from test_docker_context import context as docker_context

OPENSSL = shutil.which('openssl')
if not OPENSSL and Path('C:/Program Files/Git/mingw64/bin/openssl.exe').is_file():
    OPENSSL = 'C:/Program Files/Git/mingw64/bin/openssl.exe'
PASSWORD = 'T' * 64
HOST = '192.0.2.10'


class EntryEnvironment:
    def __init__(self, config):
        self.config, self.files, self.plan_value = config, {}, None

    def plan(self, *args):
        return self.plan_value

    def nginx(self, arguments):
        pass  # Unit boundary: no host Nginx or system service is called.

    def refresh(self):
        self.files = {str(self.config): self.config.read_text()} if self.config.exists() else {}


@unittest.skipUnless(OPENSSL, 'Real OpenSSL is required for public CA validation and signing')
class ExistingEntryRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.TemporaryDirectory()
        cls.template_state = Path(cls.template.name) / 'state'
        PKI(cls.template_state, OPENSSL).create(HOST, PASSWORD)

    @classmethod
    def tearDownClass(cls):
        cls.template.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'state'
        shutil.copytree(self.template_state, self.state)
        self.config = self.root / 'node-endpoint.conf'
        self.env = EntryEnvironment(self.config)
        self.pki = PKI(self.state, OPENSSL)
        self.pki.root.chmod(0o700)  # Match initialize_state's real private PKI directory.
        self.plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': HOST, 'port': 8443,
                     'https': True, 'ipv6': False, 'deployment': 'fpm', 'backend': 'unix:/run/php.sock',
                     'app': str(self.root / 'app'), 'app_user': 'www', 'php': str(self.root / 'php'),
                     'config_path': str(self.config), 'ws_backend': None, 'optional_api_paths': []}
        self.sidecar = self.pki.root / vault.FILENAME
        fingerprint = self.pki.openssl.info(self.pki.ca_dir() / 'ca.pem')['sha256']
        self.metadata = {'schema_version': 1, 'app': self.plan['app'], 'app_user': 'www',
                         'php': self.plan['php'], 'pending': None,
                         'cas': {self.pki.meta['active_ca']: {'credential_id': 'c' * 32,
                                  'host': HOST, 'fingerprint': fingerprint}}}
        write_json(self.sidecar, self.metadata)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(manager, 'trusted_file', side_effect=lambda path, *a: Path(path)))
        if os.name != 'posix':
            # Production is Linux. Normalize only fixture paths before the real
            # Nginx quoting validator; OpenSSL and certificate files stay real.
            quote = nginx.quote_path
            self.stack.enter_context(patch.object(nginx, 'quote_path', side_effect=lambda path: quote(Path(path).as_posix())))
        if os.name == 'posix' and os.geteuid() != 0:
            # File ownership is separately enforced by the production reader;
            # this portable test cannot manufacture root-owned temporary files.
            self.stack.enter_context(patch.object(vault.ManagedPassphrases, '_private_parent'))
        self.stack.enter_context(patch.object(manager, 'probe_after_reload', return_value={'fixture': True}))
        self.controller = manager.Manager(self.state, self.env, OPENSSL)
        self.controller.transaction(lambda: None, self.plan)

    def snapshot(self):
        return {path.relative_to(self.pki.root).as_posix(): path.read_bytes()
                for path in self.pki.root.rglob('*') if path.is_file()}

    @contextmanager
    def no_bridge(self):
        with patch.object(vault, 'PanelBridge', side_effect=AssertionError('Native bridge constructed')) as native, \
                patch.object(vault, 'DockerPanelBridge', side_effect=AssertionError('Docker bridge constructed')) as docker, \
                patch.object(vault, 'write_json', side_effect=AssertionError('Metadata write attempted')):
            yield native, docker

    @contextmanager
    def execute_context(self, action='modify', host=HOST):
        plan = {**self.plan, 'host': host,
                'optional_api_paths': ['/api/v2/server/device/acquire', '/api/v2/server/device/release']}
        self.env.plan_value = plan
        if action == 'renew':
            args = manager.parser().parse_args(['--state', str(self.state), '--openssl', OPENSSL,
                                                'renew', '--confirm', 'REPLACE SERVER KEY ' + HOST])
        else:
            args = manager.parser().parse_args(['--state', str(self.state), '--openssl', OPENSSL,
                action, '--host', host, '--port', '8443', '--include-dir', str(self.root),
                '--site-config', str(self.root / 'site.conf'), '--site-name', 'panel.example',
                '--app', self.plan['app'], '--app-user', 'www', '--php', self.plan['php'],
                '--deployment', 'fpm', '--backend', self.plan['backend'],
                '--confirm', 'INSTALL ' + host + ':8443'])
        with ExitStack() as stack:
            stack.enter_context(patch.object(manager, 'linux_root'))
            stack.enter_context(patch.object(manager, 'safe_state', side_effect=Path))
            stack.enter_context(patch.object(manager, 'initialize_state', return_value=self.state))
            stack.enter_context(patch.object(manager, 'Environment', return_value=self.env))
            stack.enter_context(patch.object(manager.os, 'fchmod', create=True))
            stack.enter_context(patch.object(manager.os, 'O_NOFOLLOW', getattr(manager.os, 'O_NOFOLLOW', 0), create=True))
            stack.enter_context(patch.dict(sys.modules, {'fcntl': SimpleNamespace(
                flock=lambda *a: None, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)}))
            stack.enter_context(patch.object(manager, 'passphrase', side_effect=AssertionError('Password prompt attempted')))
            output = stack.enter_context(patch('builtins.print'))
            yield args, output

    def test_same_host_execute_refreshes_routes_when_panel_get_fails_without_touching_pki(self):
        before = self.snapshot()
        with patch.object(vault.PanelBridge, 'call', side_effect=SafetyError(vault.FAILURE)) as bridge:
            with self.assertRaises(SafetyError):
                vault.existing_password(self.pki, self.plan)
            bridge.assert_called_once_with('get', 'c' * 32, HOST)
            bridge.reset_mock()
            with self.execute_context() as (args, _):
                self.assertEqual(manager.execute(args), 0)
            bridge.assert_not_called()
        self.assertIn('location = /api/v2/server/device/acquire', self.config.read_text())
        self.assertIn('location = /api/v2/server/device/release', self.config.read_text())
        self.assertEqual(self.snapshot(), before)

    def test_native_context_schema_fingerprint_and_unknown_rotation_fail_closed_without_bridge(self):
        mutations = [
            lambda data: data.update(app_user='another-user'),
            lambda data: data.update(app=str(self.root / 'another-app')),
            lambda data: data.update(php=str(self.root / 'another-php')),
            lambda data: data.update(schema_version=99),
            lambda data: data['cas'][self.pki.meta['active_ca']].update(fingerprint='f' * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(self.metadata)
                mutate(data)
                write_json(self.sidecar, data)
                before = self.snapshot()
                with self.no_bridge(), self.assertRaises(SafetyError):
                    vault.validate_existing_metadata(self.pki, self.plan)
                self.assertEqual(self.snapshot(), before)
        write_json(self.sidecar, self.metadata)
        meta = self.pki.meta
        for rotation in ('unknown', {'phase': 'unknown'},
                         {'phase': 'new_ca_active_dual_trust', 'old_ca': meta['active_ca'], 'new_ca': 'd' * 16}):
            with self.subTest(rotation=rotation):
                write_json(self.pki.meta_path, {**meta, 'rotation': rotation})
                before = self.snapshot()
                with self.no_bridge(), self.assertRaises(SafetyError):
                    vault.validate_existing_metadata(self.pki, self.plan)
                self.assertEqual(self.snapshot(), before)

    def test_pending_credentials_are_not_recovered_or_rebound_by_refresh(self):
        active = self.pki.meta['active_ca']
        for pending in ({'kind': 'create', 'credential_id': 'd' * 32, 'host': HOST, 'before_ca': None},
                        {'kind': 'rotation', 'credential_id': 'd' * 32, 'host': HOST, 'before_ca': active}):
            with self.subTest(kind=pending['kind']):
                write_json(self.sidecar, {**self.metadata, 'pending': pending})
                before = self.snapshot()
                with self.no_bridge(), self.assertRaisesRegex(SafetyError, '未完成的 CA 凭据操作'):
                    vault.validate_existing_metadata(self.pki, self.plan)
                self.assertEqual(self.snapshot(), before)

    def test_legacy_missing_sidecar_or_active_record_remains_read_only(self):
        self.sidecar.unlink()
        with self.no_bridge():
            self.assertIsNone(vault.validate_existing_metadata(self.pki, self.plan))
        self.assertFalse(self.sidecar.exists())
        write_json(self.sidecar, {**self.metadata, 'cas': {}})
        before = self.snapshot()
        with self.no_bridge():
            self.assertIsNone(vault.validate_existing_metadata(self.pki, self.plan))
        self.assertEqual(self.snapshot(), before)

    def test_prepared_rotation_with_complete_binding_can_refresh_current_ca_without_secrets(self):
        active = self.pki.meta['active_ca']
        write_json(self.pki.meta_path, {**self.pki.meta, 'rotation': {
            'phase': 'distribute_dual_trust', 'old_ca': active, 'new_ca': 'd' * 16}})
        before = self.snapshot()
        with self.no_bridge():
            self.assertTrue(vault.validate_existing_metadata(self.pki, self.plan))
        self.assertEqual(self.snapshot(), before)

    def test_docker_context_is_validated_offline_and_cannot_claim_another_binding(self):
        ctx = docker_context()
        data = {'schema_version': 2, 'docker_context': ctx, 'pending': None, 'cas': self.metadata['cas']}
        write_json(self.sidecar, data)
        before = self.snapshot()
        plan = {'deployment': 'docker', 'docker_context': ctx}
        with self.no_bridge():
            self.assertTrue(vault.validate_existing_metadata(self.pki, plan))
            for field, value in (('uid', 1001), ('container_id', 'd' * 64), ('working_dir', '/srv/another')):
                with self.subTest(field=field), self.assertRaises(SafetyError):
                    vault.validate_existing_metadata(self.pki, {**plan, 'docker_context': {**ctx, field: value}})
        self.assertEqual(self.snapshot(), before)

    def test_changed_host_and_renew_still_fail_when_real_credential_retrieval_fails(self):
        for action, host in (('modify', '192.0.2.20'), ('renew', HOST)):
            with self.subTest(action=action):
                before = self.snapshot()
                with patch.object(vault.PanelBridge, 'call', side_effect=SafetyError(vault.FAILURE)) as bridge:
                    with self.execute_context(action, host) as (args, _), self.assertRaises(SafetyError):
                        manager.execute(args)
                    bridge.assert_called_once_with('get', 'c' * 32, HOST)
                self.assertEqual(self.snapshot(), before)

    def test_changed_host_uses_retrieved_password_for_real_signing(self):
        active = self.pki.meta['active_ca']
        previous_leaf = self.pki.meta['active_leaf']
        metadata = self.sidecar.read_bytes()
        with patch.object(vault.PanelBridge, 'call', return_value={
                'credential_id': 'c' * 32, 'passphrase': PASSWORD}) as bridge:
            with self.execute_context(host='192.0.2.20') as (args, _):
                self.assertEqual(manager.execute(args), 0)
            bridge.assert_called_once_with('get', 'c' * 32, HOST)
        self.assertEqual(self.pki.meta['active_ca'], active)
        self.assertNotEqual(self.pki.meta['active_leaf'], previous_leaf)
        self.assertEqual(self.pki.meta['host'], '192.0.2.20')
        self.assertEqual(self.sidecar.read_bytes(), metadata)
        self.pki.openssl.verify(self.pki.leaf_dir() / 'server.pem', self.pki.ca_dir() / 'ca.pem', '192.0.2.20')

    def test_symlink_sidecar_is_rejected_without_following_or_modifying_it(self):
        target = self.root / 'original-metadata.json'
        self.sidecar.rename(target)
        try:
            self.sidecar.symlink_to(target)
        except OSError:
            self.skipTest('Host does not permit creating symlinks')
        before = target.read_bytes()
        with self.no_bridge(), self.assertRaises(SafetyError):
            vault.validate_existing_metadata(self.pki, self.plan)
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(self.sidecar.is_symlink())


if __name__ == '__main__':
    unittest.main(verbosity=2)
