#!/usr/bin/env python3
"""Read-only public lifecycle evidence for the original panel queue service."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel_control as control
from pki import write_json


def main():
    control.validate_lab()
    source = Path('/etc/systemd/system/xboard-queue.service')
    content = source.read_bytes()
    flags = re.findall(r'--(?:max-time|max-jobs|memory|timeout|sleep|tries|backoff)=\d+', content.decode())
    fields = ['MainPID', 'ActiveState', 'SubState', 'NRestarts', 'Restart', 'RestartUSec', 'ExecMainStartTimestamp',
              'ExecMainExitTimestamp', 'ExecMainStatus', 'ExecMainCode', 'Result', 'User', 'FragmentPath', 'DropInPaths']
    args = ['systemctl', 'show', 'xboard-queue.service']
    for field in fields:
        args.extend(['-p', field])
    raw = subprocess.run(args, capture_output=True, check=True, timeout=30).stdout.decode()
    properties = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    # Only service manager lifecycle messages. Application logs are never shown.
    raw = subprocess.run(['journalctl', '-u', 'xboard-queue.service', '-n', '300', '-o', 'json', '--no-pager'],
                         capture_output=True, check=True, timeout=30).stdout.decode()
    journal = []
    for line in raw.splitlines():
        item = json.loads(line)
        message = item.get('MESSAGE', '')
        if item.get('SYSLOG_IDENTIFIER') != 'systemd' or not isinstance(message, str):
            continue
        if re.search(r'(Started |Stopped |Deactivated successfully|Scheduled restart job|Main process exited|Consumed )', message):
            journal.append({'at_us': item.get('__REALTIME_TIMESTAMP'), 'message': message})
    result = {'read_only': True, 'original_queue_properties': properties,
              'current_process_identity': control.process_identity(int(properties['MainPID'])),
              'service_file_sha256': hashlib.sha256(content).hexdigest(), 'service_file_mtime_ns': source.stat().st_mtime_ns,
              'snapshot_metadata_mtime_ns': (control.LAB / 'panel-control.json').stat().st_mtime_ns,
              'numeric_worker_lifetime_options': flags, 'systemd_lifecycle_messages': journal}
    redis_events = []
    raw = subprocess.run(['journalctl', '-u', 'redis-server.service', '-n', '100', '-o', 'json', '--no-pager'],
                         capture_output=True, check=True, timeout=30).stdout.decode()
    for line in raw.splitlines():
        item = json.loads(line)
        message = item.get('MESSAGE', '')
        if item.get('SYSLOG_IDENTIFIER') == 'systemd' and isinstance(message, str) and re.search(
                r'(Started |Stopped |Starting |Stopping |Deactivated successfully|Scheduled restart job|Main process exited|Consumed )', message):
            redis_events.append({'at_us': item.get('__REALTIME_TIMESTAMP'), 'message': message})
    result['redis_systemd_lifecycle_messages'] = redis_events
    result['redis_package_events'] = [line for line in Path('/var/log/dpkg.log').read_text().splitlines()
                                      if re.search(r' (?:redis-server|redis-tools|libjemalloc2)(?::[^ ]+)? ', line)][-30:]
    result['redis_unit_metadata'] = []
    for filename in ('/usr/lib/systemd/system/redis-server.service', '/etc/systemd/system/redis-server.service.d/jemalloc.conf'):
        path = Path(filename)
        if path.exists() and not path.is_symlink():
            result['redis_unit_metadata'].append({'path': filename, 'mtime_ns': path.stat().st_mtime_ns,
                                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    upgrade_log = Path('/var/log/unattended-upgrades/unattended-upgrades.log')
    result['unattended_upgrade_public_events'] = []
    if upgrade_log.is_file():
        result['unattended_upgrade_public_events'] = [line for line in upgrade_log.read_text().splitlines()
            if line.startswith('2026-10-10') and any(word in line for word in (
                'Starting unattended upgrades', 'Packages that will be upgraded:', 'All upgrades installed',
                'Writing dpkg log to'))][-15:]
    redis_source = Path('/etc/redis/redis.conf')
    result['redis_config_metadata'] = {'path': str(redis_source), 'sha256': hashlib.sha256(redis_source.read_bytes()).hexdigest(),
                                       'mtime_ns': redis_source.stat().st_mtime_ns}
    write_json(control.LAB / 'original-queue-lifecycle-public.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
