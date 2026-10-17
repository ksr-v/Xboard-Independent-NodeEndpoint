"""Public-only Root payload checks; PHP/application writes are verified on Linux."""
import hashlib
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
import manager
import panel_ca
from pki import SafetyError


class PanelCaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.trusted = patch.object(manager, 'trusted_file', side_effect=lambda path, *args: Path(path))
        self.trusted.start()
        self.addCleanup(self.trusted.stop)
        self.safe = patch.object(manager, 'safe_state', side_effect=lambda path: Path(path))
        self.safe.start()
        self.addCleanup(self.safe.stop)
        # A syntactic PEM stub tests payload construction; real X509 validation is
        # covered by the unchanged plugin validator and the Linux application test.
        self.cert = '-----BEGIN CERTIFICATE-----\nY2VydGlmaWNhdGUtZml4dHVyZQ==\n-----END CERTIFICATE-----\n'
        ca = self.state / 'pki/ca/1111111111111111'
        ca.mkdir(parents=True)
        (ca / 'ca.pem').write_text(self.cert)
        (ca / 'ca.key.pem').write_text('PRIVATE KEY MUST NEVER BE READ OR SENT')
        (self.state / 'pki/trust-bundle.pem').write_text(self.cert)
        (self.state / 'pki/state.json').write_text(json.dumps({'trusted_ca': ['1111111111111111']}))
        (self.state / manager.STATE_MARKER).write_text(json.dumps({'tool': 'NodeEndpoint', 'path': str(self.state), 'schema_version': 1}))
        self.config = self.state / 'managed.conf'
        self.config.write_bytes(b'# ownership ' + b'a' * 32)
        self.plan = {'host': '127.0.0.1', 'port': 8443, 'https': True, 'deployment': 'fpm'}
        self.installation = {'ownership': 'a' * 32, 'config_path': str(self.config),
                             'config_sha256': hashlib.sha256(self.config.read_bytes()).hexdigest(), 'plan': self.plan}
        self.save()

    def save(self):
        (self.state / 'installation.json').write_text(json.dumps(self.installation))

    def test_payload_contains_only_public_certificates_pins_and_endpoint(self):
        plan, result = panel_ca.payload_from_state(self.state)
        self.assertEqual(plan, self.plan)
        self.assertEqual(set(result), {'pem', 'fingerprints', 'endpoint'})
        self.assertEqual(result['fingerprints'], [hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.cert)).hexdigest()])
        self.assertNotIn('PRIVATE KEY', json.dumps(result))

    def test_bundle_mismatch_refused(self):
        (self.state / 'pki/trust-bundle.pem').write_text('changed')
        with self.assertRaisesRegex(SafetyError, 'bundle differs'):
            panel_ca.payload_from_state(self.state)

    def test_private_material_in_ca_file_refused(self):
        (self.state / 'pki/ca/1111111111111111/ca.pem').write_text(self.cert + 'PRIVATE KEY')
        with self.assertRaisesRegex(SafetyError, 'Only public'):
            panel_ca.payload_from_state(self.state)

    def test_changed_owned_config_refused(self):
        self.config.write_text('external configuration')
        with self.assertRaisesRegex(SafetyError, 'changed externally'):
            panel_ca.payload_from_state(self.state)

    def test_unsupported_http_and_container_not_guessed(self):
        for update in ({'https': False}, {'https': True, 'deployment': 'docker'}):
            self.plan.update(update)
            self.save()
            with self.assertRaises(SafetyError):
                panel_ca.payload_from_state(self.state)

    def test_foreign_state_marker_refused(self):
        (self.state / manager.STATE_MARKER).write_text(json.dumps({'tool': 'foreign'}))
        with self.assertRaisesRegex(SafetyError, 'ownership marker'):
            panel_ca.payload_from_state(self.state)

    def test_duplicate_ca_ids_refused(self):
        (self.state / 'pki/state.json').write_text(json.dumps({'trusted_ca': ['1111111111111111'] * 2}))
        with self.assertRaisesRegex(SafetyError, 'trust state'):
            panel_ca.payload_from_state(self.state)

    def test_root_gate_precedes_privileged_imports_or_writes(self):
        with patch.object(manager, 'linux_root', side_effect=SafetyError('Root required')):
            with self.assertRaisesRegex(SafetyError, 'Root required'):
                panel_ca.sync_panel_ca(self.state)


if __name__ == '__main__':
    unittest.main()
