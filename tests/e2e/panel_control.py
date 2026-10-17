#!/usr/bin/env python3
"""Explicit phases for the authorized real panel E2E lab.

Stages/snapshots are private. Only start-queue starts the dedicated normal
traffic_fetch/stat/node_sync consumer; stage never starts services. Other
mutations require their named phase. Existing application files remain exact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import stat
import subprocess
import sys
import time

LAB = Path('/opt/node-endpoint-e2e-20261010')
APP = Path('/www/wwwroot/xboard')
PHP = Path('/www/server/php/83/bin/php')
NGINX = Path('/www/server/nginx/sbin/nginx')
QUEUE_UNIT = 'node-endpoint-e2e-queue-20261010.service'
WS_UNIT = 'node-endpoint-e2e-ws-20261010.service'
WS_PORT = 18076
BRIDGE_PORT = 18080
BRIDGE = Path('/www/server/panel/vhost/nginx/node-endpoint-e2e-bridge.conf')
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from manager import Environment, trusted_file
from pki import atomic_write, read_json, write_json, SafetyError


def require(condition, code):
    if not condition:
        raise SafetyError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, check=True):
    process = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=60, check=False)
    if check:
        require(process.returncode == 0, 'command_failed:' + Path(str(argv[0])).name)
    return process.returncode, process.stdout.decode(errors='replace').strip()


def properties(service):
    _, output = run(['systemctl', 'show', service, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'MainPID',
                     '-p', 'User', '-p', 'FragmentPath', '-p', 'DropInPaths'], check=False)
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def process_identity(pid):
    folder = Path('/proc') / str(pid)
    try:
        fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
        exe = (folder / 'exe').stat()
        return {'pid': int(pid), 'start': fields[19], 'uid': folder.stat().st_uid,
                'executable': str((folder / 'exe').resolve()), 'exe_dev': exe.st_dev, 'exe_ino': exe.st_ino}
    except (OSError, ValueError):
        return None


def validate_lab():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
    trusted_file(LAB)
    require(LAB.is_dir() and stat.S_IMODE(LAB.stat().st_mode) == 0o700
            and (LAB / '.node-endpoint-e2e-owner').is_file(), 'private_parent_lab_marker_required')
    trusted_file(PHP, True)
    trusted_file(NGINX, True)
    require(pwd.getpwnam('www').pw_uid != 0, 'nonroot_app_user_required')


def unit_text(role, token):
    command = ('queue:work --queue=traffic_fetch,stat,node_sync --sleep=1 --tries=1 --timeout=60'
               if role == 'queue' else f'ws-server start --host=127.0.0.1 --port={WS_PORT}')
    return (f'# NodeEndpoint E2E owned {token}\n[Unit]\nDescription=NodeEndpoint real panel E2E {role}\nAfter=network.target\n\n'
            f'[Service]\nType=simple\nUser=www\nGroup=www\nWorkingDirectory={APP}\n'
            f'ExecStart={PHP} {APP}/artisan {command}\nRestart=on-failure\nRestartSec=3\n'
            'KillMode=control-group\nTimeoutStopSec=30\nUMask=0077\nNoNewPrivileges=true\n'
            'StandardOutput=journal\nStandardError=journal\n')


def bridge_text(token):
    return f'''# NodeEndpoint E2E owned {token}
server {{
    listen 127.0.0.1:{BRIDGE_PORT};
    server_name node-endpoint-e2e-bridge.local;
    root {APP}/public;
    index index.php;
    access_log off;
    location = /ws {{
        proxy_pass http://127.0.0.1:{WS_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 90s;
    }}
    location / {{ try_files $uri $uri/ /index.php?$query_string; }}
    location ~ \\.php$ {{
        include /www/server/nginx/conf/fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:/tmp/php-cgi-83.sock;
    }}
}}
'''


def snapshot_file(path, snapshots):
    path = Path(path)
    require(not path.is_symlink(), 'snapshot_symlink_refused')
    label = digest(str(path).encode())
    if not path.exists():
        return {'path': str(path), 'present': False, 'label': label}
    require(path.is_file(), 'snapshot_regular_file_required')
    item = path.stat()
    data = path.read_bytes()
    atomic_write(snapshots / label, data)
    return {'path': str(path), 'present': True, 'label': label, 'sha256': digest(data),
            'uid': item.st_uid, 'gid': item.st_gid, 'mode': stat.S_IMODE(item.st_mode)}


def stage():
    meta_path = LAB / 'panel-control.json'
    if meta_path.exists():
        data = read_json(meta_path)
        require(data.get('schema') == 1 and data.get('lab') == str(LAB), 'existing_stage_ownership_differs')
        return data
    for service in (QUEUE_UNIT, WS_UNIT):
        require(properties(service).get('LoadState') == 'not-found'
                and not (Path('/etc/systemd/system') / service).exists(), 'fixture_unit_already_exists')
    require(not BRIDGE.exists() and not BRIDGE.is_symlink(), 'bridge_already_exists')
    for port in (WS_PORT, BRIDGE_PORT, 8443):
        with socket.socket() as stream:
            stream.bind(('127.0.0.1', port))
    environment = Environment(str(NGINX))
    snapshots = LAB / 'panel-snapshots'
    snapshots.mkdir(mode=0o700)
    files = [snapshot_file(path, snapshots) for path in environment.files]
    files.extend(snapshot_file(APP / 'storage/logs' / name, snapshots)
                 for name in ('xboard-ws-server.pid', 'xboard-ws-server.log'))
    protected = {}
    for pid in (22578, 41071, 127786, 124722, 45156):
        item = process_identity(pid)
        require(item is not None, 'expected_existing_process_missing')
        protected[str(pid)] = item
    queue = properties('xboard-queue.service')
    queue_pid = int(queue.get('MainPID', '0'))
    require(queue.get('ActiveState') == 'active' and queue_pid > 0, 'original_queue_service_missing')
    protected[str(queue_pid)] = process_identity(queue_pid)
    token = secrets.token_hex(16)
    units = {}
    for role, service in (('queue', QUEUE_UNIT), ('ws', WS_UNIT)):
        content = unit_text(role, token)
        atomic_write(LAB / service, content)
        units[role] = {'service': service, 'sha256': digest(content.encode())}
    data = {'schema': 1, 'lab': str(LAB), 'owner': token, 'protected_processes': protected,
            'original_queue_properties': queue, 'snapshots': files, 'units': units, 'started': {},
            'scope': 'real application; dedicated queue only traffic_fetch,stat,node_sync; stage starts nothing'}
    write_json(meta_path, data)
    return data


def protected_unchanged(data):
    policy = read_json(LAB / 'original-queue-continuity-policy.json', {})
    redis_policy = read_json(LAB / 'redis-external-upgrade-policy.json', {})
    original_queue_pid = data['original_queue_properties']['MainPID']
    for pid, expected in data['protected_processes'].items():
        if policy and pid == original_queue_pid:
            continue
        if redis_policy and pid == str(redis_policy['initial_process_identity']['pid']):
            continue
        observed = process_identity(int(pid))
        if observed != expected:
            write_json(LAB / ('changed-original-process-' + pid + '.json'), {'expected': expected, 'observed': observed})
            raise SafetyError('existing_service_process_identity_changed:' + pid)
    queue = properties('xboard-queue.service')
    if policy:
        require({key: value for key, value in queue.items() if key != 'MainPID'}
                == {key: value for key, value in data['original_queue_properties'].items() if key != 'MainPID'},
                'original_queue_service_definition_or_active_state_changed')
        source = Path(queue['FragmentPath'])
        require(not source.is_symlink() and digest(source.read_bytes()) == policy['unit_sha256'], 'original_queue_unit_changed')
        identity = process_identity(int(queue['MainPID']))
        expected = data['protected_processes'][original_queue_pid]
        require(identity and all(identity[key] == expected[key] for key in ('uid', 'executable', 'exe_dev', 'exe_ino')),
                'original_queue_runtime_identity_changed')
    else:
        require(queue == data['original_queue_properties'], 'original_queue_service_changed')
    if redis_policy:
        expected = redis_policy['observed_process_identity']
        require(process_identity(expected['pid']) == expected, 'post_upgrade_redis_process_identity_changed')
        for item in redis_policy['post_upgrade_files']:
            path = Path(item['path'])
            require(not path.is_symlink() and path.is_file() and digest(path.read_bytes()) == item['sha256'],
                    'post_upgrade_redis_configuration_changed')
            if 'uid' in item:
                current = path.stat()
                require(current.st_uid == item['uid'] and current.st_gid == item['gid']
                        and stat.S_IMODE(current.st_mode) == item['mode'], 'post_upgrade_redis_config_metadata_changed')
    for item in data['snapshots']:
        if 'storage/logs/xboard-ws-server.' in item['path']:
            continue
        path = Path(item['path'])
        require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == item['sha256'],
                'original_nginx_configuration_changed')
        current = path.stat()
        require(current.st_uid == item['uid'] and current.st_gid == item['gid']
                and stat.S_IMODE(current.st_mode) == item['mode'], 'original_nginx_file_metadata_changed')


def acknowledge_queue_self_restart(data):
    evidence = read_json(LAB / 'original-queue-lifecycle-public.json', {})
    require(evidence.get('read_only') and '--max-time=3600' in evidence.get('numeric_worker_lifetime_options', [])
            and evidence['original_queue_properties']['Restart'] == 'always'
            and evidence['original_queue_properties']['RestartUSec'] == '5s'
            and int(evidence['original_queue_properties']['NRestarts']) > 0, 'original_hourly_self_restart_evidence_required')
    original = data['original_queue_properties']
    queue = properties('xboard-queue.service')
    require({key: value for key, value in original.items() if key != 'MainPID'}
            == {key: value for key, value in queue.items() if key != 'MainPID'}, 'original_queue_definition_changed')
    source = Path(queue['FragmentPath'])
    require(not source.is_symlink() and digest(source.read_bytes()) == evidence['service_file_sha256'], 'queue_evidence_unit_hash_changed')
    expected = data['protected_processes'][original['MainPID']]
    actual = process_identity(int(queue['MainPID']))
    require(actual and all(actual[key] == expected[key] for key in ('uid', 'executable', 'exe_dev', 'exe_ino'))
            and int(actual['start']) > int(expected['start']), 'same_queue_executable_uid_later_start_required')
    require(any('Deactivated successfully' in item['message'] for item in evidence['systemd_lifecycle_messages'])
            and any('Scheduled restart job' in item['message'] for item in evidence['systemd_lifecycle_messages']),
            'normal_systemd_self_restart_events_required')
    policy = {'schema': 1, 'unit_sha256': evidence['service_file_sha256'], 'initial_process_identity': expected,
              'observed_process_identity': actual, 'lifetime_seconds': 3600, 'systemd_restart': 'always',
              'evidence': 'original-queue-lifecycle-public.json',
              'statement': 'Original queue worker retains its hourly/always restart policy; after five natural hourly exits a distro Redis package upgrade caused one dependency failure/restart. Test never stops, restarts or rewrites it.'}
    write_json(LAB / 'original-queue-continuity-policy.json', policy)
    protected_unchanged(data)
    return {'original_queue_continuity_verified': True, 'initial_pid': expected['pid'], 'current_pid': actual['pid'],
            'automatic_hourly_restart_expected': True, 'original_queue_unit_sha256': policy['unit_sha256'],
            'other_original_process_pid_start_strictly_preserved': True}


def acknowledge_external_redis_upgrade(data):
    evidence = read_json(LAB / 'original-queue-lifecycle-public.json', {})
    require(any('2026-10-10 06:36:23 upgrade redis-server:' in line for line in evidence.get('redis_package_events', []))
            and any('All upgrades installed' in line for line in evidence.get('unattended_upgrade_public_events', [])),
            'external_redis_package_upgrade_evidence_required')
    expected = data['protected_processes']['124722']
    _, text = run(['systemctl', 'show', 'redis-server.service', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'FragmentPath', '-p', 'DropInPaths'])
    state = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    require(state.get('ActiveState') == 'active' and state.get('FragmentPath') == '/usr/lib/systemd/system/redis-server.service'
            and state.get('DropInPaths') == '/etc/systemd/system/redis-server.service.d/jemalloc.conf', 'expected_upgraded_redis_service_definition_required')
    observed = process_identity(int(state['MainPID']))
    require(observed and observed['uid'] == expected['uid'] and observed['executable'] == expected['executable']
            and int(observed['start']) > int(expected['start']), 'expected_new_redis_process_uid_path_required')
    trusted_file(observed['executable'], True)
    files = evidence['redis_unit_metadata'] + [evidence['redis_config_metadata']]
    for item in files:
        if item['path'] == '/etc/redis/redis.conf':
            # Ubuntu deliberately assigns /etc/redis (2770) and this 0640
            # config to redis. Pin its existing identity; do not chmod/chown.
            path = Path(item['path'])
            entry, parent = path.lstat(), path.parent.lstat()
            require(stat.S_ISREG(entry.st_mode) and not path.parent.is_symlink() and entry.st_uid == observed['uid']
                    and parent.st_uid == observed['uid'] and stat.S_IMODE(entry.st_mode) == 0o640
                    and stat.S_IMODE(parent.st_mode) == 0o2770, 'expected_distro_redis_config_owner_mode_required')
            item['uid'], item['gid'], item['mode'] = entry.st_uid, entry.st_gid, stat.S_IMODE(entry.st_mode)
        else:
            trusted_file(item['path'])
        require(digest(Path(item['path']).read_bytes()) == item['sha256'], 'redis_maintenance_evidence_file_changed')
    policy = {'schema': 1, 'initial_process_identity': expected, 'observed_process_identity': observed,
              'post_upgrade_files': files, 'evidence': 'original-queue-lifecycle-public.json',
              'statement': 'Distro unattended upgrade .4 to .5 independently restarted Redis; initial Redis PID was not preserved. Root fixture never stops/restarts/rewrites Redis; subsequent process/config are strictly pinned.'}
    write_json(LAB / 'redis-external-upgrade-policy.json', policy)
    protected_unchanged(data)
    return {'external_redis_upgrade_recorded': True, 'initial_pid': expected['pid'], 'post_upgrade_pid': observed['pid'],
            'initial_redis_pid_preserved': False, 'test_requested_redis_restart': False,
            'original_website_nginx_fpm_mariadb_process_identities_preserved': True}


def stage_bridge(data):
    content = bridge_text(data['owner'])
    expected = digest(content.encode())
    path = LAB / 'node-endpoint-e2e-bridge.conf'
    if path.exists():
        require(not path.is_symlink() and digest(path.read_bytes()) == expected, 'staged_bridge_changed')
    else:
        atomic_write(path, content)
    data['bridge'] = {'path': str(BRIDGE), 'sha256': expected}
    write_json(LAB / 'panel-control.json', data)


def install_bridge(data):
    stage_bridge(data)
    require(owned_runtime('ws', data).get('ActiveState') == 'active', 'own_ws_worker_must_be_started_first')
    _, listeners = run(['ss', '-H', '-ltnp', 'sport', '=', ':' + str(WS_PORT)])
    require('127.0.0.1:' + str(WS_PORT) in listeners, 'worker_loopback_listener_missing')
    import re
    pids = re.findall(r'pid=(\d+)', listeners)
    require(pids, 'worker_listener_identity_missing')
    for pid in pids:
        require('/system.slice/' + WS_UNIT in (Path('/proc') / pid / 'cgroup').read_text(), 'worker_listener_not_in_own_unit')
    environment = Environment(str(NGINX))
    environment.nginx(['-t'])
    require(not BRIDGE.exists() and not BRIDGE.is_symlink(), 'bridge_path_already_exists')
    trusted_file(BRIDGE.parent)
    try:
        atomic_write(BRIDGE, (LAB / 'node-endpoint-e2e-bridge.conf').read_bytes(), 0o644)
        environment.nginx(['-t'])
        environment.nginx(['-s', 'reload'])
        environment.refresh()
        require(str(BRIDGE) in environment.files, 'bridge_not_loaded')
        protected_unchanged(data)
    except Exception:
        if BRIDGE.is_file() and not BRIDGE.is_symlink() and digest(BRIDGE.read_bytes()) == data['bridge']['sha256']:
            BRIDGE.unlink()
            environment.nginx(['-t'])
            environment.nginx(['-s', 'reload'])
        raise
    return {'bridge_installed': True, 'port': BRIDGE_PORT, 'worker_port': WS_PORT,
            'original_serverblock_bytes_and_expected_service_guards_verified': True}


def remove_bridge(data):
    require(not (Path('/var/lib/node-endpoint-e2e-20261010') / 'installation.json').exists(), 'uninstall_root_entry_before_bridge')
    if not BRIDGE.exists():
        return {'bridge_already_absent': True}
    require(not BRIDGE.is_symlink() and digest(BRIDGE.read_bytes()) == data.get('bridge', {}).get('sha256'), 'owned_bridge_changed_refuse_remove')
    environment = Environment(str(NGINX))
    previous = BRIDGE.read_bytes()
    BRIDGE.unlink()
    try:
        environment.nginx(['-t'])
        environment.nginx(['-s', 'reload'])
    except Exception:
        atomic_write(BRIDGE, previous, 0o644)
        environment.nginx(['-t'])
        environment.nginx(['-s', 'reload'])
        raise
    protected_unchanged(data)
    return {'bridge_removed': True, 'original_serverblock_bytes_and_expected_service_guards_verified': True}


def owned_runtime(role, data):
    entry = data['units'][role]
    service = entry['service']
    path = Path('/etc/systemd/system') / service
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and digest(path.read_bytes()) == entry['sha256'], 'owned_unit_hash_or_owner_differs')
    status = properties(service)
    require(status.get('FragmentPath') == str(path) and not status.get('DropInPaths'), 'owned_unit_source_differs')
    return status


def start_unit(role, data):
    entry = data['units'][role]
    service = entry['service']
    source = LAB / service
    require(digest(source.read_bytes()) == entry['sha256'], 'staged_unit_changed')
    target = Path('/etc/systemd/system') / service
    if not target.exists():
        require(properties(service).get('LoadState') == 'not-found', 'unit_name_conflict')
        atomic_write(target, source.read_bytes(), 0o644)
        run(['systemctl', 'daemon-reload'])
    owned_runtime(role, data)
    run(['systemctl', 'start', service])
    time.sleep(1)
    status = owned_runtime(role, data)
    require(status.get('ActiveState') == 'active', 'owned_service_not_active')
    pid = int(status.get('MainPID', '0'))
    identity = process_identity(pid)
    require(identity and identity['uid'] == pwd.getpwnam('www').pw_uid
            and identity['executable'] == str(PHP), 'owned_service_runtime_identity_differs')
    data['started'][role] = identity
    write_json(LAB / 'panel-control.json', data)
    protected_unchanged(data)
    return {'service': service, 'pid': pid, 'active': True, 'original_config_bytes_and_expected_service_guards_verified': True}


def stop_unit(role, data):
    entry = data['units'][role]
    target = Path('/etc/systemd/system') / entry['service']
    if not target.exists():
        require(properties(entry['service']).get('MainPID', '0') == '0', 'missing_unit_still_running')
        return {'service': entry['service'], 'already_absent': True}
    status = owned_runtime(role, data)
    if int(status.get('MainPID', '0')):
        current = process_identity(int(status['MainPID']))
        require(current and current['executable'] == str(PHP) and current['uid'] == pwd.getpwnam('www').pw_uid,
                'unexpected_process_refuse_stop')
    run(['systemctl', 'stop', entry['service']])
    status = owned_runtime(role, data)
    require(status.get('ActiveState') == 'inactive' and status.get('MainPID') == '0', 'owned_service_not_stopped')
    if role == 'ws':
        require(data['started'].get('ws'), 'owned_worker_start_record_required')
        stopped_files = {}
        for name in ('xboard-ws-server.pid', 'xboard-ws-server.log'):
            path = APP / 'storage/logs' / name
            require(not path.is_symlink() and (not path.exists() or path.is_file()), 'owned_worker_file_type_changed')
            stopped_files[name] = {'present': path.exists()}
            if path.exists():
                item = path.stat()
                require(item.st_uid == pwd.getpwnam('www').pw_uid, 'owned_worker_file_owner_changed')
                stopped_files[name].update(sha256=digest(path.read_bytes()), dev=item.st_dev, ino=item.st_ino)
        data['stopped_ws_files'] = stopped_files
        write_json(LAB / 'panel-control.json', data)
    target.unlink()
    run(['systemctl', 'daemon-reload'])
    protected_unchanged(data)
    return {'service': entry['service'], 'stopped_and_unit_removed': True, 'original_config_bytes_and_expected_service_guards_verified': True}


def restore_worker_files(data):
    """After stopping our worker, restore exactly the two snapshotted files."""
    service = data['units']['ws']['service']
    status = properties(service)
    require(status.get('MainPID', '0') == '0' and status.get('ActiveState') in ('inactive', 'failed'),
            'stop_owned_ws_before_restoring_app_files')
    require(not BRIDGE.exists(), 'remove_bridge_before_restoring_worker_files')
    require(data['started'].get('ws') and data.get('stopped_ws_files'), 'owned_worker_stopped_file_inventory_required')
    if data.get('worker_files_restored'):
        for item in data['snapshots']:
            if 'storage/logs/xboard-ws-server.' not in item['path']:
                continue
            path = Path(item['path'])
            require(not path.is_symlink(), 'restored_worker_file_symlink')
            if item['present']:
                current = path.stat()
                require(path.is_file() and digest(path.read_bytes()) == item['sha256'] and current.st_uid == item['uid']
                        and current.st_gid == item['gid'] and stat.S_IMODE(current.st_mode) == item['mode'],
                        'restored_worker_file_changed')
            else:
                require(not path.exists(), 'restored_absent_worker_file_created')
        protected_unchanged(data)
        return {'original_worker_log_and_pid_files_already_restored': True}
    saved = LAB / 'panel-snapshots'
    pending = []
    for item in data['snapshots']:
        if 'storage/logs/xboard-ws-server.' not in item['path']:
            continue
        path = Path(item['path'])
        require(path.parent == APP / 'storage/logs' and path.name in ('xboard-ws-server.pid', 'xboard-ws-server.log'),
                'unexpected_worker_snapshot_path')
        require(not path.is_symlink() and (not path.exists() or path.is_file()), 'worker_file_type_changed')
        current = path.read_bytes() if path.exists() else None
        final = data['stopped_ws_files'][path.name]
        if final['present']:
            present = path.stat() if path.exists() else None
            require(present and current is not None and digest(current) == final['sha256']
                    and present.st_dev == final['dev'] and present.st_ino == final['ino'], 'stopped_worker_file_changed')
        else:
            require(current is None, 'foreign_worker_file_created_after_stop')
        if current is not None:
            atomic_write(LAB / ('final-owned-' + path.name), current)
        original = (saved / item['label']).read_bytes() if item['present'] else None
        if original is not None:
            require(digest(original) == item['sha256'], 'original_worker_snapshot_damaged')
        pending.append((path, item, current, original))
    # Recheck all current bytes before touching either file; external changes
    # leave all files and evidence in place for coordinated manual recovery.
    for path, item, current, original in pending:
        require(not path.is_symlink() and (path.read_bytes() if path.exists() else None) == current,
                'worker_file_changed_during_restore')
    for path, item, current, original in pending:
        if original is None:
            if path.exists():
                path.unlink()
        else:
            atomic_write(path, original, item['mode'])
            os.chown(path, item['uid'], item['gid'])
    for path, item, _, original in pending:
        if original is None:
            require(not path.exists(), 'originally_absent_worker_file_not_restored')
        else:
            current = path.stat()
            require(path.read_bytes() == original and current.st_uid == item['uid'] and current.st_gid == item['gid']
                    and stat.S_IMODE(current.st_mode) == item['mode'], 'original_worker_file_metadata_not_restored')
    protected_unchanged(data)
    data['worker_files_restored'] = True
    write_json(LAB / 'panel-control.json', data)
    return {'original_worker_log_and_pid_files_restored': True, 'private_final_log_evidence_retained': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['stage', 'start-queue', 'start-ws', 'stop-queue', 'stop-ws',
                                        'install-bridge', 'remove-bridge', 'restore-worker-files', 'ack-queue-self-restart',
                                        'ack-external-redis-upgrade', 'check'])
    args = parser.parse_args()
    validate_lab()
    import fcntl
    fd = os.open(LAB / '.panel-control.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = stage()
        stage_bridge(data)
        if args.phase.startswith('start-'):
            output = start_unit(args.phase[6:], data)
        elif args.phase.startswith('stop-'):
            output = stop_unit(args.phase[5:], data)
        elif args.phase == 'install-bridge':
            output = install_bridge(data)
        elif args.phase == 'remove-bridge':
            output = remove_bridge(data)
        elif args.phase == 'restore-worker-files':
            output = restore_worker_files(data)
        elif args.phase == 'ack-queue-self-restart':
            output = acknowledge_queue_self_restart(data)
        elif args.phase == 'ack-external-redis-upgrade':
            output = acknowledge_external_redis_upgrade(data)
        else:
            protected_unchanged(data)
            output = {'staged': True, 'phase': args.phase, 'ws_port': WS_PORT, 'bridge_port': BRIDGE_PORT,
                      'original_config_bytes_and_expected_service_guards_verified': True}
        write_json(LAB / ('panel-phase-' + args.phase + '.json'), output)
        print(json.dumps(output))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if LAB.is_dir() and os.geteuid() == 0:
            write_json(LAB / ('panel-failure-' + str(time.time_ns()) + '.json'),
                       {'failure_type': type(error).__name__, 'detail': str(error)})
        import re
        reason = str(error) if re.fullmatch(r'[a-z0-9_:]+', str(error)) else 'private_failure_evidence_retained'
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__, 'reason': reason}), file=sys.stderr)
        sys.exit(1)
