"""Managed credential recovery and native legacy compatibility, no real panel."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
import manager
import panel_passphrase as vault
from pki import SafetyError


class Credentials:
    def __init__(self, state):
        self.state, self.rows, self.fail_bind = state, {}, False
        self.calls = []

    def call(self, action, identity, host=None, fingerprint=None):
        self.calls.append((action, identity, host, fingerprint))
        if action == 'prepare':
            data = json.loads((self.state / 'pki' / vault.FILENAME).read_text())
            assert data['pending']['credential_id'] == identity
            self.rows.setdefault(identity, hashlib.sha256(identity.encode()).hexdigest())
        if action in ('prepare', 'get'):
            return {'credential_id': identity, 'passphrase': self.rows[identity]}
        if self.fail_bind:
            raise RuntimeError('synthetic-private-error-' + self.rows[identity])
        return {'status': 'stored', 'credential_id': identity, 'fingerprint': fingerprint}


class FakePKI:
    def __init__(self, state):
        self.state, self.root, self.meta = state, state / 'pki', {}
        self.openssl = SimpleNamespace(key_matches=Mock(), info=lambda path: {'sha256': path.parent.name * 4})
        self.created = []

    def ca_dir(self, identity):
        return self.root / 'ca' / identity

    def create(self, host, password):
        self.created.append(password)
        self.meta = {'active_ca': 'a' * 16, 'host': host, 'rotation': None}
        return self.status()

    def prepare_rotation(self, password):
        self.created.append(password)
        self.meta['rotation'] = {'new_ca': 'b' * 16}
        return self.status()

    def status(self):
        return {'initialized': bool(self.meta)}


class ManagedCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        (self.state / 'pki').mkdir(mode=0o700)
        self.bridge = Credentials(self.state)
        self.pki = FakePKI(self.state)
        self.plan = {'deployment': 'fpm', 'app': str(self.state / 'app'), 'app_user': 'www', 'php': str(self.state / 'php')}
        trusted = patch.object(manager, 'trusted_file', side_effect=Path)
        trusted.start(); self.addCleanup(trusted.stop)
        private = patch.object(vault.ManagedPassphrases, '_private_parent')
        private.start(); self.addCleanup(private.stop)

    def managed(self):
        return vault.ManagedPassphrases(self.state, self.plan, self.bridge)

    def test_random_password_is_saved_before_signing_and_only_ids_remain_in_root_state(self):
        managed = self.managed()
        managed.create(self.pki, '192.0.2.10')
        self.assertEqual([call[0] for call in self.bridge.calls], ['prepare', 'bind'])
        self.pki.openssl.key_matches.assert_called_once()
        data = json.loads(managed.path.read_text())
        self.assertIsNone(data['pending'])
        self.assertIn('a' * 16, data['cas'])
        for password in self.bridge.rows.values():
            self.assertNotIn(password, managed.path.read_text())
        self.assertEqual(managed.password(self.pki), self.pki.created[0])

    def test_bind_failure_recovers_same_secret_without_creating_another_ca(self):
        self.bridge.fail_bind = True
        managed = self.managed()
        with self.assertRaisesRegex(SafetyError, vault.FAILURE) as failure:
            managed.create(self.pki, '192.0.2.10')
        self.assertNotIn(self.pki.created[0], str(failure.exception))
        self.bridge.fail_bind = False
        resumed = self.managed()
        self.assertTrue(resumed.recover_pending(self.pki))
        self.assertEqual(len(self.pki.created), 1)
        self.assertEqual(resumed.password(self.pki), self.pki.created[0])
        self.assertEqual(len(self.bridge.rows), 1)

    def test_prepare_failure_leaves_reusable_operation_id_before_any_ca(self):
        managed = self.managed()
        with patch.object(self.bridge, 'call', side_effect=RuntimeError('synthetic private application output')):
            with self.assertRaisesRegex(SafetyError, vault.FAILURE):
                managed.create(self.pki, '192.0.2.10')
        identity = json.loads(managed.path.read_text())['pending']['credential_id']
        self.assertFalse(self.pki.meta)
        resumed = self.managed()
        resumed.create(self.pki, '192.0.2.10')
        self.assertEqual(resumed.data['cas']['a' * 16]['credential_id'], identity)

    def test_existing_ca_never_prepares_a_replacement_password(self):
        self.pki.create('192.0.2.10', 'manually-entered-legacy-password')
        managed = self.managed()
        self.assertIsNone(managed.password(self.pki))
        with self.assertRaises(SafetyError):
            managed.create(self.pki, '192.0.2.10')
        self.assertFalse(self.bridge.calls)
        self.assertEqual(len(self.pki.created), 1)

    def test_rotation_retains_two_independent_credentials_and_retry_keeps_ca(self):
        managed = self.managed(); managed.create(self.pki, '192.0.2.10')
        first = managed.password(self.pki)
        managed.prepare_rotation(self.pki)
        second = managed.password(self.pki, 'b' * 16)
        self.assertNotEqual(first, second)
        managed.prepare_rotation(self.pki)
        self.assertEqual(len(self.pki.created), 2)
        self.assertEqual(len(managed.data['cas']), 2)

    def test_modified_context_and_corrupt_metadata_fail_closed(self):
        managed = self.managed(); managed.create(self.pki, '192.0.2.10')
        with self.assertRaises(SafetyError):
            vault.ManagedPassphrases(self.state, {**self.plan, 'app_user': 'another-user'}, self.bridge)
        before = managed.path.read_bytes()
        managed.path.write_text('{bad')
        with self.assertRaises(SafetyError):
            self.managed()
        self.assertEqual(managed.path.read_text(), '{bad')
        managed.path.write_bytes(before)

    def test_legacy_password_prompt_remains_but_managed_errors_do_not_prompt(self):
        prompt, callback = Mock(return_value='legacy'), Mock(return_value='done')
        self.assertEqual(vault.with_password(self.pki, callback, prompt), 'done')
        callback.assert_called_once_with('legacy'); prompt.assert_called_once()
        with patch.object(vault, 'existing_password', side_effect=SafetyError('unavailable')):
            with self.assertRaises(SafetyError):
                vault.with_password(self.pki, callback, Mock(side_effect=AssertionError('must not prompt')))

    def test_docker_auto_mode_is_refused_before_credentials_are_created(self):
        with self.assertRaisesRegex(SafetyError, 'manual-ca-passphrase'):
            vault.ManagedPassphrases(self.state, {**self.plan, 'deployment': 'docker'}, self.bridge)
        self.assertFalse(self.bridge.calls)


if __name__ == '__main__':
    unittest.main()
