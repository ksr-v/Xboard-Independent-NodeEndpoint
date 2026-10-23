#!/usr/bin/env python3
"""Root-authorized, local public-CA transfer to the non-root Xboard application."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import subprocess
import sys
sys.dont_write_bytecode = True

import manager
from pki import PKI, SafetyError, read_json


def payload_from_state(state):
    state = manager.safe_state(state)
    marker = read_json(manager.trusted_file(state / manager.STATE_MARKER))
    if marker.get('tool') != 'NodeEndpoint' or marker.get('path') != str(state) or marker.get('schema_version') != 1:
        raise SafetyError('Root state ownership marker differs')
    manager.trusted_file(state / 'installation.json')
    installed = manager.Manager(state, None).assert_owned()
    manager.trusted_file(installed['config_path'])
    plan = installed['plan']
    if plan.get('https') and plan.get('deployment') == 'docker' and plan.get('docker_context'):
        from docker_context import validate_context
        context = validate_context(plan['docker_context'])
        if (plan.get('container') != context['container_id']
                or plan.get('backend') != 'http://127.0.0.1:' + str(context['host_port'])
                or Path(plan.get('app', '')).resolve() != Path(context['working_dir']).resolve()):
            raise SafetyError('Docker Root plan differs from its recorded execution context')
    elif not plan.get('https') or plan.get('deployment') not in ('fpm', 'octane'):
        raise SafetyError('自动导入 CA 仅支持同机 HTTPS 的 FPM/Octane 面板')
    manager.trusted_file(state / 'pki/state.json')
    pki = PKI(state)
    meta = pki.meta
    ids = meta.get('trusted_ca', [])
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 2
            or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{16}', value) for value in ids)
            or len(set(ids)) != len(ids)):
        raise SafetyError('Invalid Root CA trust state')
    pem = ''
    pins = []
    for identity in ids:
        path = manager.trusted_file(pki.ca_dir(identity) / 'ca.pem')
        if path.stat().st_size > 16384:
            raise SafetyError('CA certificate exceeds size limit')
        cert = path.read_text(encoding='ascii')
        blocks = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', cert, re.S)
        if len(blocks) != 1 or cert.strip() != blocks[0] or 'PRIVATE KEY' in cert:
            raise SafetyError('Only public Root CA certificates may be transferred')
        pem += blocks[0] + '\n'
        pins.append(hashlib.sha256(ssl.PEM_cert_to_DER_cert(blocks[0])).hexdigest())
    bundle = manager.trusted_file(pki.root / 'trust-bundle.pem')
    if bundle.stat().st_size > 32768 or bundle.read_text().strip() != pem.strip() or len(set(pins)) != len(pins):
        raise SafetyError('Root trust bundle differs from trusted CA certificates')
    return plan, {'pem': pem, 'fingerprints': pins,
                  'endpoint': {key: plan[key] for key in ('host', 'port', 'https')}}


def _sync_docker_ca(plan, payload):
    from docker_context import validate_context, run_helper
    context = validate_context(plan['docker_context'])
    response = run_helper(context, 'sync_panel_ca', payload)
    if (not isinstance(response, dict) or set(response) != {'status', 'fingerprints', 'certificates', 'uid'}
            or response.get('status') not in ('imported', 'unchanged')
            or response.get('fingerprints') != payload['fingerprints']
            or type(response.get('certificates')) is not int or response['certificates'] != len(payload['fingerprints'])
            or type(response.get('uid')) is not int or response['uid'] != context['uid']):
        raise SafetyError('Docker public CA import response differs from Root trust or application identity')
    return {key: response[key] for key in ('status', 'fingerprints', 'certificates')}


def sync_panel_ca(state='/var/lib/node-endpoint'):
    manager.linux_root()
    import fcntl
    state = manager.safe_state(state)
    lock = manager.trusted_file(state / 'manager.lock')
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        if details.st_uid != 0 or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o600:
            raise SafetyError('Root manager lock must be private and owned by Root')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SafetyError('Another Root manager operation is in progress') from error
        plan, payload = payload_from_state(state)
        if plan.get('deployment') == 'docker':
            return _sync_docker_ca(plan, payload)
        import pwd
        try:
            owner = pwd.getpwnam(plan.get('app_user') or '')
        except KeyError as error:
            raise SafetyError('安装记录缺少有效的非 Root 应用用户') from error
        if owner.pw_uid == 0:
            raise SafetyError('Never execute application PHP as Root')
        app = Path(plan['app']).resolve(strict=True)
        if not all((app / name).is_file() for name in ('artisan', 'bootstrap/app.php', 'vendor/autoload.php')):
            raise SafetyError('Installed Xboard application paths are incomplete')
        if not plan.get('php'):
            raise SafetyError('安装记录缺少 PHP CLI 路径，无法安全自动导入')
        php = manager.trusted_file(plan['php'], True)
        runuser = manager.trusted_file(shutil.which('runuser') or '/usr/sbin/runuser', True)
        helper = manager.trusted_file(Path(__file__).with_name('sync_panel_ca.php'))
        code = helper.read_text(encoding='utf-8')
        if not code.startswith('<?php\n'):
            raise SafetyError('Invalid local CA helper')
        environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                       'HOME': owner.pw_dir, 'USER': owner.pw_name, 'LOGNAME': owner.pw_name}
        try:
            result = subprocess.run([str(runuser), '-u', owner.pw_name, '--', str(php),
                                     '-d', 'display_errors=0', '-d', 'log_errors=0',
                                     '-r', code[len('<?php\n'):], '--', str(app)],
                                    input=json.dumps(payload).encode(), cwd=app, env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SafetyError('本机 CA 导入进程未完成；独立入口和证书未重新生成') from error
        try:
            response = json.loads(result.stdout)
            if not isinstance(response, dict):
                raise ValueError('Unexpected response')
        except (ValueError, UnicodeError) as error:
            raise SafetyError('本机 CA 导入失败，应用未返回预期结果；未输出应用日志或凭据') from error
        if result.returncode:
            reasons = {'plugin_disabled': 'NodeEndpoint 插件尚未安装或启用',
                       'endpoint_mismatch': '插件页面保存的通讯地址/端口/HTTPS 与 Root 安装记录不一致',
                       'unsafe_storage': '插件 CA 存储路径属主或权限异常，拒绝覆盖',
                       'ca_invalid': '插件证书校验未通过',
                       'application_failed': '应用启动或 CA 导入未完成'}
            raise SafetyError(reasons.get(response.get('error'), '本机 CA 导入未完成'))
        if (response.get('status') not in ('imported', 'unchanged')
                or response.get('fingerprints') != payload['fingerprints']
                or response.get('certificates') != len(payload['fingerprints'])
                or response.get('uid') not in (None, owner.pw_uid)):
            raise SafetyError('本机 CA 导入结果与 Root 可信证书不一致')
        return {key: response[key] for key in ('status', 'fingerprints', 'certificates')}
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='/var/lib/node-endpoint')
    args = parser.parse_args()
    try:
        print(json.dumps(sync_panel_ca(args.state), ensure_ascii=False))
    except (SafetyError, OSError, ValueError, KeyError) as error:
        print('停止：' + str(error))
        raise SystemExit(1)
