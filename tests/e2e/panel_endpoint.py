#!/usr/bin/env python3
"""Explicit real Root CLI/TLS phases; nothing runs automatically.

Must be invoked by the coordinating test owner after REST baseline/identity
handoff. Private CLI logs and the synthetic CA passphrase stay in the Root lab.
The only firewall rule belongs to source Node VM 192.168.128.129, TCP8443.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import ssl
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel_control as control
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from pki import atomic_write, read_json, write_json, SafetyError

STATE = Path('/var/lib/node-endpoint-e2e-20261010')
HOST = '192.168.128.128'
NODE_HOST = '192.168.128.129'
PORT = 8443
FIREWALL_COMMENT = 'NodeEndpoint-E2E-20261010-owned'
LAB = control.LAB
MANAGER = LAB / 'tools/panel/manager.py'
PUBLIC_STAGE = Path('/home/codex/node-endpoint-e2e-stage-20261010')


def require(condition, code):
    if not condition:
        raise SafetyError(code)


def private_meta():
    path = LAB / 'panel-endpoint.json'
    if path.exists():
        value = read_json(path)
        require(value.get('schema') == 1 and value.get('state') == str(STATE), 'endpoint_metadata_differs')
        return value
    value = {'schema': 1, 'state': str(STATE), 'host': HOST, 'port': PORT, 'phases': [], 'firewall_owned': False}
    write_json(path, value)
    return value


def cli(action, stdin='', extra=()):
    args = [sys.executable, str(MANAGER), '--state', str(STATE), '--nginx', str(control.NGINX), action]
    if action == 'install':
        args += ['--host', HOST, '--port', str(PORT), '--include-dir', str(control.BRIDGE.parent),
                 '--site-config', str(control.BRIDGE), '--site-name', 'node-endpoint-e2e-bridge.local',
                 '--app', str(control.APP), '--deployment', 'fpm', '--backend', 'unix:/tmp/php-cgi-83.sock',
                 '--app-user', 'www', '--php', str(control.PHP), '--enable-ws',
                 '--ws-backend', 'http://127.0.0.1:' + str(control.WS_PORT), '--confirm', f'INSTALL {HOST}:{PORT}']
    elif action == 'renew':
        args += ['--confirm', 'REPLACE SERVER KEY ' + HOST]
    elif action == 'uninstall':
        args += ['--confirm', 'UNINSTALL NODE ENDPOINT ALL NODES MIGRATED']
    args.extend(str(item) for item in extra)
    stamp = str(time.time_ns())
    result = subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=120, check=False)
    atomic_write(LAB / f'endpoint-{stamp}-{action}.stdout', result.stdout.encode())
    atomic_write(LAB / f'endpoint-{stamp}-{action}.stderr', result.stderr.encode())
    require(result.returncode == 0, 'root_cli_failed:' + action)
    decoder, last, offset = json.JSONDecoder(), None, 0
    while offset < len(result.stdout):
        start = result.stdout.find('{', offset)
        if start < 0:
            break
        try:
            last, consumed = decoder.raw_decode(result.stdout[start:])
            offset = start + consumed
        except ValueError:
            offset = start + 1
    require(isinstance(last, dict), 'root_cli_missing_json')
    return last


def password(next_ca=False):
    path = LAB / ('.endpoint-fixture-next-ca-passphrase' if next_ca else '.endpoint-fixture-ca-passphrase')
    if not path.exists():
        if next_ca:
            require(STATE.is_dir() and not read_json(STATE / 'pki/state.json').get('rotation'), 'next_passphrase_requires_unprepared_rotation')
        else:
            require(not STATE.exists(), 'cannot_create_passphrase_for_existing_state')
        atomic_write(path, secrets.token_urlsafe(48) + '\n')
    require(not path.is_symlink() and path.stat().st_uid == 0 and stat.S_IMODE(path.stat().st_mode) == 0o600,
            'private_fixture_passphrase_file_required')
    return path.read_text().strip()


def public_summary(status):
    return {'installed': status.get('installed'), 'endpoint': status.get('endpoint'), 'ca': status.get('ca'),
            'server': status.get('server'), 'checks': status.get('checks'), 'trust': status.get('trust'),
            'rotation': status.get('rotation'), 'ca_key_online': status.get('ca_key_online')}


def publish_ca(meta, name, ca_ids):
    """Copy certificates only. Full certificate fingerprints travel separately."""
    item = PUBLIC_STAGE.lstat()
    require(not PUBLIC_STAGE.is_symlink() and PUBLIC_STAGE.is_dir() and item.st_uid == pwd.getpwnam('codex').pw_uid
            and stat.S_IMODE(item.st_mode) == 0o700, 'owned_codex_public_stage_required')
    pem, fingerprints = '', []
    for identity in ca_ids:
        require(re.fullmatch('[0-9a-f]{16}', identity), 'invalid_ca_identity')
        path = STATE / 'pki/ca' / identity / 'ca.pem'
        raw = path.read_text()
        blocks = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', raw, re.S)
        require(len(blocks) == 1 and raw.strip() == blocks[0] and 'PRIVATE KEY' not in raw, 'public_certificate_only_required')
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(blocks[0])).hexdigest()
        pem += blocks[0] + '\n'
        fingerprints.append(fingerprint)
    path = PUBLIC_STAGE / ('ca-' + name + '.pem')
    previous = meta.setdefault('public_ca_files', {}).get(name)
    if path.exists() or path.is_symlink():
        require(previous and not path.is_symlink() and path.is_file()
                and control.digest(path.read_bytes()) == previous['file_sha256'], 'public_ca_destination_changed')
    atomic_write(path, pem, 0o644)
    result = {'path': str(path), 'fingerprints': fingerprints, 'file_sha256': control.digest(pem.encode()),
              'certificate_count': len(ca_ids)}
    meta['public_ca_files'][name] = result
    write_json(LAB / 'panel-endpoint.json', meta)
    return result


def private_export(meta, next_ca=False):
    pki_meta = read_json(STATE / 'pki/state.json')
    if next_ca:
        identity = (pki_meta.get('rotation') or {}).get('new_ca') or pki_meta['active_ca']
        require(identity != meta['initial_ca_id'], 'new_ca_export_requires_distinct_ca')
    else:
        identity = meta['initial_ca_id']
    path = LAB / ('offline-ca-' + identity)
    certificate = STATE / 'pki/ca' / identity / 'ca.pem'
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
    value = password(next_ca)
    if not path.exists():
        cli('export-ca', value + '\n', ['--destination', path, '--ca-id', identity])
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == 0
            and stat.S_IMODE(path.stat().st_mode) == 0o700, 'root_private_export_required')
    for name in ('ca.pem', 'ca.key.pem', 'manifest.json'):
        item = (path / name).lstat()
        require(stat.S_ISREG(item.st_mode) and item.st_uid == 0 and stat.S_IMODE(item.st_mode) == 0o600,
                'root_private_export_file_required')
    require((path / 'ca.key.pem').read_text().startswith('-----BEGIN ENCRYPTED PRIVATE KEY-----'), 'encrypted_export_key_required')
    require(cli('verify-export', value + '\n', ['--destination', path, '--ca-id', identity,
                                                '--fingerprint', fingerprint]).get('verified'), 'export_cli_verification_required')
    meta.setdefault('private_exports', {})[identity] = {'path': str(path), 'fingerprint': fingerprint, 'verified': True}
    write_json(LAB / 'panel-endpoint.json', meta)
    return {'ca_fingerprint': fingerprint, 'encrypted_root_private_export_verified': True}, identity, path


def remove_online_key(meta, next_ca=False):
    result, identity, path = private_export(meta, next_ca)
    fingerprint = result['ca_fingerprint']
    value = cli('remove-online-ca-key', password(next_ca) + '\n',
                ['--destination', path, '--ca-id', identity, '--fingerprint', fingerprint,
                 '--confirm', 'REMOVE ONLINE CA KEY ' + fingerprint])
    require(value.get('online_key_removed') and not (STATE / 'pki/ca' / identity / 'ca.key.pem').exists(),
            'online_ca_key_not_removed')
    return {**result, 'online_ca_key_removed': True}


def firewall_open(meta):
    _, listing = control.run(['ufw', 'status'])
    require(listing.startswith('Status: active'), 'expected_active_ufw_required')
    if meta['firewall_owned']:
        require(FIREWALL_COMMENT in listing, 'owned_firewall_rule_missing')
        return {'firewall_rule_already_owned': True}
    require(FIREWALL_COMMENT not in listing and not any('8443' in line and NODE_HOST in line for line in listing.splitlines()),
            'preexisting_node8443_rule_refused')
    atomic_write(LAB / 'ufw-before.stdout', listing + '\n')
    control.run(['ufw', 'allow', 'proto', 'tcp', 'from', NODE_HOST, 'to', 'any', 'port', str(PORT), 'comment', FIREWALL_COMMENT])
    _, after = control.run(['ufw', 'status'])
    require(FIREWALL_COMMENT in after, 'owned_firewall_rule_not_observed')
    meta['firewall_owned'] = True
    write_json(LAB / 'panel-endpoint.json', meta)
    return {'firewall_owned': True, 'source': NODE_HOST, 'tcp_port': PORT}


def firewall_close(meta):
    if not meta['firewall_owned']:
        return {'firewall_no_owned_rule': True}
    _, listing = control.run(['ufw', 'status'])
    require(FIREWALL_COMMENT in listing, 'owned_firewall_comment_changed')
    control.run(['ufw', '--force', 'delete', 'allow', 'proto', 'tcp', 'from', NODE_HOST, 'to', 'any', 'port', str(PORT),
                 'comment', FIREWALL_COMMENT])
    _, after = control.run(['ufw', 'status'])
    before = (LAB / 'ufw-before.stdout').read_text().strip()
    require(after == before, 'firewall_original_listing_not_restored')
    meta['firewall_owned'] = False
    write_json(LAB / 'panel-endpoint.json', meta)
    return {'owned_firewall_rule_removed': True, 'original_firewall_listing_restored': True}


def final_report(meta, guarded):
    control.protected_unchanged(guarded)
    _, firewall = control.run(['ufw', 'status'])
    firewall_before = (LAB / 'ufw-before.stdout').read_text().strip() if (LAB / 'ufw-before.stdout').exists() else None
    cleanup = {'root_entry_absent': not (STATE / 'installation.json').exists(),
               'owned_nginx_files_absent': not control.BRIDGE.exists() and not (control.BRIDGE.parent / 'node-endpoint.conf').exists(),
               'owned_firewall_rule_absent_original_listing_exact': not meta['firewall_owned']
                   and FIREWALL_COMMENT not in firewall and firewall_before is not None and firewall == firewall_before,
               'owned_units_absent': all(not (Path('/etc/systemd/system') / name).exists()
                    and control.properties(name).get('MainPID', '0') == '0'
                    for name in (control.WS_UNIT, control.QUEUE_UNIT)),
               'original_worker_file_content_mode_uid_gid_restored': bool(guarded.get('worker_files_restored')),
               'original_nginx_config_bytes_mode_uid_gid_preserved': True}
    required = {'install', 'idempotent', 'export-current', 'remove-current-online', 'rekey', 'rollback',
                'rotate-prepare', 'rotate-activate', 'rotate-finish', 'export-next', 'remove-next-online',
                'uninstall', 'firewall-open', 'firewall-close'}
    phases = {item['phase'] for item in meta['phases']}
    queue_policy = read_json(LAB / 'original-queue-continuity-policy.json', {})
    redis_policy = read_json(LAB / 'redis-external-upgrade-policy.json', {})
    return {'schema_version': 1, 'passed': required.issubset(phases) and all(cleanup.values()),
            'scope': 'Actual panel VM native Laravel/PHP-FPM; real Root CLI, private CA export/removal, rekey and dual CA rotation',
            'host': HOST, 'port': PORT, 'nginx_version': control.Environment(str(control.NGINX)).version,
            'original_nginx_configs': [{key: item[key] for key in ('path', 'sha256', 'mode', 'uid', 'gid')}
                                      for item in guarded['snapshots'] if item['present'] and 'storage/logs/xboard-ws-server.' not in item['path']],
            'initial_service_process_identities': list(guarded['protected_processes'].values()),
            'strict_initial_pid_start_preserved': [item for key, item in guarded['protected_processes'].items()
                                                 if key not in ('124722', guarded['original_queue_properties']['MainPID'])],
            'original_queue_continuity': queue_policy, 'external_redis_maintenance': redis_policy,
            'maintenance_public_evidence': read_json(LAB / 'original-queue-lifecycle-public.json', {}),
            'public_ca_files': meta.get('public_ca_files', {}), 'phases': meta['phases'], 'cleanup': cleanup,
            'missing_required_phases': sorted(required - phases),
            'authenticated_native_node_checks': 'Recorded separately by the real Node integration runner; Root CLI unauthenticated probe does not authenticate nodes.',
            'private_evidence_retained_on_panel_vm': True, 'private_keys_or_passphrases_exported_from_panel_vm': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['install', 'idempotent', 'status', 'rekey', 'rollback', 'uninstall', 'firewall-open', 'firewall-close',
                                        'export-current', 'remove-current-online', 'rotate-prepare', 'rotate-activate',
                                        'rotate-finish', 'export-next', 'remove-next-online', 'report'])
    parser.add_argument('--ack-dual-trust', help='Explicit node-side verified new CA fingerprint; required before activation')
    parser.add_argument('--ack-new-trust', help='Explicit node-side verified new-only CA fingerprint; required before retiring old trust')
    args = parser.parse_args()
    control.validate_lab()
    import fcntl
    fd = os.open(LAB / '.panel-endpoint.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        meta = private_meta()
        guarded = read_json(LAB / 'panel-control.json')
        control.protected_unchanged(guarded)
        if args.phase == 'firewall-open':
            output = firewall_open(meta)
        elif args.phase == 'firewall-close':
            output = firewall_close(meta)
        elif args.phase == 'report':
            output = final_report(meta, guarded)
        elif args.phase == 'install':
            require(control.BRIDGE.is_file() and control.digest(control.BRIDGE.read_bytes()) == guarded['bridge']['sha256'], 'owned_bridge_required')
            value = password()
            result = cli('install', value + '\n' + value + '\n')
            status = result['status']
            require(result['changed'] and status['checks'].get('tls') == 'verified', 'live_installed_tls_required')
            meta['initial'] = public_summary(status)
            meta['initial_ca_id'] = read_json(STATE / 'pki/state.json')['active_ca']
            output = {**public_summary(status), 'public_ca': publish_ca(meta, 'current', [meta['initial_ca_id']])}
        elif args.phase == 'status':
            output = public_summary(cli('status'))
        elif args.phase == 'idempotent':
            before = read_json(STATE / 'installation.json')
            pki_before = read_json(STATE / 'pki/state.json')
            result = cli('install')
            require(not result['changed'] and read_json(STATE / 'installation.json') == before
                    and read_json(STATE / 'pki/state.json') == pki_before, 'identical_install_must_be_no_change')
            status = cli('status')
            require(status['checks'].get('tls') == 'verified', 'idempotent_install_live_tls_required')
            output = {**public_summary(status), 'changed': False, 'installation_and_pki_unchanged': True}
        elif args.phase == 'rekey':
            before = cli('status')
            installation = read_json(STATE / 'installation.json')
            leaf = read_json(STATE / 'pki/state.json')['active_leaf']
            previous_key = hashlib.sha256((STATE / 'pki/server' / leaf / 'server.key.pem').read_bytes()).hexdigest()
            extra = []
            ca_id = read_json(STATE / 'pki/state.json')['active_ca']
            if not (STATE / 'pki/ca' / ca_id / 'ca.key.pem').exists():
                export = meta.get('private_exports', {}).get(ca_id)
                require(export and export['verified'], 'verified_offline_ca_export_required_for_rekey')
                extra = ['--ca-key', Path(export['path']) / 'ca.key.pem']
            after = cli('renew', password() + '\n', extra)['status']
            leaf = read_json(STATE / 'pki/state.json')['active_leaf']
            next_key = hashlib.sha256((STATE / 'pki/server' / leaf / 'server.key.pem').read_bytes()).hexdigest()
            require(before['ca']['sha256'] == after['ca']['sha256'] and before['server']['sha256'] != after['server']['sha256']
                    and previous_key != next_key and after['checks'].get('tls') == 'verified', 'same_ca_new_key_cert_live_required')
            require(read_json(STATE / 'installation.json')['plan'] == installation['plan'], 'rekey_endpoint_plan_changed')
            meta['before_rekey'], meta['after_rekey'] = public_summary(before), public_summary(after)
            output = {**public_summary(after), 'same_ca': True, 'server_key_replaced': True}
        elif args.phase == 'rollback':
            status = cli('rollback', 'ROLLBACK NODE ENDPOINT\n')['status']
            require(status['server']['sha256'] == meta['before_rekey']['server']['sha256'], 'rollback_expected_leaf_not_restored')
            verified = cli('status')
            require(verified['checks'].get('tls') == 'verified', 'rollback_live_tls_required')
            output = {**public_summary(verified), 'expected_original_leaf_restored': True}
        elif args.phase in ('export-current', 'export-next'):
            output, _, _ = private_export(meta, args.phase == 'export-next')
        elif args.phase in ('remove-current-online', 'remove-next-online'):
            output = remove_online_key(meta, args.phase == 'remove-next-online')
        elif args.phase == 'rotate-prepare':
            before = cli('status')
            value = password(True)
            status = cli('rotate-prepare', value + '\n' + value + '\n')['status']
            rotation = read_json(STATE / 'pki/state.json')['rotation']
            verified = cli('status')
            require(status['ca']['sha256'] == before['ca']['sha256'] and status['server']['sha256'] == before['server']['sha256']
                    and len(status['trust']) == 2 and verified['checks'].get('tls') == 'verified', 'dual_trust_must_keep_old_live_leaf')
            dual = publish_ca(meta, 'dual', [rotation['old_ca'], rotation['new_ca']])
            next_ca = publish_ca(meta, 'next', [rotation['new_ca']])
            meta['next_ca_fingerprint'] = next_ca['fingerprints'][0]
            output = {**public_summary(verified), 'public_dual_ca': dual, 'public_next_ca': next_ca}
        elif args.phase == 'rotate-activate':
            fingerprint = meta['next_ca_fingerprint']
            require(args.ack_dual_trust == fingerprint, 'node_dual_trust_acknowledgment_required')
            status = cli('rotate-activate', extra=['--fingerprint', fingerprint,
                                                 '--confirm', 'ALL NODES TRUST BOTH CA ' + fingerprint])['status']
            require(status['ca']['sha256'] == fingerprint and len(status['trust']) == 2
                    and status['checks'].get('tls') == 'verified', 'new_ca_live_dual_trust_required')
            output = {**public_summary(status), 'node_dual_trust_acknowledged': True}
        elif args.phase == 'rotate-finish':
            fingerprint = meta['next_ca_fingerprint']
            require(args.ack_new_trust == fingerprint, 'node_new_only_trust_acknowledgment_required')
            cli('rotate-finish', extra=['--fingerprint', fingerprint, '--confirm', 'ALL NODES USE NEW CA ' + fingerprint])
            status = cli('status')
            require(status['ca']['sha256'] == fingerprint and len(status['trust']) == 1 and not status['rotation']
                    and status['checks'].get('tls') == 'verified', 'new_ca_only_live_required')
            output = {**public_summary(status), 'node_new_only_trust_acknowledged': True}
        else:
            status = cli('uninstall')['status']
            require(not status['installed'], 'root_entry_still_installed')
            output = public_summary(status)
        control.protected_unchanged(guarded)
        stamp = time.time_ns()
        if args.phase != 'report':
            meta['phases'].append({'phase': args.phase, 'at_ns': stamp, 'result': output})
        write_json(LAB / 'panel-endpoint.json', meta)
        write_json(LAB / ('endpoint-phase-' + args.phase + '.json'), output)
        public_stage = PUBLIC_STAGE.lstat()
        require(not PUBLIC_STAGE.is_symlink() and PUBLIC_STAGE.is_dir()
                and public_stage.st_uid == pwd.getpwnam('codex').pw_uid
                and stat.S_IMODE(public_stage.st_mode) == 0o700, 'owned_codex_public_stage_required')
        public_report = PUBLIC_STAGE / f'endpoint-phase-{stamp}-{args.phase}.json'
        require(not public_report.exists() and not public_report.is_symlink(), 'existing_public_phase_report_refused')
        write_json(public_report, {'phase': args.phase, 'at_ns': stamp, 'result': output}, 0o644)
        print(json.dumps(output))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
