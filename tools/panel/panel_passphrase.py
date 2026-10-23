"""Root metadata and private pipes for panel-encrypted CA passphrases.

No passphrase is persisted here. The native application stores the random
secret encrypted by APP_KEY; Root keeps only context and opaque identifiers.
Callers hold manager.lock and include this sidecar in their PKI transaction.
"""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess

import manager
from pki import SafetyError, read_json, validate_host, write_json


FILENAME = 'managed-passphrases.json'
ID = re.compile(r'[a-f0-9]{32}\Z')
CA_ID = re.compile(r'[a-f0-9]{16}\Z')
PIN = re.compile(r'[a-f0-9]{64}\Z')
SECRET = re.compile(r'[A-Za-z0-9_-]{64}\Z')
FAILURE = '面板 CA 凭据不可用；未显示口令或应用日志，请检查插件、APP_KEY 和本机权限'


def _require(condition, message='Root CA 凭据元数据无效，拒绝继续'):
    if not condition:
        raise SafetyError(message)


def _context(plan):
    if isinstance(plan, dict) and plan.get('deployment') == 'docker' and plan.get('docker_context'):
        from docker_context import validate_context
        context = plan['docker_context']
        validate_context(context)
        return {'docker_context': context}
    _require(isinstance(plan, dict) and plan.get('deployment') in ('fpm', 'octane')
             and all(isinstance(plan.get(key), str) and plan[key] for key in ('app', 'app_user', 'php')),
             '自动保存 CA 口令需要原生 FPM/Octane 的 app-user 和 PHP；旧版插件或 Docker 请显式使用 --manual-ca-passphrase')
    _require(Path(plan['app']).is_absolute() and Path(plan['php']).is_absolute())
    return {'app': str(Path(plan['app']).resolve()), 'app_user': plan['app_user'], 'php': plan['php']}


class PanelBridge:
    def __init__(self, context):
        self.context = context

    def call(self, action, credential_id, host=None, fingerprint=None):
        """Never forward PHP stdout, stderr, or exception messages to the CLI."""
        try:
            manager.linux_root()
            import pwd
            owner = pwd.getpwnam(self.context['app_user'])
            _require(owner.pw_uid != 0 and owner.pw_gid != 0)
            app = Path(self.context['app']).resolve(strict=True)
            _require(all((app / name).is_file() for name in ('artisan', 'bootstrap/app.php', 'vendor/autoload.php')))
            php = manager.trusted_file(self.context['php'], True)
            runuser = manager.trusted_file(shutil.which('runuser') or '/usr/sbin/runuser', True)
            helper = manager.trusted_file(Path(__file__).with_name('panel_passphrase.php'))
            _require(helper.is_file() and helper.stat().st_nlink == 1 and helper.stat().st_size <= 65536)
            code = helper.read_text(encoding='utf-8')
            _require(code.startswith('<?php\n') and ID.fullmatch(credential_id))
            payload = {'action': action, 'credential_id': credential_id}
            if host is not None:
                payload['host'] = host
            if fingerprint is not None:
                payload['fingerprint'] = fingerprint
            environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                           'HOME': owner.pw_dir, 'USER': owner.pw_name, 'LOGNAME': owner.pw_name}
            process = subprocess.run([str(runuser), '-u', owner.pw_name, '--', str(php),
                                      '-d', 'display_errors=0', '-d', 'log_errors=0',
                                      '-r', code[6:], '--', str(app)], input=json.dumps(payload).encode(),
                                     cwd=app, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=90, shell=False)
            _require(process.returncode == 0 and len(process.stdout) <= 4096)
            response = json.loads(process.stdout)
            _require(isinstance(response, dict))
            if action in ('prepare', 'get'):
                _require(set(response) == {'credential_id', 'passphrase'}
                         and response['credential_id'] == credential_id
                         and isinstance(response['passphrase'], str) and SECRET.fullmatch(response['passphrase']))
            else:
                _require(action == 'bind' and response == {'status': 'stored', 'credential_id': credential_id,
                                                           'fingerprint': fingerprint})
            return response
        except Exception:
            raise SafetyError(FAILURE) from None


class DockerPanelBridge:
    def __init__(self, context):
        self.context = context

    def call(self, action, credential_id, host=None, fingerprint=None):
        try:
            from docker_context import run_helper
            _require(ID.fullmatch(credential_id))
            payload = {'action': action, 'credential_id': credential_id}
            if host is not None:
                payload['host'] = host
            if fingerprint is not None:
                payload['fingerprint'] = fingerprint
            response = run_helper(self.context, 'panel_passphrase', payload)
            if action in ('prepare', 'get'):
                _require(set(response) == {'credential_id', 'passphrase'} and response['credential_id'] == credential_id
                         and isinstance(response['passphrase'], str) and SECRET.fullmatch(response['passphrase']))
            else:
                _require(action == 'bind' and response == {'status': 'stored', 'credential_id': credential_id,
                                                           'fingerprint': fingerprint})
            return response
        except Exception:
            raise SafetyError(FAILURE) from None


