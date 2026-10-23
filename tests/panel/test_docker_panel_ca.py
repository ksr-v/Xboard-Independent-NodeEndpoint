"""Docker public CA import preserves the original Root lock and strict trust response."""
from contextlib import ExitStack
import copy
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import manager
import panel_ca
from pki import SafetyError
from test_docker_context import context
import test_panel_ca as native_ca_fixture


class DockerPanelCaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_ca_fixture.PanelCaTests(methodName='test_payload_contains_only_public_certificates_pins_and_endpoint')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ctx = context()
        self.fixture.plan.update(deployment='docker', docker_context=self.ctx, container=self.ctx['container_id'],
                                 backend='http://127.0.0.1:17001', app=self.ctx['working_dir'])
        self.fixture.save()

    def test_public_payload_supports_valid_docker_context_without_live_rebind_or_php(self):
        with patch('docker_context.inspect_context') as inspect, patch('panel_passphrase.rebind_docker_context') as rebind:
            plan, payload = panel_ca.payload_from_state(self.fixture.state)
        self.assertEqual(plan['docker_context'], self.ctx)
        self.assertNotIn('PRIVATE KEY', str(payload))
        inspect.assert_not_called()
        rebind.assert_not_called()

    def test_mismatched_plan_container_is_refused(self):
        self.fixture.plan['container'] = 'f' * 64
        self.fixture.save()
        with self.assertRaises(SafetyError):
            panel_ca.payload_from_state(self.fixture.state)

    def test_sync_runs_in_root_lock_uses_only_public_payload_and_never_rebinds(self):
        (self.fixture.state / 'manager.lock').touch()
        _, payload = panel_ca.payload_from_state(self.fixture.state)
        response = {'status': 'imported', 'fingerprints': payload['fingerprints'], 'certificates': 1, 'uid': 1000}
        events = []
        close = panel_ca.os.close
        with ExitStack() as stack:
            stack.enter_context(patch.object(manager, 'linux_root'))
            stack.enter_context(patch.object(panel_ca.os, 'O_NOFOLLOW', getattr(panel_ca.os, 'O_NOFOLLOW', 0), create=True))
            stack.enter_context(patch.object(panel_ca.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_nlink=1, st_mode=stat.S_IFREG | 0o600)))
            stack.enter_context(patch.dict(sys.modules, {'fcntl': SimpleNamespace(flock=lambda fd, operation: events.append(operation),
                LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)}))
            stack.enter_context(patch.object(panel_ca.os, 'close', side_effect=lambda fd: (events.append('close'), close(fd))[1]))
            helper = stack.enter_context(patch('docker_context.run_helper', side_effect=lambda *args: (events.append('helper'), response)[1]))
            native = stack.enter_context(patch.object(panel_ca.subprocess, 'run'))
            rebind = stack.enter_context(patch('panel_passphrase.rebind_docker_context'))
            result = panel_ca.sync_panel_ca(self.fixture.state)
        self.assertEqual(events, [3, 'helper', 'close'])
        self.assertEqual(result, {key: response[key] for key in ('status', 'fingerprints', 'certificates')})
        self.assertEqual(helper.call_args.args, (self.ctx, 'sync_panel_ca', payload))
        native.assert_not_called()
        rebind.assert_not_called()

    def test_wrong_status_uid_count_fingerprints_or_extra_output_is_refused(self):
        _, payload = panel_ca.payload_from_state(self.fixture.state)
        response = {'status': 'unchanged', 'fingerprints': payload['fingerprints'], 'certificates': 1, 'uid': 1000}
        for change in ({'status': 'success'}, {'uid': 0}, {'uid': None}, {'certificates': True},
                       {'certificates': 2}, {'fingerprints': ['f' * 64]}, {'extra': 'untrusted-output'}):
            with self.subTest(change=change), patch('docker_context.run_helper', return_value={**response, **change}), self.assertRaises(SafetyError):
                panel_ca._sync_docker_ca(self.fixture.plan, payload)


if __name__ == '__main__':
    unittest.main(verbosity=2)
