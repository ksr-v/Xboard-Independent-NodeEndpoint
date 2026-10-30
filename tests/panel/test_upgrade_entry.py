"""An entry upgrade never creates web authorization or refreshes after helper failure."""
from contextlib import ExitStack
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import quick_install as quick


class UpgradeEntryTests(unittest.TestCase):
    def context(self, stack, present=()):
        stack.enter_context(patch('panel_ca.payload_from_state', return_value=(
            {'deployment': 'fpm', 'https': True}, {})))
        def path(value):
            return SimpleNamespace(exists=lambda: value in present, is_symlink=lambda: False)
        stack.enter_context(patch.object(quick, 'Path', side_effect=path))
        secure = stack.enter_context(patch.object(quick, 'secure_path'))
        helper = stack.enter_context(patch.object(quick, 'install_web_manager'))
        refresh = stack.enter_context(patch.object(quick, 'refresh_entry'))
        return secure, helper, refresh

    def test_no_helper_only_refreshes_existing_entry(self):
        with ExitStack() as stack:
            _, helper, refresh = self.context(stack)
            quick.upgrade_entry('/verified/nginx')
            helper.assert_not_called()
            refresh.assert_called_once_with('/verified/nginx')

    def test_existing_helper_is_upgraded_before_route_refresh(self):
        with ExitStack() as stack:
            secure, helper, refresh = self.context(stack, ('/etc/nodeendpoint-web-manager.json',))
            order = []
            helper.side_effect = lambda **kw: order.append('helper')
            refresh.side_effect = lambda *a: order.append('refresh')
            quick.upgrade_entry('/verified/nginx')
            self.assertEqual(order, ['helper', 'refresh'])
            secure.assert_called_once()
            helper.assert_called_once_with(nginx='/verified/nginx', upgrade=True)

    def test_helper_verification_or_upgrade_failure_never_refreshes_routes(self):
        for boundary in ('secure', 'helper'):
            with self.subTest(boundary=boundary), ExitStack() as stack:
                secure, helper, refresh = self.context(stack, ('/etc/nodeendpoint-web-manager.json',))
                (secure if boundary == 'secure' else helper).side_effect = ValueError('Refused')
                with self.assertRaises(ValueError):
                    quick.upgrade_entry()
                refresh.assert_not_called()

    def test_orphaned_helper_layout_is_refused_before_route_refresh(self):
        for residue in ('/etc/systemd/system/nodeendpoint-web-manager.service',
                        '/opt/nodeendpoint-web-manager', '/var/lib/nodeendpoint-web-manager',
                        '/run/nodeendpoint-web-manager/control.sock'):
            with self.subTest(residue=residue), ExitStack() as stack:
                _, helper, refresh = self.context(stack, (residue,))
                with self.assertRaisesRegex(ValueError, '安装状态不完整'):
                    quick.upgrade_entry()
                helper.assert_not_called()
                refresh.assert_not_called()

    def test_unsupported_entry_is_refused_before_helper_or_refresh(self):
        with ExitStack() as stack:
            stack.enter_context(patch('panel_ca.payload_from_state', return_value=(
                {'deployment': 'docker', 'https': True}, {})))
            helper = stack.enter_context(patch.object(quick, 'install_web_manager'))
            refresh = stack.enter_context(patch.object(quick, 'refresh_entry'))
            with self.assertRaises(ValueError):
                quick.upgrade_entry()
            helper.assert_not_called()
            refresh.assert_not_called()

    def main_context(self, stack, arguments, tty=True):
        stack.enter_context(patch.object(sys, 'argv', ['quick_install.py', *arguments]))
        stack.enter_context(patch.object(sys, 'platform', 'linux'))
        stack.enter_context(patch.object(sys, 'path', list(sys.path)))
        stack.enter_context(patch.object(sys, 'stdin', SimpleNamespace(isatty=lambda: tty)))
        stack.enter_context(patch.object(quick.os, 'geteuid', return_value=0, create=True))
        stack.enter_context(patch.object(quick.os, 'umask'))
        stack.enter_context(patch.dict(sys.modules, {'pwd': SimpleNamespace()}))

    def test_cli_upgrade_requires_its_own_interactive_preservation_action(self):
        for other in ('--refresh-entry', '--enable-web-manager', '--detect-only', '--repair-address'):
            with self.subTest(other=other), patch.object(sys, 'argv', ['quick_install.py', '--upgrade-entry', other]), \
                    patch.object(quick, 'download') as download, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    quick.main()
                self.assertEqual(caught.exception.code, 2)
                download.assert_not_called()
        for arguments, tty in ((['--upgrade-entry', '--host', '1.1.1.1'], True),
                               (['--upgrade-entry', '--skip-firewall'], True), (['--upgrade-entry'], False)):
            with self.subTest(arguments=arguments, tty=tty), ExitStack() as stack:
                self.main_context(stack, arguments, tty)
                download = stack.enter_context(patch.object(quick, 'download'))
                with self.assertRaises(ValueError):
                    quick.main()
                download.assert_not_called()

    def test_main_dispatches_verified_upgrade_before_site_discovery(self):
        with ExitStack() as stack:
            self.main_context(stack, ['--upgrade-entry', '--nginx', '/verified/nginx'])
            stack.enter_context(patch.object(quick.shutil, 'which', return_value='/fixture/tool'))
            stack.enter_context(patch.object(quick, 'download', return_value=b'fixture'))
            stack.enter_context(patch.object(quick, 'verified_members', return_value={}))
            stack.enter_context(patch.object(quick, 'install_cache', return_value=ROOT))
            upgrade = stack.enter_context(patch.object(quick, 'upgrade_entry'))
            discovery = stack.enter_context(patch.object(quick, 'discover'))
            stack.enter_context(patch('builtins.print'))
            quick.main()
            upgrade.assert_called_once_with('/verified/nginx')
            discovery.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
