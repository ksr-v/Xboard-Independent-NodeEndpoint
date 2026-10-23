"""Schema-2 credentials and reviewed container rebinding with real CA key proof."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_context
import manager
import panel_passphrase as vault
from pki import PKI, SafetyError, write_json
from test_docker_context import context

OPENSSL = shutil.which('openssl')
if not OPENSSL and Path('C:/Program Files/Git/mingw64/bin/openssl.exe').is_file():
    OPENSSL = 'C:/Program Files/Git/mingw64/bin/openssl.exe'


class MemoryPanel:
    """Application bridge substitute; cryptographic key verification stays real."""
    def __init__(self):
        self.rows, self.calls = {}, []
        self.error = None

    def call(self, action, identity, host=None, fingerprint=None):
        self.calls.append((action, identity, host, fingerprint))
        if self.error:
            raise self.error
        if action == 'prepare':
            self.rows.setdefault(identity, hashlib.sha256(identity.encode()).hexdigest())
        if action in ('prepare', 'get'):
            return {'credential_id': identity, 'passphrase': self.rows[identity]}
        return {'status': 'stored', 'credential_id': identity, 'fingerprint': fingerprint}

    def helper(self, candidate, name, payload):
        self.asserted_context = candidate
        assert name == 'panel_passphrase'
        return self.call(payload['action'], payload['credential_id'], payload.get('host'), payload.get('fingerprint'))


class FakeEnvironment:
    def __init__(self, config):
        self.config, self.files, self.fail_reload_once = config, {}, False

    def nginx(self, arguments):
        if arguments == ['-s', 'reload'] and self.fail_reload_once:
            self.fail_reload_once = False
            raise SafetyError('Injected reload failure after credential rebind')

    def refresh(self):
        self.files = {str(self.config): self.config.read_text()} if self.config.exists() else {}


@unittest.skipUnless(OPENSSL, 'Real OpenSSL required for encrypted Root CA key proof')
class DockerManagedCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'state'
        self.pki = PKI(self.state, OPENSSL)
        self.ctx = context()
        self.plan = {'deployment': 'docker', 'docker_context': self.ctx}
        self.bridge = MemoryPanel()
        for patcher in (patch.object(manager, 'trusted_file', side_effect=lambda path, *args: Path(path)),
                        patch.object(vault.ManagedPassphrases, '_private_parent'),
                        patch.object(docker_context, 'run_helper', side_effect=self.bridge.helper)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        managed = vault.ManagedPassphrases(self.state, self.plan, self.bridge)
        managed.create(self.pki, '192.168.128.128')
        return managed

    def replacement(self, **changes):
        return {**self.plan, 'docker_context': {**copy.deepcopy(self.ctx),
                'container_id': 'c' * 64, 'image_id': 'sha256:' + 'd' * 64, **changes}}

    def sidecar(self):
        return self.pki.root / vault.FILENAME

    def snapshot(self):
        return {str(path.relative_to(self.pki.root)): path.read_bytes()
                for path in self.pki.root.rglob('*') if path.is_file()}

    def test_schema2_creation_uses_existing_vault_and_never_stores_plaintext_secret_in_root_metadata(self):
        managed = self.create()
        self.assertEqual(managed.data['schema_version'], 2)
        self.assertEqual(managed.data['docker_context'], self.ctx)
        self.assertIsNone(managed.data['pending'])
        self.assertEqual(len(managed.data['cas']), 1)
        for password in self.bridge.rows.values():
            self.assertNotIn(password.encode(), self.sidecar().read_bytes())
        loaded = vault.ManagedPassphrases(self.state)
        self.assertIsInstance(loaded.bridge, vault.DockerPanelBridge)
        self.assertEqual(loaded.password(self.pki), next(iter(self.bridge.rows.values())))

    def test_same_context_is_noop_without_panel_password_lookup_or_sidecar_rewrite(self):
        self.create()
        before = self.snapshot()
        self.bridge.calls.clear()
        self.assertFalse(vault.rebind_docker_context(self.pki, self.plan))
        self.assertFalse(self.bridge.calls)
        self.assertEqual(before, self.snapshot())

    def test_new_container_and_image_rebind_only_context_after_actual_encrypted_ca_key_matches(self):
        self.create()
        before = self.snapshot()
        old = json.loads(self.sidecar().read_text())
        candidate = self.replacement()
        self.assertTrue(vault.rebind_docker_context(self.pki, candidate))
        changed = json.loads(self.sidecar().read_text())
        self.assertEqual(changed['docker_context'], candidate['docker_context'])
        self.assertEqual(changed['cas'], old['cas'])
        self.assertEqual(changed['pending'], old['pending'])
        after = self.snapshot()
        self.assertEqual({key: value for key, value in before.items() if key != vault.FILENAME},
                         {key: value for key, value in after.items() if key != vault.FILENAME})
        self.assertEqual(self.bridge.asserted_context, candidate['docker_context'])
        with self.assertRaises(SafetyError):
            vault.ManagedPassphrases(self.state, self.plan)

    def test_wrong_app_key_or_missing_application_credential_refuses_without_rebinding_or_leaking(self):
        self.create()
        before = self.sidecar().read_bytes()
        password = next(iter(self.bridge.rows.values()))
        for failure in (RuntimeError('APP_KEY decrypt failed: ' + password), KeyError('missing credential')):
            with self.subTest(failure=type(failure).__name__):
                self.bridge.error = failure
                with self.assertRaisesRegex(SafetyError, vault.FAILURE) as caught:
                    vault.rebind_docker_context(self.pki, self.replacement())
                self.assertNotIn(password, str(caught.exception))
                self.assertEqual(self.sidecar().read_bytes(), before)
        self.bridge.error = None

    def test_wrong_returned_password_cannot_unlock_real_ca_or_replace_binding(self):
        self.create()
        before = self.sidecar().read_bytes()
        identity = next(iter(self.bridge.rows))
        self.bridge.rows[identity] = 'Z' * 64
        with self.assertRaises(SafetyError) as caught:
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertNotIn('Z' * 64, str(caught.exception))
        self.assertEqual(self.sidecar().read_bytes(), before)

    def test_unrelated_real_private_key_is_not_accepted_even_with_same_password(self):
        self.create()
        other = PKI(Path(self.temporary.name) / 'other-state', OPENSSL)
        other.create('192.168.128.128', next(iter(self.bridge.rows.values())))
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement(), other.ca_dir() / 'ca.key.pem')
        self.assertEqual(self.sidecar().read_bytes(), before)

    def test_offline_key_needs_explicit_matching_original_key_and_never_recreates_ca(self):
        self.create()
        original = self.pki.ca_dir() / 'ca.key.pem'
        offline = Path(self.temporary.name) / 'offline-encrypted-ca.pem'
        original.rename(offline)
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertEqual(self.sidecar().read_bytes(), before)
        self.assertFalse(original.exists())
        self.assertTrue(vault.rebind_docker_context(self.pki, self.replacement(), offline))
        self.assertFalse(original.exists())

    def test_pending_operation_and_missing_trusted_ca_record_refuse_rebind(self):
        managed = self.create()
        managed.data['pending'] = {'kind': 'rotation', 'credential_id': 'e' * 32,
                                  'host': '192.168.128.128', 'before_ca': self.pki.meta['active_ca']}
        managed._save()
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertEqual(before, self.sidecar().read_bytes())
        managed.data['pending'] = None
        managed._save()
        metadata = self.pki.meta
        metadata['trusted_ca'].append('f' * 16)
        write_json(self.pki.meta_path, metadata)
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertEqual(before, self.sidecar().read_bytes())

    def test_changed_daemon_project_paths_mounts_or_numeric_identity_refuse_before_secret_lookup(self):
        self.create()
        mutations = [{'daemon_id': 'different-daemon-1234'}, {'project': 'other'},
                     {'working_dir': '/srv/other'}, {'uid': 1001}, {'gid': 1001},
                     {'bind_sources': {**self.ctx['bind_sources'], '/www/.env': '/srv/other/.env'}},
                     {'volume_sources': {'/data': 'other_redis-data'}}]
        for change in mutations:
            with self.subTest(change=change):
                before = self.sidecar().read_bytes()
                self.bridge.calls.clear()
                with self.assertRaises(SafetyError):
                    vault.rebind_docker_context(self.pki, self.replacement(**change))
                self.assertFalse(self.bridge.calls)
                self.assertEqual(before, self.sidecar().read_bytes())

    def test_empty_trust_list_cannot_skip_original_root_key_proof(self):
        self.create()
        metadata = self.pki.meta
        metadata['trusted_ca'] = []
        write_json(self.pki.meta_path, metadata)
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertEqual(before, self.sidecar().read_bytes())

    def test_rotation_checks_each_trusted_ca_before_committing_new_context(self):
        managed = self.create()
        managed.prepare_rotation(self.pki)
        self.bridge.calls.clear()
        self.assertTrue(vault.rebind_docker_context(self.pki, self.replacement()))
        self.assertEqual([call[0] for call in self.bridge.calls], ['get', 'get'])
        self.assertEqual(len(json.loads(self.sidecar().read_text())['cas']), 2)

    def test_schema1_native_records_still_load_but_cannot_be_silently_claimed_by_docker(self):
        native = {'deployment': 'fpm', 'app': str(Path(self.temporary.name) / 'app'),
                  'app_user': 'www', 'php': str(Path(self.temporary.name) / 'php')}
        managed = vault.ManagedPassphrases(self.state, native, self.bridge)
        managed.create(self.pki, '192.168.128.128')
        loaded = vault.ManagedPassphrases(self.state, native, self.bridge)
        self.assertEqual(loaded.data['schema_version'], 1)
        self.assertEqual(loaded.password(self.pki), next(iter(self.bridge.rows.values())))
        before = self.sidecar().read_bytes()
        with self.assertRaises(SafetyError):
            vault.rebind_docker_context(self.pki, self.replacement())
        self.assertEqual(before, self.sidecar().read_bytes())

    def test_real_manager_failed_reload_restores_old_sidecar_container_context_and_pki(self):
        self.create()
        config = Path(self.temporary.name) / 'entry.conf'
        environment = FakeEnvironment(config)
        controller = manager.Manager(self.state, environment, OPENSSL)
        plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': '192.168.128.128', 'port': 19843,
                'https': False, 'ipv6': False, 'deployment': 'docker', 'docker_context': self.ctx,
                'backend': 'http://127.0.0.1:17003', 'ws_backend': None, 'config_path': str(config)}
        with patch.object(manager, 'local_probe', return_value={'fixture': 'unit-only'}):
            controller.transaction(lambda: None, plan)
            before = self.snapshot()
            installed = (self.state / 'installation.json').read_bytes()
            environment.fail_reload_once = True
            candidate = {**plan, 'docker_context': self.replacement()['docker_context']}
            with self.assertRaisesRegex(SafetyError, 'previous configuration restored'):
                controller.transaction(lambda: vault.rebind_docker_context(controller.pki, candidate), candidate)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.state / 'installation.json').read_bytes(), installed)
        self.assertEqual(json.loads(self.sidecar().read_text())['docker_context'], self.ctx)


class DockerBridgeResponseTests(unittest.TestCase):
    def test_bridge_requires_exact_response_shape_matching_id_and_private_fixed_errors(self):
        bridge = vault.DockerPanelBridge(context())
        identity, secret = 'a' * 32, 'S' * 64
        with patch.object(docker_context, 'run_helper', return_value={'credential_id': identity, 'passphrase': secret}) as helper:
            self.assertEqual(bridge.call('get', identity, '192.168.128.128')['passphrase'], secret)
        self.assertEqual(helper.call_args.args, (context(), 'panel_passphrase',
                         {'action': 'get', 'credential_id': identity, 'host': '192.168.128.128'}))
        for response in ({'credential_id': 'b' * 32, 'passphrase': secret},
                         {'credential_id': identity, 'passphrase': secret, 'extra': secret},
                         {'credential_id': identity, 'passphrase': 'short'}, [], None):
            with self.subTest(response_type=type(response).__name__), patch.object(docker_context, 'run_helper', return_value=response):
                with self.assertRaisesRegex(SafetyError, vault.FAILURE) as caught:
                    bridge.call('get', identity)
                self.assertNotIn(secret, str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
