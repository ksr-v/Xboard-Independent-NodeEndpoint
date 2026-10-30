"""Automatic Docker planning and confirmation boundaries; no Docker/PKI is started."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import manager
from nginx import API_METHODS, AUDITED_PROXY_TRUST_SHA256, parse
from pki import SafetyError
from test_docker_context import CID, context


class DockerManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'project'
        self.app.mkdir()
        self.include = self.root / 'sites'
        self.include.mkdir()
        self.site = self.include / 'original.conf'
        site = ('server { listen 80; server_name panel.example; location / { '
                'proxy_pass http://127.0.0.1:17001; proxy_set_header Upgrade $http_upgrade; '
                'proxy_set_header Connection upgrade; } }')
        self.site.write_text(site)
        self.env = manager.Environment.__new__(manager.Environment)
        self.env.main = str(self.root / 'nginx.conf')
        main = 'events {} http { server { listen 80; } }'
        self.env.files = {self.env.main: main, str(self.site): site}
        self.env.prefix = str(self.root)
        self.env.nodes = parse(main)
        self.env.includes = [str(self.include / '*.conf')]
        self.env.ss, self.env.unix = '', ''
        self.args = manager.parser().parse_args(['install', '--host', '1.1.1.1', '--port', '8443',
            '--include-dir', str(self.include), '--site-config', str(self.site), '--site-name', 'panel.example',
            '--app', str(self.app), '--deployment', 'docker', '--docker-auto', '--container', CID,
            '--backend', 'http://127.0.0.1:17001', '--enable-ws', '--ws-backend', 'http://127.0.0.1:17001'])
        self.ctx = context()
        self.ctx['working_dir'] = str(self.app)
        self.routes = [{'uri': path[1:], 'method': '|'.join(methods)} for path, methods in API_METHODS.items()]

    def plan_context(self, stack):
        stack.enter_context(patch.object(manager, 'trusted_file', side_effect=lambda path, *args: Path(path)))
        stack.enter_context(patch('docker_context.inspect_context', return_value=self.ctx))
        stack.enter_context(patch('docker_context.assert_state_isolated'))
        stack.enter_context(patch('docker_context.read_proxy_profile', return_value=next(iter(AUDITED_PROXY_TRUST_SHA256))))
        stack.enter_context(patch('docker_context.run_artisan', return_value=json.dumps(self.routes)))
        stack.enter_context(patch.object(manager, 'probe_backend'))

    def test_auto_audits_container_instead_of_requiring_host_php_or_full_application_mount(self):
        self.assertFalse((self.app / 'artisan').exists())
        with ExitStack() as stack:
            self.plan_context(stack)
            native = stack.enter_context(patch('nginx.verify_proxy_trust', side_effect=AssertionError('Host PHP profile used')))
            legacy = stack.enter_context(patch.object(manager, 'command', side_effect=AssertionError('Legacy Docker transport used')))
            plan = self.env.plan(self.args, self.root / 'root-state', {})
        self.assertEqual(plan['docker_context'], self.ctx)
        self.assertEqual(plan['container_user'], '1000:1000')
        self.assertEqual(plan['container_app'], '/www')
        self.assertEqual(plan['ws_backend'], plan['backend'])
        self.assertEqual(plan['optional_api_paths'], [])
        native.assert_not_called()
        legacy.assert_not_called()

    def test_unknown_proxy_or_changed_compose_project_is_refused(self):
        for wrong_project in (False, True):
            with self.subTest(wrong_project=wrong_project), ExitStack() as stack:
                self.plan_context(stack)
                if wrong_project:
                    stack.enter_context(patch('docker_context.inspect_context', return_value={**self.ctx, 'working_dir': str(self.root)}))
                else:
                    stack.enter_context(patch('docker_context.read_proxy_profile', return_value='f' * 64))
                with self.assertRaises(SafetyError):
                    self.env.plan(self.args, self.root / 'root-state', {})

    def test_legacy_namespace_without_flag_keeps_original_full_bind_guard(self):
        del self.args.docker_auto
        with patch.object(manager, 'trusted_file', side_effect=lambda path, *args: Path(path)), \
                patch('docker_context.inspect_context') as inspect, self.assertRaises(SafetyError):
            self.env.plan(self.args, self.root / 'root-state', {})
        inspect.assert_not_called()

    def execute_context(self, stack, reviewed=None, confirmation_error=None):
        state = self.root / 'root-state'
        state.mkdir(exist_ok=True)
        self.args.state = str(state)
        stack.enter_context(patch.object(manager, 'linux_root'))
        stack.enter_context(patch.object(manager, 'trusted_file', side_effect=lambda path, *args: Path(path)))
        stack.enter_context(patch.object(manager, 'safe_state', side_effect=lambda path: Path(path)))
        initialized = stack.enter_context(patch.object(manager, 'initialize_state', return_value=state))
        stack.enter_context(patch('docker_context.inspect_context', return_value=self.ctx))
        isolation = stack.enter_context(patch('docker_context.assert_state_isolated'))
        stack.enter_context(patch.object(manager.os, 'fchmod', create=True))
        stack.enter_context(patch.object(manager.os, 'O_NOFOLLOW', getattr(manager.os, 'O_NOFOLLOW', 0), create=True))
        stack.enter_context(patch.dict(sys.modules, {'fcntl': SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)}))
        pki = SimpleNamespace(meta={'host': '1.1.1.1'})
        stack.enter_context(patch.object(manager, 'PKI', return_value=pki))
        plan = {'host': '1.1.1.1', 'port': 8443, 'https': True, 'deployment': 'docker',
                'app': str(self.app), 'docker_context': self.ctx, 'ownership': 'a' * 32,
                'route_profile_sha256': 'b' * 64}
        if reviewed is not None:
            self.args._approved_plan = reviewed(plan)
        environment = Mock()
        environment.plan.return_value = plan
        stack.enter_context(patch.object(manager, 'Environment', return_value=environment))
        controller = Mock(installed={})
        events = []
        def transaction(callback, *_):
            events.append('backup')
            callback()
            return {'changed': True}
        controller.transaction.side_effect = transaction
        stack.enter_context(patch.object(manager, 'Manager', return_value=controller))
        confirm = stack.enter_context(patch.object(manager, 'confirmation', side_effect=confirmation_error or (lambda *args: events.append('confirm'))))
        rebind = stack.enter_context(patch('panel_passphrase.rebind_docker_context', side_effect=lambda *args: events.append('rebind')))
        existing = stack.enter_context(patch('panel_passphrase.validate_existing_metadata', side_effect=lambda *args, **kwargs: events.append('metadata')))
        stack.enter_context(patch('builtins.print'))
        return events, controller, confirm, rebind, existing, initialized, isolation

    def test_approved_plan_only_ignores_random_ownership_and_rebind_happens_after_backup_and_confirmation(self):
        with ExitStack() as stack:
            events, controller, confirm, rebind, existing, _, _ = self.execute_context(stack, lambda plan: {**plan, 'ownership': 'c' * 32})
            manager.execute(self.args)
        self.assertEqual(events, ['confirm', 'backup', 'rebind', 'metadata'])
        controller.transaction.assert_called_once()

    def test_changed_review_or_cancel_does_not_rebind_or_retrieve_credentials(self):
        for mutate, error in ((lambda plan: {**plan, 'route_profile_sha256': 'd' * 64}, None),
                              (lambda plan: copy.deepcopy(plan), SafetyError('Cancelled'))):
            with self.subTest(cancel=bool(error)), ExitStack() as stack:
                _, controller, _, rebind, existing, _, _ = self.execute_context(stack, mutate, error)
                with self.assertRaises(SafetyError):
                    manager.execute(self.args)
                controller.transaction.assert_not_called()
                rebind.assert_not_called()
                existing.assert_not_called()

    def test_mounted_root_state_is_rejected_before_directory_creation(self):
        with ExitStack() as stack:
            _, controller, _, rebind, _, initialized, isolation = self.execute_context(stack)
            isolation.side_effect = SafetyError('Root state is mounted')
            with self.assertRaises(SafetyError):
                manager.execute(self.args)
            initialized.assert_not_called()
            rebind.assert_not_called()
            controller.transaction.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
