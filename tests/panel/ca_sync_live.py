#!/usr/bin/env python3
"""Root-only isolated PHP/SQLite CA import test; never changes the real panel."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import manager
from panel_ca import payload_from_state, sync_panel_ca
from pki import PKI, SafetyError, write_json


def run(args):
    assert sys.platform == 'linux' and os.geteuid() == 0
    owner = pwd.getpwnam(args.app_user)
    assert owner.pw_uid != 0
    with tempfile.TemporaryDirectory(prefix='nodeendpoint-ca-e2e-', dir='/opt') as directory:
        base = Path(directory)
        assert base.resolve().parent == Path('/opt') and base.name.startswith('nodeendpoint-ca-e2e-')
        base.chmod(0o755)
        app = base / 'app'
        for name in ('bootstrap/cache', 'storage/app', 'storage/framework/views', 'storage/logs',
                     'resources/views', 'plugins/NodeEndpoint/Services', 'plugins/NodeEndpoint/Controllers'):
            (app / name).mkdir(parents=True, exist_ok=True, mode=0o755)
        (app / 'artisan').write_text('<?php // fixture\n')
        (app / 'vendor').symlink_to(Path(args.vendor).resolve(), target_is_directory=True)
        shutil.copyfile(Path(__file__).with_name('ca_fixture_bootstrap.php'), app / 'bootstrap/app.php')
        for name in ('Services/Certificates.php', 'Services/Settings.php', 'Services/Endpoint.php', 'Controllers/SettingsController.php'):
            shutil.copyfile(ROOT / 'plugin/NodeEndpoint' / name, app / 'plugins/NodeEndpoint' / name)
        db = app / 'database.sqlite'
        settings = {'enabled': True, 'host': '127.0.0.1', 'port': 8443, 'https': True, 'ws_mode': 'rest'}
        def sql(statement, parameters=()):
            with sqlite3.connect(db) as connection:
                return connection.execute(statement, parameters).fetchall()
        sql('CREATE TABLE v2_plugins (id INTEGER PRIMARY KEY, code TEXT, name TEXT, version TEXT, is_enabled INTEGER)')
        sql('INSERT INTO v2_plugins VALUES (1, "node_endpoint", "NodeEndpoint", "1.0.0", 1)')
        sql('INSERT INTO v2_plugins VALUES (2, "foreign_fixture", "Foreign", "1.0", 1)')
        sql('CREATE TABLE node_endpoint_settings (id INTEGER PRIMARY KEY, settings TEXT)')
        sql('INSERT INTO node_endpoint_settings VALUES (1, ?)', (json.dumps(settings),))
        for path in [app, *app.rglob('*')]:
            if not path.is_symlink():
                os.chown(path, owner.pw_uid, owner.pw_gid)
                path.chmod(0o755 if path.is_dir() else 0o600)
        initial_db = db.read_bytes()
        state = manager.initialize_state(base / 'state')
        pki = PKI(state)
        password = 'fixture-only-ca-password-123456'
        pki.create('127.0.0.1', password)
        config = state / 'managed.conf'
        config.write_text('# ownership ' + 'a' * 32 + '\n')
        config.chmod(0o644)
        plan = {'host': '127.0.0.1', 'port': 8443, 'https': True, 'deployment': 'fpm',
                'app': str(app), 'app_user': owner.pw_name, 'php': args.php}
        write_json(state / 'installation.json', {'ownership': 'a' * 32, 'config_path': str(config),
                                               'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(), 'plan': plan})
        (state / 'manager.lock').touch(mode=0o600)
        private = pki.ca_dir() / 'ca.key.pem'
        private_hash = hashlib.sha256(private.read_bytes()).hexdigest()
        assert subprocess.run(['runuser', '-u', owner.pw_name, '--', 'test', '-r', str(private)]).returncode != 0
        first = sync_panel_ca(state)
        assert first['status'] == 'imported' and first['certificates'] == 1
        target = app / 'storage/app/node_endpoint/trust-bundle.pem'
        info = target.stat()
        assert info.st_uid == owner.pw_uid and stat.S_IMODE(info.st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert 'PRIVATE KEY' not in target.read_text()
        assert sync_panel_ca(state)['status'] == 'unchanged'
        # Verify the unchanged native controller displays the imported CA.
        code = 'require $argv[1]."/vendor/autoload.php"; $app=require $argv[1]."/bootstrap/app.php"; echo (new Plugin\\NodeEndpoint\\Controllers\\SettingsController)->show()->getContent();'
        show = subprocess.run(['runuser', '-u', owner.pw_name, '--', args.php, '-r', code, '--', str(app)], capture_output=True, timeout=30)
        assert show.returncode == 0
        assert len(json.loads(show.stdout)['data']['ca']) == 1
        assert db.read_bytes() == initial_db
        # A wrong independent fingerprint is refused by the actual plugin validator.
        _, payload = payload_from_state(state)
        payload['fingerprints'] = ['0' * 64]
        helper = (ROOT / 'tools/panel/sync_panel_ca.php').read_text()[len('<?php\n'):]
        rejected = subprocess.run(['runuser', '-u', owner.pw_name, '--', args.php, '-r', helper, '--', str(app)],
                                  input=json.dumps(payload).encode(), capture_output=True, timeout=30)
        assert rejected.returncode == 1 and json.loads(rejected.stdout)['error'] == 'ca_invalid'
        before = target.read_bytes()
        sql('UPDATE node_endpoint_settings SET settings=? WHERE id=1', (json.dumps({**settings, 'host': 'other.invalid'}),))
        try: sync_panel_ca(state)
        except SafetyError as error: assert '不一致' in str(error)
        else: raise AssertionError('Endpoint mismatch accepted')
        assert target.read_bytes() == before
        sql('UPDATE node_endpoint_settings SET settings=? WHERE id=1', (json.dumps(settings),))
        sql('UPDATE v2_plugins SET is_enabled=0 WHERE id=1')
        try: sync_panel_ca(state)
        except SafetyError as error: assert '未安装或启用' in str(error)
        else: raise AssertionError('Disabled plugin accepted')
        sql('UPDATE v2_plugins SET is_enabled=1 WHERE id=1')
        sentinel = app / 'storage/app/sentinel'
        sentinel.write_text('preserve foreign file')
        original = target.read_bytes()
        target.unlink()
        target.symlink_to(sentinel)
        try: sync_panel_ca(state)
        except SafetyError as error: assert '存储路径' in str(error)
        else: raise AssertionError('Symlink accepted')
        assert sentinel.read_text() == 'preserve foreign file'
        target.unlink()
        target.write_bytes(original)
        os.chown(target, owner.pw_uid, owner.pw_gid)
        target.chmod(0o600)
        pki.prepare_rotation(password)
        dual = sync_panel_ca(state)
        assert dual['certificates'] == 2 and len(dual['fingerprints']) == 2
        assert hashlib.sha256(private.read_bytes()).hexdigest() == private_hash
        assert sql('SELECT code, version, is_enabled FROM v2_plugins WHERE id=2') == [('foreign_fixture', '1.0', 1)]
        return {'result': 'PASS', 'single_ca_import': True, 'repeat_idempotent': True,
                'native_controller_sees_ca': True, 'non_root_file_uid': owner.pw_uid,
                'ca_file_mode': '0600', 'ca_directory_mode': '0700', 'private_key_unreadable_by_app': True,
                'private_key_unchanged': True, 'wrong_pin_rejected': True, 'endpoint_mismatch_rejected': True,
                'disabled_plugin_rejected': True, 'symlink_target_preserved': True, 'dual_ca_import': True,
                'foreign_plugin_row_preserved': True, 'production_database_used': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--php', required=True)
    parser.add_argument('--vendor', required=True)
    parser.add_argument('--app-user', required=True)
    print(json.dumps(run(parser.parse_args())))