class ManagedPassphrases:
    def __init__(self, state, plan=None, bridge=None):
        self.path = Path(state) / 'pki' / FILENAME
        self.data = self._read()
        if plan is not None:
            context = _context(plan)
            if self.data:
                _require({key: self.data.get(key) for key in context} == context,
                         'CA 凭据属于其他应用或运行身份，拒绝更换绑定')
        else:
            _require(self.data is not None, '此 CA 没有自动保存的口令记录')
            context = ({'docker_context': self.data['docker_context']} if self.data['schema_version'] == 2
                       else {key: self.data[key] for key in ('app', 'app_user', 'php')})
        if self.data is None:
            self.data = {'schema_version': 2 if 'docker_context' in context else 1, **context, 'pending': None, 'cas': {}}
        self.bridge = bridge or (DockerPanelBridge(context['docker_context']) if 'docker_context' in context else PanelBridge(context))

    def _private_parent(self):
        parent = manager.trusted_file(self.path.parent)
        _require(parent.is_dir())
        if os.name == 'posix':
            _require(parent.stat().st_uid == 0 and stat.S_IMODE(parent.stat().st_mode) == 0o700,
                     'CA 凭据目录必须为 Root 0700')

    def _read(self):
        self._private_parent()
        if self.path.is_symlink():
            raise SafetyError('拒绝 CA 凭据元数据符号链接')
        if not self.path.exists():
            return None
        path = manager.trusted_file(self.path)
        _require(path.is_file() and path.stat().st_nlink == 1 and path.stat().st_size <= 1048576)
        if os.name == 'posix':
            _require(stat.S_IMODE(path.stat().st_mode) == 0o600, 'CA 凭据元数据必须为 Root 0600')
        try:
            data = read_json(path)
            self._validate(data)
            return data
        except Exception:
            raise SafetyError('Root CA 凭据元数据无效，拒绝继续') from None

    def _validate(self, data):
        _require(isinstance(data, dict))
        if data.get('schema_version') == 2:
            _require(set(data) == {'schema_version', 'docker_context', 'pending', 'cas'})
            _context({'deployment': 'docker', 'docker_context': data['docker_context']})
        else:
            _require(set(data) == {'schema_version', 'app', 'app_user', 'php', 'pending', 'cas'}
                     and data['schema_version'] == 1)
            _context({'deployment': 'fpm', **{key: data[key] for key in ('app', 'app_user', 'php')}})
        _require(isinstance(data['cas'], dict))
        for ca_id, record in data['cas'].items():
            _require(isinstance(ca_id, str) and CA_ID.fullmatch(ca_id)
                     and isinstance(record, dict) and set(record) == {'credential_id', 'host', 'fingerprint'}
                     and ID.fullmatch(record['credential_id']) and PIN.fullmatch(record['fingerprint'])
                     and validate_host(record['host'])[0] == record['host'])
        pending = data['pending']
        if pending is not None:
            _require(isinstance(pending, dict) and set(pending) == {'kind', 'credential_id', 'host', 'before_ca'}
                     and pending['kind'] in ('create', 'rotation') and ID.fullmatch(pending['credential_id'])
                     and validate_host(pending['host'])[0] == pending['host'])
            _require((pending['kind'] == 'create' and pending['before_ca'] is None)
                     or (pending['kind'] == 'rotation' and isinstance(pending['before_ca'], str)
                         and CA_ID.fullmatch(pending['before_ca'])))

    def _save(self):
        self._validate(self.data)
        self._private_parent()
        if self.path.exists():
            manager.trusted_file(self.path)
            _require(not self.path.is_symlink() and self.path.is_file() and self.path.stat().st_nlink == 1)
        write_json(self.path, self.data, 0o600)

    def _candidate(self, pki):
        pending, meta = self.data['pending'], pki.meta
        if not pending or not meta:
            return None
        if pending['kind'] == 'rotation':
            candidate = (meta.get('rotation') or {}).get('new_ca') or meta.get('active_ca')
            return candidate if candidate != pending['before_ca'] else None
        return meta.get('active_ca')

    def _bind(self, pki, ca_id, password, ca_key=None):
        pending = self.data['pending']
        _require(pending and isinstance(ca_id, str) and CA_ID.fullmatch(ca_id))
        folder = pki.ca_dir(ca_id)
        pki.openssl.key_matches(Path(ca_key) if ca_key else folder / 'ca.key.pem', folder / 'ca.pem', password)
        fingerprint = pki.openssl.info(folder / 'ca.pem')['sha256']
        _require(PIN.fullmatch(fingerprint))
        self.bridge.call('bind', pending['credential_id'], pending['host'], fingerprint)
        self.data['cas'][ca_id] = {'credential_id': pending['credential_id'], 'host': pending['host'],
                                   'fingerprint': fingerprint}
        self.data['pending'] = None
        self._save()

    def recover_pending(self, pki, ca_key=None):
        candidate = self._candidate(pki)
        if not candidate:
            return False
        try:
            pending = self.data['pending']
            password = self.bridge.call('get', pending['credential_id'], pending['host'])['passphrase']
            self._bind(pki, candidate, password, ca_key)
            return True
        except Exception:
            raise SafetyError(FAILURE) from None

    def _prepare(self, pki, host, kind):
        host = validate_host(host)[0]
        before_ca = pki.meta.get('active_ca') if pki.meta else None
        expected = {'kind': kind, 'host': host, 'before_ca': before_ca}
        pending = self.data['pending']
        if pending:
            _require(all(pending[key] == value for key, value in expected.items()),
                     '先前 CA 凭据准备尚未恢复，拒绝创建不同的 CA')
        else:
            pending = {**expected, 'credential_id': secrets.token_hex(16)}
            self.data['pending'] = pending
            self._save()  # Opaque recovery ID precedes PHP preparation and PKI.
        return self.bridge.call('prepare', pending['credential_id'], host)['passphrase']

    def create(self, pki, host):
        try:
            self.recover_pending(pki)
            _require(not pki.meta, '已有 CA，禁止重新生成口令或证书')
            password = self._prepare(pki, host, 'create')
            output = pki.create(host, password)
            self._bind(pki, pki.meta['active_ca'], password)
            return output
        except Exception:
            raise SafetyError(FAILURE) from None

    def prepare_rotation(self, pki):
        try:
            self.recover_pending(pki)
            if pki.meta.get('rotation'):
                _require(pki.meta['rotation']['new_ca'] in self.data['cas'])
                return pki.status()  # A retried prepared CA keeps its bytes/ID.
            password = self._prepare(pki, pki.meta['host'], 'rotation')
            output = pki.prepare_rotation(password)
            self._bind(pki, pki.meta['rotation']['new_ca'], password)
            return output
        except Exception:
            raise SafetyError(FAILURE) from None

    def password(self, pki, ca_id=None, ca_key=None):
        ca_id = ca_id or pki.meta.get('active_ca')
        if self._candidate(pki) == ca_id:
            self.recover_pending(pki, ca_key)
        record = self.data['cas'].get(ca_id)
        if record is None:
            return None  # Legacy CA: keep the previous hidden-prompt workflow.
        try:
            _require(pki.openssl.info(pki.ca_dir(ca_id) / 'ca.pem')['sha256'] == record['fingerprint'])
            return self.bridge.call('get', record['credential_id'], record['host'])['passphrase']
        except Exception:
            raise SafetyError(FAILURE) from None


