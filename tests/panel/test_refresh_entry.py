"""Refresh uses the owned entry and native Root confirmation, without setup side effects."""
from contextlib import ExitStack
import copy
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import manager
import quick_install as quick


class RefreshEntryTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            'host': '8.8.8.8', 'port': 19843, 'https': True,
            'source_site': '/etc/nginx/sites-enabled/original-panel.conf',
            'site_name': 'original.example', 'include_dir': '/etc/nginx/sites-enabled',
            # No independently maintained ConfigureEndpoint command is present here.
            'app': '/srv/original-xboard', 'app_user': 'www-data', 'php': '/usr/bin/php8.3',
            'deployment': 'fpm', 'backend': 'unix:/run/php/php8.3-fpm.sock',
            'ws_backend': 'http://127.0.0.1:8080',
        }
        self.payload = {'pem': 'fixture-public-ca-pem', 'fingerprints': ['a' * 64],
                        'endpoint': {key: self.plan[key] for key in ('host', 'port', 'https')}}

    def forbidden_followups(self, stack):
        mocks = [stack.enter_context(patch.object(quick, name)) for name in
                 ('panel_endpoint_command', 'finish_install', 'configure_firewall',
                  'select_communication_host', 'detect_public_ip', 'ask')]
        mocks.append(stack.enter_context(patch('panel_ca.sync_panel_ca')))
        mocks.append(stack.enter_context(patch('builtins.input', side_effect=AssertionError('Unexpected prompt'))))
        return mocks

    def test_owned_native_plan_and_ws_are_passed_without_address_prompt_or_settings_write(self):
        for deployment, backend in [('fpm', self.plan['backend']), ('octane', 'http://127.0.0.1:8000')]:
            plan = {**self.plan, 'deployment': deployment, 'backend': backend}
            with self.subTest(deployment=deployment), ExitStack() as stack:
                read = stack.enter_context(patch('panel_ca.payload_from_state', return_value=(plan, self.payload)))
                execute = stack.enter_context(patch.object(manager, 'execute'))
                output = stack.enter_context(patch('builtins.print'))
                forbidden = self.forbidden_followups(stack)
                quick.refresh_entry('/www/server/nginx/sbin/nginx')
                self.assertEqual(read.call_count, 2)
                read.assert_called_with('/var/lib/node-endpoint')
                args = execute.call_args.args[0]
                self.assertEqual(args.action, 'modify')
                self.assertEqual(args.nginx, '/www/server/nginx/sbin/nginx')
                self.assertEqual(args.site_config, plan['source_site'])
                for field in ('host', 'port', 'site_name', 'include_dir', 'app', 'app_user', 'php', 'deployment', 'backend', 'ws_backend'):
                    self.assertEqual(getattr(args, field), plan[field])
                self.assertTrue(args.enable_ws)
                self.assertFalse(args.http)
                self.assertIsNone(args.confirm)  # Manager must still require a full INSTALL confirmation.
                for mock in forbidden:
                    mock.assert_not_called()
                messages = ' '.join(str(call.args) for call in output.call_args_list)
                self.assertIn('10', messages)
                self.assertNotIn('systemctl restart', messages)

    def test_rest_only_plan_remains_rest_only(self):
        plan = {**self.plan, 'ws_backend': None}
        with patch('panel_ca.payload_from_state', return_value=(plan, self.payload)), \
                patch.object(manager, 'execute') as execute, patch('builtins.print'):
            quick.refresh_entry()
            self.assertFalse(execute.call_args.args[0].enable_ws)
            self.assertIsNone(execute.call_args.args[0].ws_backend)

    def test_cancel_or_manager_failure_stops_before_after_read_and_followups(self):
        for error in (ValueError('Cancelled'), RuntimeError('Root transaction failed'), EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), ExitStack() as stack:
                read = stack.enter_context(patch('panel_ca.payload_from_state', return_value=(self.plan, self.payload)))
                stack.enter_context(patch.object(manager, 'execute', side_effect=error))
                stack.enter_context(patch('builtins.print'))
                forbidden = self.forbidden_followups(stack)
                with self.assertRaises(type(error)):
                    quick.refresh_entry()
                read.assert_called_once()
                for mock in forbidden:
                    mock.assert_not_called()

    def test_host_port_https_and_ca_changes_are_rejected_after_root_operation(self):
        changes = [({'host': '1.1.1.1'}, {}), ({'port': 8443}, {}), ({'https': False}, {}),
                   ({}, {'pem': 'changed-public-ca'}), ({}, {'fingerprints': ['b' * 64]})]
        for plan_change, payload_change in changes:
            with self.subTest(plan_change=plan_change, payload_change=payload_change), ExitStack() as stack:
                stack.enter_context(patch('panel_ca.payload_from_state', side_effect=[
                    (copy.deepcopy(self.plan), copy.deepcopy(self.payload)),
                    ({**self.plan, **plan_change}, {**self.payload, **payload_change}),
                ]))
                stack.enter_context(patch.object(manager, 'execute'))
                stack.enter_context(patch('builtins.print'))
                forbidden = self.forbidden_followups(stack)
                with self.assertRaises(ValueError):
                    quick.refresh_entry()
                for mock in forbidden:
                    mock.assert_not_called()

    def test_incomplete_record_does_not_guess_parameters_or_modify(self):
        for change in ({'php': None}, {'app_user': ''}, {'https': False}, {'deployment': 'docker'}):
            with self.subTest(change=change), patch('panel_ca.payload_from_state', return_value=({**self.plan, **change}, self.payload)), \
                    patch.object(manager, 'execute') as execute, self.assertRaises(ValueError):
                quick.refresh_entry()
            execute.assert_not_called()

    def root_main_context(self, stack, arguments, tty=True):
        stack.enter_context(patch.object(sys, 'argv', ['quick_install.py', *arguments]))
        stack.enter_context(patch.object(sys, 'platform', 'linux'))
        stack.enter_context(patch.object(sys, 'path', list(sys.path)))
        stack.enter_context(patch.object(sys, 'stdin', SimpleNamespace(isatty=lambda: tty)))
        stack.enter_context(patch.object(quick.os, 'geteuid', return_value=0, create=True))
        stack.enter_context(patch.object(quick.os, 'umask'))
        stack.enter_context(patch.dict(sys.modules, {'pwd': SimpleNamespace()}))

    def test_refresh_is_exclusive_and_rejects_address_firewall_flags_before_download(self):
        for other in ('--detect-only', '--sync-ca', '--configure-firewall', '--repair-address'):
            with self.subTest(other=other), patch.object(sys, 'argv', ['quick_install.py', '--refresh-entry', other]), \
                    patch.object(quick, 'download') as download, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    quick.main()
                self.assertEqual(caught.exception.code, 2)
                download.assert_not_called()
        for arguments, tty in ((['--refresh-entry', '--host', '1.1.1.1'], True),
                               (['--refresh-entry', '--skip-firewall'], True), (['--refresh-entry'], False)):
            with self.subTest(arguments=arguments, tty=tty), ExitStack() as stack:
                self.root_main_context(stack, arguments, tty)
                download = stack.enter_context(patch.object(quick, 'download'))
                with self.assertRaises(ValueError):
                    quick.main()
                download.assert_not_called()

    def test_main_dispatches_after_nginx_selection_before_discovery(self):
        with ExitStack() as stack:
            self.root_main_context(stack, ['--refresh-entry', '--nginx', '/verified/nginx'])
            stack.enter_context(patch.object(quick.shutil, 'which', return_value='/fixture/tool'))
            stack.enter_context(patch.object(quick, 'download', return_value=b'fixture'))
            stack.enter_context(patch.object(quick, 'verified_members', return_value={}))
            stack.enter_context(patch.object(quick, 'install_cache', return_value=ROOT))
            refresh = stack.enter_context(patch.object(quick, 'refresh_entry'))
            environment = stack.enter_context(patch.object(manager, 'Environment'))
            discover = stack.enter_context(patch.object(quick, 'discover'))
            stack.enter_context(patch('builtins.print'))
            quick.main()
            refresh.assert_called_once_with('/verified/nginx')
            environment.assert_not_called()
            discover.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
