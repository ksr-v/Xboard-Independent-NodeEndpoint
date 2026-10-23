"""The simplified Root confirmation still precedes every endpoint/settings mutation."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
import docker_context
import manager
import quick_install as quick
from test_docker_context import context


def site():
    ctx = context()
    return {'site_config': '/etc/nginx/sites/panel.conf', 'site_name': 'panel.test',
            'source_file_sha256': 'a' * 64, 'app': ctx['working_dir'],
            'backend': 'http://127.0.0.1:17001', 'include_dir': '/etc/nginx/sites',
            'ws_backend': 'http://127.0.0.1:17001', 'docker_context': ctx}


class DockerWizardTests(unittest.TestCase):
    def fixture(self, stack, answer='yes'):
        selected = site()
        settings = {'enabled': False, 'host': '', 'port': 8443, 'https': True,
                    'http_acknowledged': False, 'ws_mode': 'rewrite'}
        preview = {'host': '8.8.4.4', 'port': 8443, 'docker_context': selected['docker_context']}
        env = SimpleNamespace(plan=lambda *args: copy.deepcopy(preview))
        stack.enter_context(patch.object(manager, 'linux_root'))
        stack.enter_context(patch.object(manager, 'Environment', return_value=env))
        stack.enter_context(patch.object(manager, 'safe_state', side_effect=lambda value: value))
        stack.enter_context(patch.object(manager, 'Manager', return_value=SimpleNamespace(installed={})))
        stack.enter_context(patch.object(quick, 'discover_docker', return_value=[selected]))
        stack.enter_context(patch('panel_ca.payload_from_state', return_value=(preview, {})))
        stack.enter_context(patch('builtins.input', return_value=answer))
        stack.enter_context(patch('builtins.print'))
        calls = []

        def helper(ctx, name, payload):
            calls.append(payload['action'])
            self.assertEqual(name, 'endpoint_settings')
            if payload['action'] == 'read':
                return {'status': 'read', 'uid': 1000, 'settings': settings}
            self.assertEqual(calls, ['read', 'install', 'save'])
            self.assertEqual(payload['expected'], settings)
            return {'status': 'saved', 'uid': 1000, 'settings': {**settings, 'host': '8.8.4.4'}}

        bridge = stack.enter_context(patch.object(docker_context, 'run_helper', side_effect=helper))
        execute = stack.enter_context(patch.object(manager, 'execute', side_effect=lambda args: calls.append('install')))
        finish = stack.enter_context(patch.object(quick, 'finish_install'))
        return selected, preview, execute, bridge, finish, calls

    def test_cancel_never_changes_entry_settings_firewall_or_ca(self):
        for answer in ('', 'no', 'INSTALL'):
            with self.subTest(answer=answer), ExitStack() as stack:
                _, _, execute, bridge, finish, calls = self.fixture(stack, answer)
                with self.assertRaisesRegex(ValueError, '已取消'):
                    quick.docker_setup(host='8.8.4.4')
                execute.assert_not_called()
                finish.assert_not_called()
                self.assertEqual(calls, ['read'])

    def test_one_yes_audits_and_commits_before_settings_and_followups(self):
        with ExitStack() as stack:
            _, preview, execute, _, finish, calls = self.fixture(stack)
            quick.docker_setup(host='8.8.4.4', skip_firewall=True)
            self.assertEqual(calls, ['read', 'install', 'save'])
            args = execute.call_args.args[0]
            self.assertTrue(args.docker_auto)
            self.assertEqual(args._approved_plan, preview)
            self.assertEqual(args.confirm, 'INSTALL 8.8.4.4:8443')
            self.assertEqual(args.container_user, '1000:1000')
            finish.assert_called_once_with(True)

    def test_failed_root_transaction_never_saves_panel_settings(self):
        with ExitStack() as stack:
            _, _, execute, _, finish, calls = self.fixture(stack)
            execute.side_effect = RuntimeError('transaction rolled back')
            with self.assertRaisesRegex(RuntimeError, 'rolled back'):
                quick.docker_setup(host='8.8.4.4')
            self.assertEqual(calls, ['read'])
            finish.assert_not_called()

    def test_merged_confirmation_requires_same_reviewed_source(self):
        for change in (None, {'source_file_sha256': 'b' * 64}, {'backend': 'http://127.0.0.1:17002'}):
            with self.subTest(change=change), ExitStack() as stack:
                selected, _, execute, bridge, finish, _ = self.fixture(stack)
                expected = None if change is None else {**selected, **change}
                with self.assertRaises(ValueError):
                    quick.docker_setup(host='8.8.4.4', confirmed=True,
                                       expected_backend=selected['backend'], expected_site=expected)
                execute.assert_not_called()
                bridge.assert_not_called()
                finish.assert_not_called()

    def test_merged_confirmation_uses_already_approved_plan_without_second_question(self):
        with ExitStack() as stack:
            selected, _, execute, _, _, _ = self.fixture(stack)
            with patch('builtins.input', side_effect=AssertionError('second confirmation')):
                quick.docker_setup(host='8.8.4.4', confirmed=True,
                                   expected_backend=selected['backend'], expected_site=selected)
            execute.assert_called_once()

    def test_discovery_joins_real_loopback_site_and_combined_ws_backend(self):
        text = '''server { server_name panel.test alias.test; location / {
            proxy_pass http://127.0.0.1:17001;
            proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
        } }'''
        env = SimpleNamespace(files={'/etc/nginx/sites/panel.conf': text}, prefix='/etc/nginx',
                              includes=['/etc/nginx/sites/*.conf'], nodes=manager.parse(text))
        with patch.object(docker_context, 'list_contexts', return_value=[context()]):
            found = quick.discover_docker(env, '/srv/panel')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['ws_backend'], 'http://127.0.0.1:17001')
        self.assertEqual(found[0]['site_name'], 'panel.test')
        self.assertEqual(found[0]['source_file_sha256'], manager.sha256(text))
        env.files['/etc/nginx/aggregate.conf'] = 'include /etc/nginx/sites/*.conf;'
        with patch.object(docker_context, 'list_contexts', return_value=[context()]):
            self.assertEqual(len(quick.discover_docker(env)), 1)
        env.files['/etc/nginx/sites/node-endpoint.conf'] = text.replace('panel.test alias.test', '8.8.4.4')
        with patch.object(docker_context, 'list_contexts', return_value=[context()]), \
                patch.object(quick, 'owned_config', return_value='/etc/nginx/sites/node-endpoint.conf'):
            self.assertEqual(len(quick.discover_docker(env)), 1)
        del env.files['/etc/nginx/sites/node-endpoint.conf']
        env.files['/etc/nginx/sites/panel.conf'] = text.replace('17001', '17002')
        with patch.object(docker_context, 'list_contexts', return_value=[context()]):
            self.assertEqual(quick.discover_docker(env), [])


if __name__ == '__main__':
    unittest.main()