def existing_password(pki, plan=None, ca_id=None, ca_key=None):
    if not (pki.root / FILENAME).exists() and not (pki.root / FILENAME).is_symlink():
        return None
    return ManagedPassphrases(pki.state, plan).password(pki, ca_id, ca_key)


def rebind_docker_context(pki, plan, ca_key=None):
    """Called only inside a reviewed Root transaction; no new CA or password."""
    target = pki.root / FILENAME
    if plan.get('deployment') != 'docker' or not plan.get('docker_context') or not target.exists():
        return False
    vault = ManagedPassphrases(pki.state)
    _require(vault.data['schema_version'] == 2, '已有 CA 属于原生应用；Docker 迁移需要单独审阅，未更换绑定')
    old, candidate = vault.data['docker_context'], _context(plan)['docker_context']
    if old == candidate:
        return False
    stable = ('daemon_id', 'project', 'service', 'working_dir', 'app', 'uid', 'gid', 'php', 'bind_sources', 'volume_sources')
    _require(all(old.get(key) == candidate.get(key) for key in stable),
             'Docker 应用或持久化来源不同，拒绝换绑 CA')
    _require(vault.data['pending'] is None and pki.meta and pki.meta.get('active_ca') in vault.data['cas'],
             '先恢复待完成的 CA 凭据操作，未自动换绑容器')
    trusted = pki.meta.get('trusted_ca')
    _require(isinstance(trusted, list) and 1 <= len(trusted) <= 2
             and all(isinstance(identity, str) and CA_ID.fullmatch(identity) for identity in trusted)
             and len(set(trusted)) == len(trusted) and pki.meta['active_ca'] in trusted,
             '原 CA 信任记录不完整，拒绝跳过私钥证明换绑容器')
    bridge = DockerPanelBridge(candidate)
    for identity in trusted:
        record = vault.data['cas'].get(identity)
        _require(record is not None, '缺少现有 CA 绑定，未自动认领新容器')
        folder = pki.ca_dir(identity)
        _require(pki.openssl.info(folder / 'ca.pem')['sha256'] == record['fingerprint'])
        password = bridge.call('get', record['credential_id'], record['host'])['passphrase']
        key = Path(ca_key) if ca_key and identity == pki.meta['active_ca'] else folder / 'ca.key.pem'
        _require(key.is_file(), '容器重建换绑需要原 CA 私钥作本机匹配验证；请提供 --ca-key，未重建 CA')
        pki.openssl.key_matches(key, folder / 'ca.pem', password)
    vault.data['docker_context'] = candidate
    vault._save()
    return True


def with_password(pki, callback, manual_prompt, plan=None, ca_id=None, ca_key=None, manual=False):
    password = None if manual else existing_password(pki, plan, ca_id, ca_key)
    if password is None:
        return callback(manual_prompt())
    try:
        return callback(password)
    except Exception:
        raise SafetyError(FAILURE) from None
