"""Bootstrap integrity and evidence-based FPM discovery; no real service changes."""
import hashlib
from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import manager
spec = importlib.util.spec_from_file_location('quick_install', ROOT / 'tools/panel/quick_install.py')
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)


def bundle(extra=None, symlink=False):
    files = {'tools/panel/' + name: b'# fixture\n' for name in ('manager.py', 'nginx.py', 'pki.py', 'panel_ca.py', 'sync_panel_ca.php', 'firewall.py')}
    if extra:
        files.update(extra)
    manifest = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in files.items())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            if symlink and name == 'tools/panel/manager.py':
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, data)
            else:
                archive.writestr(name, data)
        archive.writestr('MANIFEST.sha256', manifest)
    raw = stream.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


class BootstrapTests(unittest.TestCase):
    def test_wrong_pin_rejected_before_zip_or_file_operations(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            quick.verified_members(b'not a zip')

    def test_pinned_members_and_manifest_verified(self):
        raw, pin = bundle()
        self.assertEqual(len(quick.verified_members(raw, pin)), 7)
        changed = bytearray(raw)
        changed[-1] ^= 1
        with self.assertRaises(ValueError):
            quick.verified_members(bytes(changed), pin)

    def test_traversal_and_symlinks_refused_even_with_matching_outer_pin(self):
        for raw, pin in (bundle({'../outside': b'no'}), bundle(symlink=True)):
            with self.assertRaises(ValueError):
                quick.verified_members(raw, pin)

    def test_cache_repeat_verifies_every_file_and_refuses_changes(self):
        raw, pin = bundle()
        members = quick.verified_members(raw, pin)
        with tempfile.TemporaryDirectory() as base, patch.object(quick, 'secure_path', side_effect=lambda p: Path(p).absolute()):
            target = Path(base) / 'cache'
            quick.install_cache(members, target)
            quick.install_cache(members, target)
            (target / 'tools/panel/manager.py').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '缓存已被修改'):
                quick.install_cache(members, target)

    def test_cache_foreign_directory_not_overwritten(self):
        with tempfile.TemporaryDirectory() as base, patch.object(quick, 'secure_path', side_effect=lambda p: Path(p).absolute()):
            target = Path(base) / 'cache'
            target.mkdir()
            foreign = target / 'foreign'
            foreign.write_text('preserve')
            with self.assertRaises(ValueError):
                quick.install_cache({}, target)
            self.assertEqual(foreign.read_text(), 'preserve')

    def test_non_https_redirect_refused(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            quick.HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'http://example.test/tools.zip')

    def test_fpm_site_discovery_expands_php_include_and_separates_ws(self):
        with tempfile.TemporaryDirectory() as base:
            app = Path(base) / 'xboard'
            (app / 'public').mkdir(parents=True)
            (app / 'artisan').write_text('fixture')
            (app / 'public/index.php').write_text('fixture')
            public = (app / 'public').as_posix()
            env = SimpleNamespace(prefix='/etc/nginx', includes=['/etc/nginx/sites-enabled/*'], files={
                '/etc/nginx/sites-enabled/panel.conf': 'server { server_name panel.test; root "' + public + '"; include /etc/nginx/php.conf; location = /ws { proxy_pass http://127.0.0.1:8076; } }',
                '/etc/nginx/php.conf': 'location ~ \\.php$ { fastcgi_pass unix:/run/php.sock; }'})
            result = quick.discover(env, manager)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['app'], str(app))
            self.assertEqual(result[0]['backend'], 'unix:/run/php.sock')
            self.assertEqual(result[0]['include_dir'], '/etc/nginx/sites-enabled')
            self.assertEqual(result[0]['ws_backend'], 'http://127.0.0.1:8076')
            env.files['/etc/nginx/php.conf'] += ' location /other { fastcgi_pass unix:/run/other.sock; }'
            self.assertEqual(quick.discover(env, manager), [])

    def test_multiple_candidates_require_explicit_choice(self):
        with patch('builtins.input', side_effect=['', '0', '2']), patch('builtins.print'):
            self.assertEqual(quick.select(['first', 'second'], '站点'), 'second')

    def test_tools_without_firewall_rejected_even_with_valid_pin_and_manifest(self):
        raw, _ = bundle()
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, 'w') as target:
            members = {name: source.read(name) for name in source.namelist()
                       if name not in ('tools/panel/firewall.py', 'MANIFEST.sha256')}
            for name, data in members.items():
                target.writestr(name, data)
            target.writestr('MANIFEST.sha256', ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                                      for name, data in members.items()))
        data = output.getvalue()
        with self.assertRaisesRegex(ValueError, '防火墙'):
            quick.verified_members(data, hashlib.sha256(data).hexdigest())


