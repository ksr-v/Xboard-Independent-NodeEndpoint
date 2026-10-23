"""Owned VM128 panel fixture for isolated rate-limit/routing traffic tests.

All application output, including initial admin credentials and seed output,
stays under the Root-only private directory. Public stdout is fixed metadata.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
os.umask(0o077)
BASE = Path('/opt/xboard-rate-route-20261024')
PRIVATE = BASE / 'private'
NAME = 'codex-xboard-rate-route-20261024'
VOLUME = NAME + '-redis'
IMAGE = 'sha256:f8d45147fe23db138f0922b6137d162b283f90c2129ca6775c507ba084e00d73'
PORT = 17004
LABEL = 'com.codex.rate-route-fixture'
OWNER = {'tool': 'Codex Xboard rate and route fixture', 'path': str(BASE), 'date': '2026-10-24'}
MOUNTS = {'.env': '/www/.env', 'storage-app': '/www/storage/app', '.dockerdata': '/www/.docker/.data', 'plugins': '/www/plugins'}
ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
       'HOME': str(PRIVATE), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


class FixtureError(Exception):
    pass


def require(value, reason):
    if not value:
        raise FixtureError(reason)


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    path.chmod(0o600)


def read(path):
    return json.loads(path.read_text())


def root():
    require(os.geteuid() == 0 and sys.platform == 'linux', 'root-linux-required')


def owned():
    root()
    require(BASE.is_dir() and not BASE.is_symlink() and BASE.resolve() == BASE, 'fixture-directory-differs')
    require(read(BASE / 'owner.json') == OWNER, 'fixture-marker-differs')
    require(PRIVATE.is_dir() and not PRIVATE.is_symlink(), 'private-directory-differs')
    for path in [BASE, PRIVATE]:
        require(path.stat().st_uid == 0 and stat.S_IMODE(path.stat().st_mode) == 0o700, 'fixture-privacy-differs')


def run(argv, label, *, data=None, timeout=90, check=True):
    require(re.fullmatch('[a-z0-9-]+', label), 'diagnostic-label-differs')
    try:
        result = subprocess.run([str(x) for x in argv], input=data, capture_output=True,
                                cwd=PRIVATE, env=ENV, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        (PRIVATE / (label + '.out')).write_bytes(error.stdout or b'')
        (PRIVATE / (label + '.err')).write_bytes(error.stderr or b'')
        raise FixtureError(label + '-timeout')
    (PRIVATE / (label + '.out')).write_bytes(result.stdout)
    (PRIVATE / (label + '.err')).write_bytes(result.stderr)
    if check:
        require(result.returncode == 0, label + '-exit-' + str(result.returncode))
    return result


def docker(args, label, **kwargs):
    return run(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', PRIVATE / 'docker-config', *args], label, **kwargs)


def inspect(name=NAME):
    result = docker(['container', 'inspect', name], 'inspect-' + ('main' if name == NAME else 'install'), check=False)
    if result.returncode:
        require(result.returncode == 1, 'container-inspect-failed')
        return None
    rows = json.loads(result.stdout)
    require(len(rows) == 1, 'container-inspect-count-differs')
    row = rows[0]
    require(row['Config'].get('Labels', {}).get(LABEL) == str(BASE), 'container-owner-differs')
    require(row['Image'] == IMAGE, 'container-image-differs')
    binds = {item['Destination']: item['Source'] for item in row['Mounts'] if item['Type'] == 'bind'}
    require(binds == {target: str(BASE / source) for source, target in MOUNTS.items()}, 'container-binds-differ')
    return row


def common():
    args = ['--label', LABEL + '=' + str(BASE)]
    for source, target in MOUNTS.items():
        args += ['--mount', 'type=bind,src=' + str(BASE / source) + ',dst=' + target]
    args += ['--mount', 'type=volume,src=' + VOLUME + ',dst=/data']
    for key, value in {'ENABLE_SQLITE': 'true', 'ENABLE_REDIS': 'true',
                       'ADMIN_ACCOUNT': 'rate-route@example.test', 'RESOURCE_PROFILE': 'minimal',
                       'OCTANE_WORKERS': '1', 'OCTANE_TASK_WORKERS': '1',
                       'HORIZON_DATA_PIPELINE_MAX': '1', 'HORIZON_BUSINESS_MAX': '1',
                       'HORIZON_NOTIFICATION_MAX': '1'}.items():
        args += ['--env', key + '=' + value]
    return args


def prepare():
    root()
    if BASE.exists():
        owned()
        require((BASE / 'state.json').is_file(), 'fixture-state-missing')
        print(json.dumps({'result': 'PASS', 'phase': 'prepared', 'reused': True, 'port': PORT}))
        return
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', PORT))
    BASE.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    (PRIVATE / 'docker-config').mkdir(mode=0o700)
    write(BASE / 'owner.json', OWNER)
    write(BASE / 'state.json', {'phase': 'preparing'})
    require(inspect() is None and inspect(NAME + '-install') is None, 'container-name-in-use')
    image = docker(['image', 'inspect', IMAGE, '--format', '{{.Id}}'], 'image-check')
    require(image.stdout.decode().strip() == IMAGE, 'required-image-missing')
    existing = docker(['volume', 'inspect', VOLUME], 'volume-check', check=False)
    require(existing.returncode == 1, 'volume-name-in-use')
    docker(['volume', 'create', '--label', LABEL + '=' + str(BASE), VOLUME], 'volume-create')
    (BASE / '.env').touch(mode=0o600)
    for name in ['storage-app', '.dockerdata', 'plugins']:
        path = BASE / name
        path.mkdir(mode=0o755)
        os.chown(path, 1000, 1000)
    write(BASE / 'state.json', {'phase': 'prepared', 'image': IMAGE})
    print(json.dumps({'result': 'PASS', 'phase': 'prepared', 'reused': False, 'port': PORT}))


def install():
    owned()
    state = read(BASE / 'state.json')
    if state['phase'] in ['installed', 'running']:
        print(json.dumps({'result': 'PASS', 'phase': 'installed', 'reused': True}))
        return
    require(state['phase'] == 'prepared', 'install-state-differs')
    require((BASE / '.env').stat().st_size == 0, 'env-not-empty-before-install')
    require(inspect() is None and inspect(NAME + '-install') is None, 'fixture-container-already-exists')
    write(BASE / 'state.json', {'phase': 'install-started', 'image': IMAGE})
    docker(['run', '--rm', '--name', NAME + '-install', *common(), IMAGE,
            'php', 'artisan', 'xboard:install', '--database=sqlite', '--no-interaction'], 'install', timeout=240)
    require((BASE / '.dockerdata/database.sqlite').is_file(), 'installed-database-missing')
    require(re.search(r'^INSTALLED=(true|1)$', (BASE / '.env').read_text(), re.M), 'installed-env-marker-missing')
    write(BASE / 'state.json', {'phase': 'installed', 'image': IMAGE})
    print(json.dumps({'result': 'PASS', 'phase': 'installed', 'credential_output': 'root-private-only'}))


def start():
    owned()
    require(read(BASE / 'state.json')['phase'] in ['installed', 'running'], 'start-state-differs')
    row = inspect()
    if row:
        require(row['State']['Running'], 'owned-container-not-running')
    else:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', PORT))
        docker(['run', '-d', '--name', NAME, *common(), '--publish', '127.0.0.1:' + str(PORT) + ':7001', IMAGE], 'start', timeout=120)
    write(BASE / 'state.json', {'phase': 'running', 'image': IMAGE})
    verify()


def verify():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    require(row['HostConfig'].get('PortBindings') == {'7001/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}]}, 'port-binding-differs')
    http_status = None
    for _ in range(45):
        try:
            connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=2)
            connection.request('GET', '/')
            response = connection.getresponse()
            http_status = response.status
            response.read(1048576)
            connection.close()
            if http_status == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    require(http_status == 200, 'fixture-http-not-200')
    db_path = BASE / '.dockerdata/database.sqlite'
    require(db_path.is_file() and not db_path.is_symlink(), 'database-path-differs')
    with sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT code, version, is_enabled FROM v2_plugins WHERE code IN ('node_endpoint','smart_expiry','qrcodeextend','flowscope') ORDER BY code").fetchall()
    expected = {'node_endpoint': '1.0.7', 'smart_expiry': '1.2.0', 'qrcodeextend': '1.2.0', 'flowscope': '2.4.0'}
    require({code: version for code, version, enabled in rows} == expected and all(enabled for code, version, enabled in rows), 'bundled-plugins-differ')
    report = {'result': 'PASS', 'phase': 'verified', 'container': NAME, 'container_id': row['Id'],
              'image': row['Image'], 'http_status': http_status, 'port': PORT,
              'plugins': [{'code': code, 'version': version, 'enabled': bool(enabled)} for code, version, enabled in rows],
              'database': 'isolated-sqlite', 'host_nginx_changed': False}
    write(BASE / 'verification.json', report)
    print(json.dumps(report))


def php_load():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    data = sys.stdin.buffer.read(1048577)
    require(0 < len(data) <= 1048576 and data.lstrip().startswith(b'<?php'), 'php-seed-input-differs')
    digest = hashlib.sha256(data).hexdigest()
    (PRIVATE / 'seed-program.php').write_bytes(data)
    code = "$p='/tmp/codex-rate-route-seed-20261024.php'; $d=stream_get_contents(STDIN); if(file_put_contents($p,$d)!==strlen($d)){exit(1);} chmod($p,0600);"
    docker(['exec', '--user', '1000:1000', '--workdir', '/www', '-i', NAME, 'php', '-r', code], 'seed-program-load', data=data)
    write(PRIVATE / 'seed-program.json', {'sha256': digest})
    print(json.dumps({'result': 'PASS', 'phase': 'php-load', 'input_sha256': digest}))


def seed():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    require((PRIVATE / 'seed-program.json').is_file(), 'seed-program-not-loaded')
    digest = read(PRIVATE / 'seed-program.json')['sha256']
    action = sys.argv[2] if len(sys.argv) == 3 else 'seed'
    require(action in ['seed', 'inspect', 'credentials', 'mutate', 'routes', 'audit', 'observer-audit'], 'seed-action-differs')
    data = sys.stdin.buffer.read(1048577)
    require(len(data) <= 1048576, 'seed-input-too-large')
    if data.strip():
        require(isinstance(json.loads(data), dict), 'seed-input-must-be-object')
    label = 'seed-' + action + '-' + str(time.time_ns())
    result = docker(['exec', '--user', '1000:1000', '--workdir', '/www',
                     '--env', 'XBOARD_RATE_ROUTE_FIXTURE=20261024', '-i', NAME, 'php',
                     '/tmp/codex-rate-route-seed-20261024.php', action], label, data=data, timeout=120)
    write(PRIVATE / 'last-seed.json', {'label': label, 'input_sha256': digest, 'exit': result.returncode})
    print(json.dumps({'result': 'PASS', 'phase': 'seed', 'input_sha256': digest, 'exit': 0,
                      'action': action, 'output_path': str(PRIVATE / (label + '.out')), 'output_bytes': len(result.stdout)}))


def cleanup():
    owned()
    require(inspect(NAME + '-install') is None, 'install-container-still-present')
    row = inspect()
    if row:
        docker(['container', 'rm', '-f', NAME], 'cleanup-container', timeout=90)
    require(inspect() is None, 'cleanup-container-remains')
    state = read(BASE / 'state.json')
    if state['phase'] == 'running':
        state['phase'] = 'installed'
    write(BASE / 'state.json', state)
    print(json.dumps({'result': 'PASS', 'phase': 'cleanup', 'container_removed': bool(row), 'data_retained': True}))


if __name__ == '__main__':
    try:
        require(2 <= len(sys.argv) <= 3, 'one-phase-required')
        phase = sys.argv[1]
        require(phase in ['prepare', 'install', 'start', 'verify', 'php-load', 'seed', 'cleanup'], 'phase-differs')
        require(len(sys.argv) == 2 or phase == 'seed', 'extra-argument-differs')
        globals()[phase.replace('-', '_')]()
    except FixtureError as error:
        print(json.dumps({'result': 'FAIL', 'reason': str(error)}))
        sys.exit(1)
    except Exception as error:
        print(json.dumps({'result': 'FAIL', 'reason': 'fixture-operation-failed', 'type': type(error).__name__}))
        sys.exit(1)
