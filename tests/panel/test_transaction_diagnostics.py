"""Transaction failures retain bounded, non-secret evidence without changing recovery."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import manager as module
from pki import SafetyError

SECRET = 'TOP_SECRET_TEST_VALUE'


class FixtureEnvironment:
    def __init__(self, config):
        self.config = config
        self.files = {}

    def nginx(self, arguments):
        pass  # Isolated transaction fixture; never invokes a host service.

    def refresh(self):
        self.files = {str(self.config): self.config.read_text()} if self.config.exists() else {}


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        config = root / 'node-endpoint.conf'
        env = FixtureEnvironment(config)
        manager = module.Manager(root / 'state', env)
        stack.enter_context(patch.object(manager.pki, 'status', return_value={'initialized': False}))
        stack.enter_context(patch.object(module, 'probe_after_reload', return_value={'fixture': True}))
        module.write_json(manager.pki.meta_path, {'test_certificate_pointer': 'old'})
        plan = {'version': '1.0.0', 'ownership': 'a' * 32, 'host': 'node.example',
                'port': 8443, 'https': False, 'ipv6': False, 'deployment': 'octane',
                'backend': 'http://127.0.0.1:8000', 'ws_backend': None, 'config_path': str(config)}
        manager.transaction(lambda: None, plan)
        previous = {path: path.read_bytes() for path in (
            config, manager.state / 'installation.json', manager.pki.meta_path,
            manager.state / 'public-status.json', manager.state / 'last-backup')}
        yield SimpleNamespace(root=root, config=config, env=env, manager=manager,
                              plan=plan, previous=previous)


def failure(case):
    paths = list((case.manager.state / 'backups').glob('*/FAILURE.json'))
    if len(paths) != 1:
        raise AssertionError('Expected exactly one failure record')
    return paths[0], json.loads(paths[0].read_text())


def raising(*args, **kwargs):
    raise SafetyError(SECRET)


class TransactionDiagnosticsTests(unittest.TestCase):
    def assert_restored(self, case):
        for path, content in case.previous.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    @contextmanager
    def inject(self, case, stage):
        with ExitStack() as stack:
            action = lambda: None
            plan = {**case.plan, 'port': 9443}
            remove = False
            if stage == 'protected_app_registry':
                module.write_json(case.manager.state / 'protected-apps.json', [None])
                plan['app'] = str(case.root / 'app')
            elif stage == 'action':
                action = raising
            elif stage == 'cert_verify':
                plan['https'] = True
                stack.enter_context(patch.object(case.manager.pki, 'leaf_dir', return_value=case.root / 'leaf'))
                stack.enter_context(patch.object(case.manager.pki, 'ca_dir', return_value=case.root / 'ca'))
                stack.enter_context(patch.object(case.manager.pki.openssl, 'verify', side_effect=raising))
            elif stage == 'render_config':
                stack.enter_context(patch.object(module, 'render', side_effect=raising))
            elif stage == 'compare_existing_config':
                plan = case.plan
                original = Path.read_text
                failed = False
                def read_text(path, *args, **kwargs):
                    nonlocal failed
                    if path == case.config and not failed:
                        failed = True
                        raising()
                    return original(path, *args, **kwargs)
                stack.enter_context(patch.object(Path, 'read_text', read_text))
            elif stage in ('write_config', 'write_installation', 'commit_backup_pointer'):
                name = 'write_json' if stage == 'write_installation' else 'atomic_write'
                target = {'write_config': case.config,
                          'write_installation': case.manager.state / 'installation.json',
                          'commit_backup_pointer': case.manager.state / 'last-backup'}[stage]
                original = getattr(module, name)
                failed = False
                def write(path, *args, **kwargs):
                    nonlocal failed
                    if Path(path) == target and not failed:
                        failed = True
                        raise OSError(13, SECRET)
                    return original(path, *args, **kwargs)
                stack.enter_context(patch.object(module, name, side_effect=write))
            elif stage in ('nginx_test', 'nginx_reload'):
                counts = {'test': 0, 'reload': 0}
                def nginx(arguments):
                    key = 'test' if arguments == ['-t'] else 'reload'
                    counts[key] += 1
                    if ((stage == 'nginx_test' and key == 'test' and counts[key] == 2)
                            or (stage == 'nginx_reload' and key == 'reload' and counts[key] == 1)):
                        raising()
                stack.enter_context(patch.object(case.env, 'nginx', side_effect=nginx))
            elif stage == 'refresh_loaded_config':
                original = case.env.refresh
                calls = 0
                def refresh():
                    nonlocal calls
                    calls += 1
                    return raising() if calls == 1 else original()
                stack.enter_context(patch.object(case.env, 'refresh', side_effect=refresh))
            elif stage == 'post_reload_probe':
                stack.enter_context(patch.object(module, 'probe_after_reload', side_effect=raising))
            elif stage == 'public_status':
                stack.enter_context(patch.object(case.manager, 'public_status', side_effect=raising))
            elif stage == 'remove_config':
                remove = True
                original = Path.unlink
                def unlink(path, *args, **kwargs):
                    return raising() if path == case.config else original(path, *args, **kwargs)
                stack.enter_context(patch.object(Path, 'unlink', unlink))
            else:
                raise AssertionError('Unknown test injection')
            yield action, plan, remove

    def test_each_failed_change_phase_is_recorded_and_restored(self):
        stages = ('protected_app_registry', 'action', 'cert_verify', 'render_config',
                  'compare_existing_config', 'write_config', 'write_installation',
                  'nginx_test', 'refresh_loaded_config', 'nginx_reload',
                  'post_reload_probe', 'public_status', 'commit_backup_pointer', 'remove_config')
        for stage in stages:
            with self.subTest(stage=stage), fixture() as case:
                with self.inject(case, stage) as (action, plan, remove):
                    with self.assertRaises(SafetyError) as caught:
                        case.manager.transaction(action, plan, remove=remove)
                path, data = failure(case)
                self.assertEqual(data['schema'], 1)
                self.assertEqual(data['operation'], 'transaction')
                self.assertEqual(data['failed_stage'], stage)
                self.assertFalse(data['rollback_failed'])
                self.assertIn(stage, str(caught.exception))
                self.assertIn(str(path), str(caught.exception))
                self.assertNotIn(SECRET, path.read_text())
                self.assertNotIn(SECRET, str(caught.exception))
                if os.name == 'posix':
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assert_restored(case)

    def test_preflight_failure_keeps_original_exception_and_creates_no_backup(self):
        with fixture() as case:
            before = set((case.manager.state / 'backups').iterdir())
            original = SafetyError(SECRET)
            action = Mock()
            with patch.object(case.env, 'nginx', side_effect=original), \
                    patch.object(case.manager, 'restore') as restore:
                with self.assertRaises(SafetyError) as caught:
                    case.manager.transaction(action, {**case.plan, 'port': 9443})
            self.assertIs(caught.exception, original)
            action.assert_not_called()
            restore.assert_not_called()
            self.assertEqual(set((case.manager.state / 'backups').iterdir()), before)
            self.assert_restored(case)

    def test_rollback_failure_records_its_stage_and_preserves_recovery_marker(self):
        for rollback_stage in ('rollback_restore', 'rollback_nginx_test',
                               'rollback_nginx_reload', 'rollback_refresh_loaded_config'):
            with self.subTest(rollback_stage=rollback_stage), fixture() as case, ExitStack() as stack:
                if rollback_stage == 'rollback_restore':
                    stack.enter_context(patch.object(case.manager, 'restore', side_effect=PermissionError(13, SECRET)))
                elif rollback_stage == 'rollback_refresh_loaded_config':
                    stack.enter_context(patch.object(case.env, 'refresh', side_effect=raising))
                else:
                    tests = 0
                    def nginx(arguments):
                        nonlocal tests
                        if arguments == ['-t']:
                            tests += 1
                        if ((rollback_stage == 'rollback_nginx_test' and arguments == ['-t'] and tests == 2)
                                or (rollback_stage == 'rollback_nginx_reload' and arguments == ['-s', 'reload'])):
                            raising()
                    stack.enter_context(patch.object(case.env, 'nginx', side_effect=nginx))
                with self.assertRaises(SafetyError) as caught:
                    case.manager.transaction(raising, {**case.plan, 'port': 9443})
                path, data = failure(case)
                self.assertEqual(data['failed_stage'], 'action')
                self.assertTrue(data['rollback_failed'])
                self.assertEqual(data['rollback_stage'], rollback_stage)
                self.assertTrue(data['rollback_exception']['exception_types'])
                self.assertTrue((path.parent / 'RECOVERY_REQUIRED.json').exists())
                self.assertIn('Automatic rollback failed', str(caught.exception))
                self.assertNotIn(SECRET, path.read_text())

    def test_diagnostic_write_failure_never_prevents_real_restoration(self):
        with fixture() as case:
            original = module.write_json
            def write(path, *args, **kwargs):
                if Path(path).name == 'FAILURE.json':
                    raise PermissionError(13, SECRET)
                return original(path, *args, **kwargs)
            def action():
                original(case.manager.pki.meta_path, {'test_certificate_pointer': 'changed'})
                raising()
            with patch.object(module, 'write_json', side_effect=write):
                with self.assertRaisesRegex(SafetyError, 'previous configuration restored') as caught:
                    case.manager.transaction(action, {**case.plan, 'port': 9443})
            self.assertIn('action', str(caught.exception))
            self.assertIn('diagnostic file could not be written', str(caught.exception))
            self.assert_restored(case)

    def test_all_evidence_write_failures_still_report_failed_rollback(self):
        with fixture() as case:
            original = module.write_json
            def write(path, *args, **kwargs):
                if Path(path).name in ('FAILURE.json', 'RECOVERY_REQUIRED.json'):
                    raise PermissionError(13, SECRET)
                return original(path, *args, **kwargs)
            with patch.object(module, 'write_json', side_effect=write), \
                    patch.object(case.manager, 'restore', side_effect=OSError(5, SECRET)):
                with self.assertRaisesRegex(SafetyError, 'Automatic rollback failed at rollback_restore') as caught:
                    case.manager.transaction(raising, {**case.plan, 'port': 9443})
            self.assertNotIn(SECRET, str(caught.exception))

    def test_exception_messages_commands_and_output_are_never_serialized(self):
        with fixture() as case:
            low = OSError(13, SECRET, SECRET)
            command = subprocess.CalledProcessError(7, [SECRET], output=SECRET, stderr=SECRET)
            command.__cause__ = low
            outer = SafetyError(SECRET)
            outer.__cause__ = command
            with self.assertRaises(SafetyError):
                case.manager.transaction(lambda: (_ for _ in ()).throw(outer), {**case.plan, 'port': 9443})
            path, data = failure(case)
            self.assertEqual(data['exception_types'], ['SafetyError', 'CalledProcessError', 'PermissionError'])
            self.assertEqual(data['exception_codes'], [{'index': 1, 'returncode': 7}, {'index': 2, 'errno': 13}])
            self.assertNotIn(SECRET, path.read_text())
            for forbidden in ('message', 'args', 'cmd', 'stdout', 'stderr', 'output', 'filename'):
                self.assertNotIn('"' + forbidden + '"', path.read_text())

    def test_explicit_rollback_failure_keeps_current_entry_and_has_own_diagnostic(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent), fixture() as case:
                case.manager.transaction(lambda: None, {**case.plan, 'port': 9443})
                current = case.config.read_bytes()
                reloads = 0
                def nginx(arguments):
                    nonlocal reloads
                    if arguments == ['-s', 'reload']:
                        reloads += 1
                        if persistent or reloads == 1:
                            raising()
                with patch.object(case.env, 'nginx', side_effect=nginx):
                    with self.assertRaises(SafetyError):
                        case.manager.rollback()
                path, data = failure(case)
                self.assertEqual(data['operation'], 'rollback')
                self.assertEqual(data['failed_stage'], 'nginx_reload')
                self.assertEqual(data['rollback_failed'], persistent)
                self.assertEqual((path.parent / 'RECOVERY_REQUIRED.json').exists(), persistent)
                self.assertEqual(case.config.read_bytes(), current)


class ExceptionDiagnosticTests(unittest.TestCase):
    def test_real_command_error_retains_only_numeric_exit_code_for_diagnostics(self):
        result = SimpleNamespace(returncode=7, stdout=SECRET.encode(), stderr=SECRET.encode())
        with patch.object(module.subprocess, 'run', return_value=result), \
                patch.object(module, 'trusted_file', side_effect=lambda value, *a: Path(value)):
            with self.assertRaises(SafetyError) as caught:
                module.command(['nginx', SECRET])
        data = module.exception_diagnostic(caught.exception)
        self.assertEqual(data['exception_types'], ['SafetyError'])
        self.assertEqual(data['exception_codes'], [{'index': 0, 'returncode': 7}])
        self.assertNotIn(SECRET, json.dumps(data))

    def test_chain_is_bounded_and_cycles_are_identified(self):
        errors = [SafetyError(SECRET) for _ in range(12)]
        for first, second in zip(errors, errors[1:]):
            first.__cause__ = second
        data = module.exception_diagnostic(errors[0])
        self.assertEqual(len(data['exception_types']), 8)
        self.assertTrue(data['exception_chain_truncated'])
        self.assertFalse(data['exception_chain_cycle'])
        first, second = SafetyError(SECRET), ValueError(SECRET)
        first.__cause__, second.__cause__ = second, first
        data = module.exception_diagnostic(first)
        self.assertEqual(data['exception_types'], ['SafetyError', 'ValueError'])
        self.assertTrue(data['exception_chain_cycle'])
        self.assertFalse(data['exception_chain_truncated'])

    def test_suppressed_context_and_numeric_metadata_follow_strict_whitelist(self):
        error = ValueError(SECRET)
        error.__context__ = PermissionError(13, SECRET)
        error.__suppress_context__ = True
        error.errno, error.returncode = SECRET, True
        data = module.exception_diagnostic(error)
        self.assertEqual(data['exception_types'], ['ValueError'])
        self.assertEqual(data['exception_codes'], [])
        error.__suppress_context__ = False
        self.assertEqual(module.exception_diagnostic(error)['exception_types'], ['ValueError', 'PermissionError'])
        error.__cause__ = KeyError(SECRET)
        self.assertEqual(module.exception_diagnostic(error)['exception_types'], ['ValueError', 'KeyError'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