class InstallFollowupTests(unittest.TestCase):
    def test_firewall_failure_still_attempts_ca_and_keeps_entry(self):
        with patch.object(quick, 'configure_firewall', side_effect=RuntimeError('firewall failed')), \
                patch('panel_ca.sync_panel_ca', return_value={'status': 'imported'}) as ca, \
                patch.object(manager, 'execute') as install, patch('builtins.print'):
            with self.assertRaises(SystemExit) as caught:
                quick.finish_install()
            self.assertEqual(caught.exception.code, 2)
            ca.assert_called_once_with()
            install.assert_not_called()

    def test_unsupported_firewall_reported_without_blocking_ca(self):
        with patch.object(quick, 'configure_firewall', return_value={'status': 'manual_required'}), \
                patch('panel_ca.sync_panel_ca', return_value={'status': 'unchanged'}) as ca, patch('builtins.print'):
            with self.assertRaises(SystemExit) as caught:
                quick.finish_install()
            self.assertEqual(caught.exception.code, 2)
            ca.assert_called_once_with()

    def test_skip_firewall_still_syncs_ca(self):
        with patch.object(quick, 'configure_firewall') as firewall, \
                patch('panel_ca.sync_panel_ca', return_value={'status': 'imported'}) as ca, patch('builtins.print'):
            quick.finish_install(skip_firewall=True)
            firewall.assert_not_called()
            ca.assert_called_once_with()

    def test_ca_failure_reports_ca_retry_after_successful_firewall(self):
        with patch.object(quick, 'configure_firewall', return_value={'status': 'ensured'}) as firewall, \
                patch('panel_ca.sync_panel_ca', side_effect=ValueError('fixture mismatch')), patch('builtins.print') as output:
            with self.assertRaises(SystemExit) as caught:
                quick.finish_install()
            self.assertEqual(caught.exception.code, 2)
            firewall.assert_called_once_with()
            self.assertIn('--sync-ca', str(output.call_args_list))

    def test_noninteractive_actions_mutually_exclusive(self):
        for args in (['--detect-only', '--configure-firewall'], ['--sync-ca', '--configure-firewall'],
                     ['--detect-only', '--sync-ca']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['quick_install.py', *args]), \
                    patch.object(quick, 'download') as download, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    quick.main()
                self.assertEqual(caught.exception.code, 2)
                download.assert_not_called()

    def test_firewall_only_needs_no_tty_ca_or_nginx_discovery_and_reports_failure(self):
        for status in ('ensured', 'saved_inactive', 'manual_required', 'error'):
            with self.subTest(status=status), ExitStack() as stack:
                stack.enter_context(patch.object(sys, 'argv', ['quick_install.py', '--configure-firewall']))
                stack.enter_context(patch.object(sys, 'platform', 'linux'))
                stack.enter_context(patch.object(sys, 'path', list(sys.path)))
                stack.enter_context(patch.object(sys, 'stdin', SimpleNamespace(isatty=lambda: False)))
                stack.enter_context(patch.object(quick.os, 'geteuid', return_value=0, create=True))
                stack.enter_context(patch.object(quick.os, 'umask'))
                stack.enter_context(patch.dict(sys.modules, {'pwd': SimpleNamespace()}))
                stack.enter_context(patch.object(quick.shutil, 'which', return_value='/fixture/tool'))
                stack.enter_context(patch.object(quick, 'download', return_value=b'fixture'))
                stack.enter_context(patch.object(quick, 'verified_members', return_value={}))
                stack.enter_context(patch.object(quick, 'install_cache', return_value=ROOT))
                firewall = stack.enter_context(patch.object(quick, 'configure_firewall', return_value={'status': status}))
                discovery = stack.enter_context(patch.object(manager, 'Environment'))
                install = stack.enter_context(patch.object(manager, 'execute'))
                ca = stack.enter_context(patch('panel_ca.sync_panel_ca'))
                stack.enter_context(patch('builtins.print'))
                if status in ('manual_required', 'error'):
                    with self.assertRaises(SystemExit) as caught:
                        quick.main()
                    self.assertEqual(caught.exception.code, 2)
                else:
                    quick.main()
                firewall.assert_called_once_with()
                discovery.assert_not_called()
                install.assert_not_called()
                ca.assert_not_called()


if __name__ == '__main__':
    unittest.main()
