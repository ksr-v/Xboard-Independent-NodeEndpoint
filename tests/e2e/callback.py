#!/usr/bin/env python3
"""Restricted, ephemeral SSH callbacks between the two authorized lab VMs.

Install Root-owned in the private panel lab. A forced authorized_keys command
passes SSH_ORIGINAL_COMMAND as one argument. Credentials only cross private
stdin/stdout, never argv; the node runner saves callback output Root-0600.
"""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

STAGE = Path('/opt/node-endpoint-e2e-20261010')
RUN = 'e2e-20261010'
APP = '/www/wwwroot/xboard'
PHP = '/www/server/php/83/bin/php'
PORT_PROTOCOLS = {32181: 'vless', 32182: 'vless', 32183: 'socks', 32184: 'socks'}


def retain_child_failure(action, value, stdout, stderr, failure, exit_code=None):
    """Keep potentially sensitive child errors only in the owned Root lab."""
    folder = STAGE / 'callback-diagnostics'
    if not folder.exists():
        folder.mkdir(mode=0o700)
    info = folder.stat()
    if folder.is_symlink() or info.st_uid != 0 or info.st_mode & 0o777 != 0o700:
        raise ValueError('callback_diagnostics_not_private')
    stem = str(time.time_ns()) + '-' + action + '-' + uuid.uuid4().hex
    metadata = {'action': action, 'failure': failure, 'exit_code': exit_code}
    if action == 'proxy':
        metadata.update(protocol=value['protocol'], node_id=int(value['node_id']),
                        port=int(value['port']))
    for label, output in (('stdout', stdout), ('stderr', stderr)):
        raw = output or b''
        if isinstance(raw, str):
            raw = raw.encode('utf-8', errors='replace')
        metadata[label + '_bytes'] = len(raw)
        metadata[label + '_truncated'] = len(raw) > 1048576
        path = folder / (stem + '-' + label + '.private')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw[:1048576])
    path = folder / (stem + '-metadata.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(metadata, handle, sort_keys=True)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise ValueError('callback_requires_root_and_one_action')
    action = sys.argv[1]
    if action not in ('inspect', 'rotate', 'rotate-observer', 'rotate-delta', 'credentials-refresh', 'proxy'):
        raise ValueError('callback_action_rejected')
    if not (STAGE / '.node-endpoint-e2e-owner').is_file():
        raise ValueError('callback_owner_marker_missing')
    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536:
        raise ValueError('callback_input_too_large')
    value = json.loads(data)
    if value.get('run_id') != RUN:
        raise ValueError('callback_run_mismatch')
    if action == 'credentials-refresh':
        if set(value) != {'run_id'}:
            raise ValueError('callback_unexpected_parameters')
        credentials = STAGE / (RUN + '-credentials.json')
        info = credentials.stat()
        if info.st_uid != 0 or info.st_mode & 0o077 or credentials.is_symlink():
            raise ValueError('callback_credentials_not_private')
        sys.stdout.buffer.write(credentials.read_bytes())
        return
    if action in ('inspect', 'rotate', 'rotate-observer', 'rotate-delta'):
        if set(value) != {'run_id'}:
            raise ValueError('callback_unexpected_parameters')
        argv = [PHP, str(STAGE / 'panel_probe.php'), 'rotate' if action.startswith('rotate-') else action,
                '--app=' + APP, '--stage=' + str(STAGE), '--run=' + RUN]
        if action in ('rotate', 'rotate-observer', 'rotate-delta'):
            argv.append('--confirm=ROTATE_SYNTHETIC_USER_ONLY')
            if action == 'rotate-observer':
                argv.append('--sync-mode=observer-only')
            elif action == 'rotate-delta':
                argv.append('--sync-mode=delta')
        request = None
    else:
        if set(value) - {'run_id', 'protocol', 'host', 'port', 'echo_port', 'target',
                         'uuid', 'bytes', 'hold_seconds', 'expected_source', 'node_id'}:
            raise ValueError('callback_unexpected_parameters')
        if str(ipaddress.ip_address(value['host'])) != '192.168.128.129':
            raise ValueError('callback_node_host_rejected')
        if PORT_PROTOCOLS.get(int(value['port'])) != value['protocol']:
            raise ValueError('callback_node_port_rejected')
        if int(value['node_id']) != int(value['port']) - 32180:
            raise ValueError('callback_node_id_rejected')
        if value.get('target', '127.0.0.1') != '127.0.0.1':
            raise ValueError('callback_target_rejected')
        if value.get('expected_source') != '192.168.128.128':
            raise ValueError('callback_source_rejected')
        uuid.UUID(value['uuid'])
        if not 1024 <= int(value['echo_port']) <= 65535:
            raise ValueError('callback_echo_port_rejected')
        if not 1 <= int(value.get('bytes', 1048576)) <= 8388608:
            raise ValueError('callback_payload_rejected')
        if not 0 <= float(value.get('hold_seconds', 2)) <= 10:
            raise ValueError('callback_duration_rejected')
        argv = ['/usr/bin/python3', str(STAGE / 'tests/e2e/node_runner.py'),
                '--action', 'probe-stdin']
        request = data
    try:
        process = subprocess.run(argv, input=request, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=90, check=False)
    except subprocess.TimeoutExpired as error:
        retain_child_failure(action, value, error.stdout, error.stderr, 'timeout')
        raise ValueError('callback_private_action_timeout') from None
    if process.returncode != 0:
        # Host application exceptions and private subprocess output stay private.
        retain_child_failure(action, value, process.stdout, process.stderr,
                             'nonzero_exit', process.returncode)
        raise ValueError('callback_private_action_failed')
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeDecodeError):
        retain_child_failure(action, value, process.stdout, process.stderr,
                             'invalid_json', process.returncode)
        raise ValueError('callback_private_action_invalid_json') from None
    if not isinstance(result, dict) or result.get('status') == 'ERROR':
        retain_child_failure(action, value, process.stdout, process.stderr,
                             'rejected_result', process.returncode)
        raise ValueError('callback_private_action_rejected')
    sys.stdout.buffer.write(process.stdout)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'ERROR', 'reason': 'restricted_callback_rejected'}))
        sys.exit(1)
