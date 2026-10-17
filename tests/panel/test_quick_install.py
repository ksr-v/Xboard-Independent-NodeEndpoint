"""Bootstrap integrity and evidence-based FPM discovery; no real service changes."""
import hashlib
import importlib.util
import io
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
    files = {'tools/panel/' + name: b'# fixture\n' for name in ('manager.py', 'nginx.py', 'pki.py', 'panel_ca.py', 'sync_panel_ca.php')}
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
        self.assertEqual(len(quick.verified_members(raw, pin)), 6)
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


if __name__ == '__main__':
    unittest.main()
