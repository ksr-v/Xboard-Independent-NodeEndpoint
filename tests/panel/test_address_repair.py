"""Explicit address repair ordering and application UID boundaries, without real PKI or services."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
spec = importlib.util.spec_from_file_location('address_repair_quick_fixture', ROOT / 'tools/panel/quick_install.py')
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)
import pki


class AddressRepairTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.old = {'host': '203.0.113.10', 'port': 8443, 'https': True,
                    'source_site': '/fixture/panel.conf', 'site_name': 'panel.example',
                    'include_dir': '/fixture/http', 'app': '/fixture/xboard', 'app_user': 'fixture-user',
                    'php': '/fixture/custom/php', 'deployment': 'fpm', 'backend': 'unix:/fixture/php.sock', 'ws_backend': ''}
        self.committed = dict(self.old, host='8.8.8.8')
        self.payload = {'fingerprints': ['a' * 64], 'pem': 'synthetic-public-certificate'}
        self.payload_reader = Mock(side_effect=[(self.old, self.payload), (self.committed, self.payload)])
        self.parser = Mock()
        self.parser.parse_args.side_effect = lambda argv: SimpleNamespace(argv=argv, action='modify')
        self.execute = Mock(side_effect=lambda args: self.events.append('root'))
        self.manager = SimpleNamespace(parser=Mock(return_value=self.parser), execute=self.execute,
                                       trusted_file=Mock(side_effect=Path))
        self.metadata = {'active_ca': 'a' * 16, 'host': self.old['host'], 'rotation': None}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {'manager': self.manager,
            'panel_ca': SimpleNamespace(payload_from_state=self.payload_reader)}))
        self.stack.enter_context(patch.object(pki, 'read_json', side_effect=lambda path: self.metadata))
        self.panel = self.stack.enter_context(patch.object(quick, 'panel_endpoint_command', side_effect=self.panel_call))
        self.select = self.stack.enter_context(patch.object(quick, 'select_communication_host', return_value='8.8.8.8'))
        self.finish = self.stack.enter_context(patch.object(quick, 'finish_install', side_effect=lambda skip: self.events.append('followup')))
        self.stack.enter_context(patch('builtins.print'))

    def panel_call(self, plan, check=False):
        self.events.append('application-check' if check else 'settings')

    def test_legacy_documentation_host_is_readable_but_only_explicit_modify_is_executed(self):
        quick.repair_address(nginx='/fixture/nginx')
        self.assertEqual(self.events, ['application-check', 'root', 'settings', 'followup'])
        self.select.assert_called_once_with(current=None, explicit=None)
        args = self.execute.call_args.args[0]
        self.assertEqual(args.action, 'modify')
        self.assertIn('modify', args.argv)
        self.assertNotIn('install', args.argv)
        self.assertNotIn('rotate-prepare', args.argv)
        self.assertEqual(args.argv[args.argv.index('--host') + 1], '8.8.8.8')
        self.assertEqual(args.argv[args.argv.index('--php') + 1], self.old['php'])
        self.assertEqual(self.old['host'], '203.0.113.10')
        self.panel.assert_any_call(self.old, check=True)
        self.panel.assert_any_call(self.committed)

    def test_existing_real_address_retry_keeps_current_host_and_never_installs_new_ca(self):
        self.old['host'] = '8.8.8.8'
        self.metadata['host'] = self.old['host']
        quick.repair_address(skip_firewall=True)
        self.select.assert_called_once_with(current='8.8.8.8', explicit=None)
        self.assertEqual(self.events, ['application-check', 'root', 'settings', 'followup'])
        self.assertEqual(self.execute.call_args.args[0].action, 'modify')
        self.finish.assert_called_once_with(True)

    def test_explicit_override_and_websocket_backend_are_preserved(self):
        self.old['ws_backend'] = 'http://127.0.0.1:8076'
        quick.repair_address(explicit='8.8.8.8')
        self.select.assert_called_once_with(current=None, explicit='8.8.8.8')
        argv = self.execute.call_args.args[0].argv
        self.assertIn('--enable-ws', argv)
        self.assertEqual(argv[argv.index('--ws-backend') + 1], self.old['ws_backend'])

    def test_application_preflight_failure_does_not_modify_root_or_panel_settings(self):
        self.panel.side_effect = ValueError('fixture backend command unavailable')
        with self.assertRaises(ValueError):
            quick.repair_address()
        self.execute.assert_not_called()
        self.select.assert_not_called()
        self.finish.assert_not_called()

    def test_root_failure_or_confirmation_cancellation_never_writes_panel_settings(self):
        for failure in [RuntimeError('fixture root failure'), EOFError('fixture cancellation')]:
            with self.subTest(error=type(failure).__name__):
                self.events.clear()
                self.payload_reader.side_effect = [(self.old, self.payload)]
                self.execute.side_effect = failure
                self.panel.reset_mock()
                with self.assertRaises(type(failure)):
                    quick.repair_address()
                self.panel.assert_called_once_with(self.old, check=True)
                self.finish.assert_not_called()

    def test_post_commit_panel_failure_is_retryable_without_replacing_root_ca(self):
        self.panel.side_effect = [None, ValueError('fixture settings write unavailable')]
        with self.assertRaises(ValueError):
            quick.repair_address()
        self.finish.assert_not_called()
        self.assertEqual(self.execute.call_args.args[0].action, 'modify')
        # Root is already committed; another explicit repair reuses the same
        # host and manager modify path, then resumes settings/public-CA sync.
        self.old = self.committed
        self.metadata['host'] = self.old['host']
        self.payload_reader.side_effect = [(self.old, self.payload), (self.committed, self.payload)]
        self.panel.side_effect = self.panel_call
        self.events.clear()
        quick.repair_address()
        self.assertEqual(self.events, ['application-check', 'root', 'settings', 'followup'])
        self.assertEqual(self.execute.call_args.args[0].action, 'modify')

    def test_changed_committed_endpoint_is_not_written_into_panel(self):
        other = dict(self.committed, host='1.1.1.1')
        self.payload_reader.side_effect = [(self.old, self.payload), (other, self.payload)]
        with self.assertRaises(ValueError):
            quick.repair_address()
        self.panel.assert_called_once_with(self.old, check=True)
        self.finish.assert_not_called()

    def test_invalid_candidate_is_rejected_before_modify(self):
        self.select.side_effect = ValueError('fixture invalid candidate')
        with self.assertRaises(ValueError):
            quick.repair_address(explicit='203.0.113.10')
        self.execute.assert_not_called()
        self.panel.assert_called_once_with(self.old, check=True)
        self.finish.assert_not_called()

    def test_absent_ca_pending_rotation_and_mismatched_metadata_fail_before_application_or_root(self):
        for metadata in [{}, {'active_ca': 'a' * 16, 'host': self.old['host'], 'rotation': {'new_ca': 'b' * 16}},
                         {'active_ca': 'a' * 16, 'host': '1.1.1.1', 'rotation': None}]:
            with self.subTest(metadata=metadata):
                self.metadata = metadata
                self.payload_reader.side_effect = [(self.old, self.payload)]
                with self.assertRaisesRegex(ValueError, 'CA'):
                    quick.repair_address()
                self.panel.assert_not_called()
                self.select.assert_not_called()
                self.execute.assert_not_called()
                self.finish.assert_not_called()

    def test_changed_ca_trust_after_root_commit_is_not_silently_saved(self):
        other = {'fingerprints': ['b' * 64], 'pem': 'different-synthetic-public-certificate'}
        self.payload_reader.side_effect = [(self.old, self.payload), (self.committed, other)]
        with self.assertRaisesRegex(ValueError, 'CA'):
            quick.repair_address()
        self.panel.assert_called_once_with(self.old, check=True)
        self.finish.assert_not_called()


class ApplicationUserCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Path(temporary.name).resolve()
        command = self.app / 'app/Console/Commands/ConfigureEndpoint.php'
        command.parent.mkdir(parents=True)
        command.write_text('fixture command marker')
        self.owner = SimpleNamespace(pw_uid=1234, pw_gid=1234, pw_name='fixture-user', pw_dir='/fixture/home')
        self.plan = {'host': '8.8.8.8', 'port': 8443, 'app': str(self.app), 'app_user': 'fixture-user',
                     'php': '/fixture/custom/php', 'ws_backend': ''}
        self.trusted = Mock(side_effect=lambda path, executable=False: Path(path))
        self.process = Mock(return_value=SimpleNamespace(returncode=0,
            stdout=b'xboard:configure-endpoint   fixture command\n', stderr=b''))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {'manager': SimpleNamespace(trusted_file=self.trusted),
            'pwd': SimpleNamespace(getpwnam=lambda name: self.owner)}))
        self.stack.enter_context(patch.object(quick.shutil, 'which', return_value='/fixture/runuser'))
        self.stack.enter_context(patch.object(quick.subprocess, 'run', self.process))

    def test_custom_php_runs_as_recorded_nonroot_user_with_private_captured_output(self):
        quick.panel_endpoint_command(self.plan)
        argv = self.process.call_args.args[0]
        self.assertEqual(argv[:5], [str(Path('/fixture/runuser')), '-u', 'fixture-user', '--', str(Path('/fixture/custom/php'))])
        self.assertIn('xboard:configure-endpoint', argv)
        self.assertIn('--host=8.8.8.8', argv)
        self.assertIn('--ws-mode=rest', argv)
        kwargs = self.process.call_args.kwargs
        self.assertEqual(kwargs['cwd'], self.app)
        self.assertEqual(kwargs['timeout'], 90)
        self.assertEqual(kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(kwargs['stderr'], subprocess.PIPE)
        self.assertNotIn('shell', kwargs)
        self.assertEqual(kwargs['env']['USER'], 'fixture-user')
        self.assertNotIn('APP_KEY', kwargs['env'])
        self.trusted.assert_any_call('/fixture/custom/php', True)
        self.trusted.assert_any_call('/fixture/runuser', True)

    def test_root_user_or_root_group_is_rejected_before_php_execution(self):
        for uid, gid in [(0, 1234), (1234, 0)]:
            with self.subTest(uid=uid, gid=gid):
                self.owner.pw_uid, self.owner.pw_gid = uid, gid
                with self.assertRaises(ValueError):
                    quick.panel_endpoint_command(self.plan)
        self.process.assert_not_called()

    def test_preflight_allows_legacy_documentation_host_but_does_not_save_it(self):
        self.plan['host'] = '203.0.113.10'
        quick.panel_endpoint_command(self.plan, check=True)
        argv = self.process.call_args.args[0]
        self.assertEqual(argv[-2:], ['list', '--raw'])
        self.assertNotIn('xboard:configure-endpoint', argv)
        self.assertNotIn('--host=203.0.113.10', argv)

    def test_missing_custom_backend_is_rejected_before_php_execution(self):
        (self.app / 'app/Console/Commands/ConfigureEndpoint.php').unlink()
        with self.assertRaisesRegex(ValueError, 'repair-address'):
            quick.panel_endpoint_command(self.plan, check=True)
        self.process.assert_not_called()

    def test_missing_command_and_failed_php_do_not_forward_application_logs(self):
        private = 'synthetic-private-error-never-forwarded'
        for rc, stdout in [(1, private.encode()), (0, b'other:command fixture\n')]:
            with self.subTest(returncode=rc):
                self.process.return_value = SimpleNamespace(returncode=rc, stdout=stdout, stderr=private.encode())
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output), self.assertRaises(ValueError) as caught:
                    quick.panel_endpoint_command(self.plan, check=True)
                self.assertNotIn(private, str(caught.exception))
                self.assertEqual(output.getvalue(), '')

    def test_timeout_or_execution_failure_uses_generic_retry_message(self):
        private = 'synthetic-private-error-never-forwarded'
        for failure in [subprocess.TimeoutExpired(['php'], 90, output=private, stderr=private), OSError(private)]:
            with self.subTest(error=type(failure).__name__):
                self.process.side_effect = failure
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output), self.assertRaises(ValueError) as caught:
                    quick.panel_endpoint_command(self.plan)
                self.assertIn('--repair-address', str(caught.exception))
                self.assertNotIn(private, str(caught.exception))
                self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
