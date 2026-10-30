"""Same-scope helper upgrades preserve prior root policy and systemd choices."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import web_manager as web


def previous_policy(version='1.0.0'):
    binding = {'app': '/srv/xboard', 'app_user': 'www', 'php': '/usr/bin/php', 'deployment': 'fpm',
               'backend': 'unix:/run/php.sock', 'source_site': '/etc/nginx/site.conf',
               'site_name': 'panel.example', 'include_dir': '/etc/nginx/conf.d',
               'config_path': '/etc/nginx/conf.d/node.conf', 'main_config': '/etc/nginx/nginx.conf',
               'ownership': 'a' * 32}
    return {'schema': 1, 'version': version,
            'tools_sha256': web.PREVIOUS_TOOLS_SHA256 if version == '1.0.1' else web.LEGACY_TOOLS_SHA256,
            'manifest_sha256': 'b' * 64, 'app': binding['app'], 'app_user': 'www', 'php': binding['php'],
            'app_uid': 1001, 'app_gid': 1001, 'nginx': '/usr/sbin/nginx', 'state': '/var/lib/node-endpoint',
            'binding': binding, 'ws_backend': None, 'allowed_ports': [8443, 9443]}


class UpgradePolicyTests(unittest.TestCase):
    def test_known_legacy_and_current_policies_keep_the_complete_authorization_scope(self):
        old = previous_policy()
        expected = {**old, 'version': web.VERSION, 'tools_sha256': web.TOOLS_SHA256,
                    'manifest_sha256': 'c' * 64}
        self.assertEqual(web.upgrade_policy(old, 'c' * 64), expected)
        self.assertEqual(web.upgrade_policy(expected, 'c' * 64), expected)
        self.assertEqual(old['version'], '1.0.0')
        previous = previous_policy('1.0.1')
        self.assertEqual(web.upgrade_policy(previous, 'c' * 64), expected)
        self.assertEqual(previous['version'], '1.0.1')

    def test_unknown_version_pin_or_invalid_legacy_scope_is_rejected(self):
        for change in ({'version': '1.0.9'}, {'tools_sha256': 'f' * 64},
                       {'version': web.VERSION, 'tools_sha256': web.LEGACY_TOOLS_SHA256},
                       {'version': '1.0.1', 'tools_sha256': web.LEGACY_TOOLS_SHA256},
                       {'version': '1.0.0', 'tools_sha256': web.PREVIOUS_TOOLS_SHA256},
                       {'app_uid': 0}, {'allowed_ports': [80]}, {'allowed_ports': [8443, 8443]},
                       {'manifest_sha256': 'invalid'}, {'unknown': 'field'}):
            with self.subTest(change=change), self.assertRaises(web.RpcError):
                web.upgrade_policy({**previous_policy(), **change}, 'c' * 64)


class UpgradeInstallTests(unittest.TestCase):
    def context(self, stack, active=True, uid=1001, change=None, version='1.0.0'):
        old = previous_policy(version)
        plan = {**old['binding'], 'port': 8443, 'host': 'node.example', 'https': True,
                'ws_backend': old['ws_backend'], **(change or {})}
        env = SimpleNamespace(main=plan['main_config'], binary=old['nginx'],
                              files={plan['config_path']: 'owned entry'})
        tools = SimpleNamespace(manifest_sha256='c' * 64,
            manager=SimpleNamespace(safe_state=lambda value: PurePosixPath(value),
                                    Environment=Mock(return_value=env), trusted_file=Mock()),
            panel_ca=SimpleNamespace(payload_from_state=Mock(return_value=(plan, {}))))
        stack.enter_context(patch.object(web, 'root_only'))
        stack.enter_context(patch.object(web, 'PinnedTools', return_value=tools))
        stack.enter_context(patch.object(web, 'read_private', return_value=old))
        lifecycle = stack.enter_context(patch.object(web, 'service_state', return_value={'active': active, 'enabled': active}))
        stack.enter_context(patch.dict(sys.modules, {'pwd': SimpleNamespace(
            getpwnam=lambda name: SimpleNamespace(pw_uid=uid, pw_gid=1001))}))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        unit = folder / 'unit'
        unit.write_text('old unit')
        stack.enter_context(patch.object(web, 'SERVICE', unit))
        stack.enter_context(patch.object(web, 'PRIVATE', folder / 'private'))
        stack.enter_context(patch.object(web, 'CONFIG', folder / 'policy'))
        code = folder / web.VERSION / 'helper.py'
        stack.enter_context(patch.object(web, 'CODE', code))
        source = SimpleNamespace(resolve=lambda: source,
            stat=lambda: SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1, st_size=10),
            read_bytes=lambda: b'new helper')
        actual_path = Path
        stack.enter_context(patch.object(web, 'Path', side_effect=lambda value: source if value == web.__file__ else actual_path(value)))
        stack.enter_context(patch.object(web, 'secure', side_effect=lambda value, *a, **kw: value))
        stack.enter_context(patch.object(web, 'create_directory'))
        stack.enter_context(patch.object(web, 'service_text', return_value='new unit'))
        writes = stack.enter_context(patch.object(web, 'atomic'))
        service = stack.enter_context(patch.object(web, 'systemctl'))
        return old, writes, service, lifecycle

    def test_active_helper_keeps_extra_ports_and_uses_only_stop_reload_start(self):
        with ExitStack() as stack:
            old, writes, service, _ = self.context(stack)
            result = web.install(SimpleNamespace(command='upgrade', state='/var/lib/node-endpoint'))
            self.assertEqual(result['policy']['allowed_ports'], [8443, 9443])
            saved = json.loads(writes.call_args_list[1].args[1])
            self.assertEqual(saved, web.upgrade_policy(old, 'c' * 64))
            self.assertEqual([call.args[0] for call in service.call_args_list],
                             ['stop', 'daemon-reload', 'start', 'is-active'])

    def test_inactive_disabled_helper_stays_inactive_without_enabling(self):
        with ExitStack() as stack:
            _, _, service, _ = self.context(stack, active=False)
            web.install(SimpleNamespace(command='upgrade', state='/var/lib/node-endpoint'))
            self.assertEqual([call.args[0] for call in service.call_args_list], ['stop', 'daemon-reload'])

    def test_previously_upgraded_helper_can_upgrade_again_after_entry_rollback(self):
        with ExitStack() as stack:
            old, writes, service, lifecycle = self.context(stack, version='1.0.1')
            result = web.install(SimpleNamespace(command='upgrade', state='/var/lib/node-endpoint'))
            lifecycle.assert_called_once_with(old)
            self.assertEqual(result['policy']['allowed_ports'], old['allowed_ports'])
            self.assertEqual(json.loads(writes.call_args_list[1].args[1]), web.upgrade_policy(old, 'c' * 64))
            self.assertEqual([call.args[0] for call in service.call_args_list],
                             ['stop', 'daemon-reload', 'start', 'is-active'])

    def test_different_state_uid_or_binding_is_rejected_before_any_service_change(self):
        for uid, change, state in ((1002, None, '/var/lib/node-endpoint'),
                                   (1001, {'app': '/foreign/app'}, '/var/lib/node-endpoint'),
                                   (1001, None, '/var/lib/another-entry')):
            with self.subTest(uid=uid, change=change, state=state), ExitStack() as stack:
                _, writes, service, _ = self.context(stack, uid=uid, change=change)
                with self.assertRaises(web.RpcError):
                    web.install(SimpleNamespace(command='upgrade', state=state))
                writes.assert_not_called()
                service.assert_not_called()

    def test_failed_file_replacement_does_not_start_old_or_new_helper(self):
        for failure in range(3):
            with self.subTest(failure=failure), ExitStack() as stack:
                _, writes, service, _ = self.context(stack)
                writes.side_effect = [None] * failure + [OSError('injected write failure')]
                with self.assertRaises(OSError):
                    web.install(SimpleNamespace(command='upgrade', state='/var/lib/node-endpoint'))
                self.assertEqual([call.args[0] for call in service.call_args_list], ['stop'])

    def test_failed_daemon_reload_does_not_start_a_helper(self):
        with ExitStack() as stack:
            _, _, service, _ = self.context(stack)
            service.side_effect = [None, OSError('injected daemon-reload failure')]
            with self.assertRaises(OSError):
                web.install(SimpleNamespace(command='upgrade', state='/var/lib/node-endpoint'))
            self.assertEqual([call.args[0] for call in service.call_args_list], ['stop', 'daemon-reload'])


class ServiceStateTests(unittest.TestCase):
    def context(self, stack, enabled='enabled', active='active', dropins='', version='1.0.0'):
        data = b'legacy helper fixture'
        fingerprint = 'PREVIOUS_HELPER_SHA256' if version == '1.0.1' else 'LEGACY_HELPER_SHA256'
        stack.enter_context(patch.object(web, fingerprint, hashlib.sha256(data).hexdigest()))
        old_code = Path('/opt/nodeendpoint-web-manager') / version / 'web_manager.py'
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        unit = Path(temporary.name) / 'unit'
        unit.write_text('generated unit')
        stack.enter_context(patch.object(web, 'SERVICE', unit))
        stack.enter_context(patch.object(web, 'service_text', return_value='generated unit'))
        stack.enter_context(patch.object(web, 'secure', side_effect=lambda value, *a, **kw:
            SimpleNamespace(read_bytes=lambda: data) if value == old_code else value))
        def run(argv, **kwargs):
            self.assertFalse(kwargs['shell'])
            if '--property=FragmentPath' in argv:
                value, code = str(web.SERVICE), 0
            elif '--property=DropInPaths' in argv:
                value, code = dropins, 0
            elif 'is-enabled' in argv:
                value, code = enabled, 0 if enabled == 'enabled' else 1
            else:
                value, code = active, 0 if active == 'active' else 3
            return SimpleNamespace(stdout=(value + '\n').encode(), stderr=b'', returncode=code)
        stack.enter_context(patch.object(web.subprocess, 'run', side_effect=run))

    def test_only_standard_enabled_and_active_states_are_preserved(self):
        for enabled in ('enabled', 'disabled'):
            for active in ('active', 'inactive'):
                with self.subTest(enabled=enabled, active=active), ExitStack() as stack:
                    self.context(stack, enabled, active)
                    self.assertEqual(web.service_state(previous_policy()),
                                     {'enabled': enabled == 'enabled', 'active': active == 'active'})

    def test_masked_failed_or_overridden_units_are_refused(self):
        for enabled, active, dropins in (('masked', 'inactive', ''), ('enabled', 'failed', ''),
                                        ('enabled', 'active', '/etc/systemd/override.conf')):
            with self.subTest(enabled=enabled, active=active, dropins=dropins), ExitStack() as stack:
                self.context(stack, enabled, active, dropins)
                with self.assertRaises(web.RpcError):
                    web.service_state(previous_policy())

    def test_previous_published_helper_code_is_verified_with_its_own_pin(self):
        with ExitStack() as stack:
            self.context(stack, version='1.0.1')
            self.assertEqual(web.service_state(previous_policy('1.0.1')), {'enabled': True, 'active': True})


if __name__ == '__main__':
    unittest.main(verbosity=2)
