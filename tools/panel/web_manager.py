#!/usr/bin/env python3
"""Local, fixed-policy NodeEndpoint web bridge. No network listener or shell RPC.

This asset is released separately from the immutable tools 1.0.6 archive. Install
once as Root after the SSH wizard has created a native FPM/Octane HTTPS entry.
Application code is always executed by its existing non-Root account. The RPC
grants that account only the operations described by the Root-installed policy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import copy
import datetime as dt
import hashlib
import importlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.dont_write_bytecode = True
VERSION = '1.0.0'
TOOLS_SHA256 = 'd1dd1258b68e34e7feca111a85c97bd0d435516093b49840633166fe066e566e'
TOOLS_ROOT = Path('/opt/nodeendpoint-quick') / TOOLS_SHA256
CONFIG = Path('/etc/nodeendpoint-web-manager.json')
CODE = Path('/opt/nodeendpoint-web-manager/1.0.0/web_manager.py')
STATE = Path('/var/lib/node-endpoint')
PRIVATE = Path('/var/lib/nodeendpoint-web-manager')
RUNTIME = Path('/run/nodeendpoint-web-manager')
SOCKET = RUNTIME / 'control.sock'
SERVICE = Path('/etc/systemd/system/nodeendpoint-web-manager.service')
MAX_REQUEST = 32768
MAX_RESPONSE = 131072
MAX_PLANS = 128
MAX_JOBS = 128
MAX_PENDING = 4
PLAN_TTL = 300
ID = re.compile(r'[a-f0-9]{32}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
BACKUP = re.compile(r'[0-9]{8}T[0-9]{6}-[a-f0-9]{8}\Z')
TERMINAL = {'succeeded', 'partial', 'failed', 'interrupted'}
AUDIT_LOCK = threading.Lock()
PUBLIC_STATUS = {'schema_version', 'version', 'generated_at', 'installed', 'endpoint',
                 'ca', 'server', 'trust', 'rotation', 'checks', 'revocation_enforced', 'ca_key_online'}
BOUND_FIELDS = ('app', 'app_user', 'php', 'deployment', 'backend', 'source_site',
                'site_name', 'include_dir', 'config_path', 'main_config', 'ownership')
MESSAGES = {
    'invalid_request': '请求格式或参数不受支持。',
    'unauthorized': '此本机进程未获 Root 策略授权。',
    'unavailable': 'Root 助手不可用，请通过 SSH 检查安装。',
    'unsafe_path': 'Root 文件归属、权限或完整性检查失败。',
    'policy_changed': '已安装入口或应用身份不符合 Root 策略，请通过 SSH 重新审核。',
    'busy': '其他 Root 操作正在执行，请稍后重试。',
    'capacity': 'Root 助手任务数量已达上限，请等待现有任务完成。',
    'not_found': '计划或任务不存在。',
    'expired': '确认计划已过期，请重新预览。',
    'confirmation_mismatch': '确认内容或计划摘要不匹配。',
    'plan_changed': '入口、证书、路由或已保存设置已变化，请重新预览。',
    'port_not_allowed': '该端口未获 Root 策略授权。',
    'manual_required': '此操作需要 SSH 处理原有 CA、口令、轮换或站点绑定。',
    'settings_mismatch': '请求与面板已保存的 HTTPS 通讯设置不一致。',
    'entry_failed': '入口操作失败，请通过 SSH 查看 Root 备份与恢复证据。',
    'ca_sync_failed': '入口已保留，公共 CA 同步未完成，请核对设置后重试。',
    'firewall_failed': '本机端口规则未完全确认，可能已部分应用，请核对防火墙结果。',
    'interrupted': '助手曾重启，任务未自动重放；请核对当前状态后重新预览。',
    'operation_failed': 'Root 操作未完成，请核对当前状态或通过 SSH 检查。',
}


class RpcError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in MESSAGES else 'operation_failed'
        super().__init__(MESSAGES[self.code])


def require(condition, code='invalid_request'):
    if not condition:
        raise RpcError(code)


def root_only():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'unauthorized')


def utc(value=None):
    return dt.datetime.fromtimestamp(time.time() if value is None else value, dt.timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise RpcError('invalid_request')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise RpcError('invalid_request') from None


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value)
            and set(value) <= set(required) | set(optional))


def secure(path, mode=None, directory=False):
    """No symlinks, writable ancestors, special files or multiply linked files."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe_path')
        if item != path:
            require(stat.S_ISDIR(info.st_mode), 'unsafe_path')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe_path')
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode, 'unsafe_path')
    return path


def read_private(path, limit=1048576):
    path = secure(path, 0o600)
    require(path.stat().st_size <= limit, 'unsafe_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        raw = handle.read(limit + 1)
    require(len(raw) <= limit, 'unsafe_path')
    return strict_json(raw)


def atomic(path, data, mode=0o600):
    path = Path(path)
    secure(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        secure(path)
    fd, temporary = tempfile.mkstemp(prefix='.web-manager-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def audit(stage, error, identity=None):
    """Root-only diagnostic frames, without exception text, source or locals."""
    try:
        root_only()
        secure(PRIVATE, 0o700, directory=True)
        event = {'at': utc(), 'stage': stage, 'job': identity,
                 'error_type': type(error).__name__,
                 'frames': [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                            for frame in traceback.extract_tb(error.__traceback__)[-12:]]}
        with AUDIT_LOCK:
            path = PRIVATE / 'events.jsonl'
            if path.exists() or path.is_symlink():
                secure(path, 0o600)
                if path.stat().st_size >= 1048576:
                    previous = PRIVATE / 'events.previous.jsonl'
                    if previous.exists() or previous.is_symlink():
                        secure(previous, 0o600)
                    os.replace(path, previous)
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                info = os.fstat(handle.fileno())
                require(info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_path')
                handle.write(canonical(event) + b'\n')
                handle.flush()
                os.fsync(handle.fileno())
    except Exception:
        # Diagnostics must not change a completed operation or leak to its RPC.
        pass


class PinnedTools:
    REQUIRED = {'tools/panel/' + name for name in ('manager.py', 'pki.py', 'nginx.py',
        'panel_ca.py', 'sync_panel_ca.php', 'panel_passphrase.py', 'panel_passphrase.php',
        'firewall.py', 'endpoint_settings.php', 'docker_context.py')}

    def __init__(self, expected_manifest=None):
        self.root = TOOLS_ROOT
        self.manifest_sha256 = self.verify(expected_manifest)
        tool_dir = str(self.root / 'tools/panel')
        sys.path.insert(0, tool_dir)
        for name in ('pki', 'nginx', 'manager', 'panel_ca', 'panel_passphrase', 'firewall'):
            old = sys.modules.get(name)
            require(old is None or Path(old.__file__).resolve() == (Path(tool_dir) / (name + '.py')).resolve(), 'unsafe_path')
            setattr(self, name, importlib.import_module(name))

    def verify(self, expected_manifest=None):
        secure(self.root, directory=True)
        marker = read_private(self.root / '.nodeendpoint-verified-package.json')
        require(marker == {'sha256': TOOLS_SHA256}, 'unsafe_path')
        manifest = secure(self.root / 'MANIFEST.sha256')
        raw = manifest.read_bytes()
        require(len(raw) <= 262144, 'unsafe_path')
        manifest_sha = hashlib.sha256(raw).hexdigest()
        if expected_manifest is not None:
            require(manifest_sha == expected_manifest, 'unsafe_path')
        members = {}
        try:
            for line in raw.decode('ascii').splitlines():
                sha, name = line.split('  ', 1)
                path = PurePosixPath(name)
                require(SHA.fullmatch(sha) and name not in members and path.parts
                        and not path.is_absolute() and '..' not in path.parts
                        and name == path.as_posix() and '\\' not in name and ':' not in name, 'unsafe_path')
                members[name] = sha
        except (ValueError, UnicodeError):
            raise RpcError('unsafe_path') from None
        require(self.REQUIRED <= set(members) and len(members) < 2000, 'unsafe_path')
        total, observed = 0, set()
        for path in self.root.rglob('*'):
            require(not path.is_symlink(), 'unsafe_path')
            if path.is_dir():
                secure(path, directory=True)
                continue
            secure(path)
            name = path.relative_to(self.root).as_posix()
            if name in ('MANIFEST.sha256', '.nodeendpoint-verified-package.json'):
                continue
            require(name in members and path.stat().st_size <= 16 * 1024 * 1024, 'unsafe_path')
            total += path.stat().st_size
            require(total <= 64 * 1024 * 1024 and hashlib.sha256(path.read_bytes()).hexdigest() == members[name], 'unsafe_path')
            observed.add(name)
        require(observed == set(members), 'unsafe_path')
        return manifest_sha


def validate_policy(policy):
    fields(policy, ('schema', 'version', 'tools_sha256', 'manifest_sha256', 'app', 'app_user',
                    'app_uid', 'app_gid', 'php', 'nginx', 'state', 'binding', 'ws_backend', 'allowed_ports'))
    require(type(policy['schema']) is int and policy['schema'] == 1 and policy['version'] == VERSION
            and policy['tools_sha256'] == TOOLS_SHA256 and SHA.fullmatch(policy['manifest_sha256']), 'policy_changed')
    require(all(isinstance(policy[key], str) and PurePosixPath(policy[key]).is_absolute()
                and '\\' not in policy[key] and '\x00' not in policy[key] for key in ('app', 'php', 'nginx', 'state')), 'policy_changed')
    require(len(PurePosixPath(policy['state']).parts) >= 3 and '..' not in PurePosixPath(policy['state']).parts, 'policy_changed')
    require(isinstance(policy['app_user'], str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', policy['app_user'])
            and all(type(policy[key]) is int and policy[key] > 0 for key in ('app_uid', 'app_gid')), 'policy_changed')
    require(isinstance(policy['binding'], dict) and set(policy['binding']) == set(BOUND_FIELDS), 'policy_changed')
    require(policy['binding']['deployment'] in ('fpm', 'octane') and all(
        isinstance(policy['binding'][key], str) and policy['binding'][key] for key in BOUND_FIELDS), 'policy_changed')
    require(all(policy['binding'][key] == policy[key] for key in ('app', 'app_user', 'php')), 'policy_changed')
    require(policy['ws_backend'] is None or isinstance(policy['ws_backend'], str), 'policy_changed')
    ports = policy['allowed_ports']
    require(isinstance(ports, list) and 1 <= len(ports) <= 32 and len(ports) == len(set(ports))
            and all(type(port) is int and 1 <= port <= 65535 and port not in (80, 443) for port in ports), 'policy_changed')
    return policy


def safe_host(value, pki):
    require(isinstance(value, str) and len(value) <= 253)
    try:
        host, kind = pki.validate_host(value)
        if kind == 'IP':
            address = ipaddress.ip_address(host)
            examples = ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')
            require(not any(address in ipaddress.ip_network(net) for net in examples))
        return host
    except (ValueError, RuntimeError):
        raise RpcError('invalid_request') from None


def bounded_capture(argv, input_bytes, environment, limit=65536, error_limit=8192, timeout=90):
    """Drain both pipes with hard byte limits and kill the whole process group."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd='/', env=environment, shell=False, start_new_session=True)
    failed, blocks, errors, threads = threading.Event(), [], [], []
    def stop():
        failed.set()
        try:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    def reader(pipe, target, maximum):
        size = 0
        try:
            while True:
                data = pipe.read1(4096)
                if not data:
                    return
                size += len(data)
                if size > maximum:
                    stop()
                    return
                if target is not None:
                    target.append(data)
        except (OSError, ValueError):
            stop()
    def writer():
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except (OSError, ValueError):
            stop()
    try:
        tasks = [(reader, (process.stdout, blocks, limit)), (reader, (process.stderr, errors, error_limit))]
        if input_bytes is not None:
            tasks.append((writer, ()))
        for target, args in tasks:
            thread = threading.Thread(target=target, args=args, daemon=True)
            thread.start()
            threads.append(thread)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop()
            process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=2)
        require(not failed.is_set() and not any(t.is_alive() for t in threads), 'operation_failed')
        return b''.join(blocks), b''.join(errors), process.returncode
    finally:
        if process.poll() is None or any(t.is_alive() for t in threads):
            stop()
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError):
                pass


def bounded_process(argv, payload, environment, limit=65536):
    """JSON application bridge; no raw bootstrap diagnostics cross its boundary."""
    output, _, code = bounded_capture(argv, canonical(payload), environment, limit=limit)
    require(code == 0, 'operation_failed')
    return strict_json(output)


class BoundedPanelBridge:
    """Runtime replacement for tools 1.0.6's otherwise unbounded PHP pipe."""
    def __init__(self, adapter, context):
        self.adapter = adapter
        require(context == {key: adapter.policy[key] for key in ('app', 'app_user', 'php')}, 'policy_changed')

    def call(self, action, credential_id, host=None, fingerprint=None):
        require(action in ('get', 'bind') and isinstance(credential_id, str) and ID.fullmatch(credential_id), 'manual_required')
        payload = {'action': action, 'credential_id': credential_id}
        if host is not None:
            require(isinstance(host, str) and len(host) <= 253, 'manual_required')
            payload['host'] = host
        if fingerprint is not None:
            require(isinstance(fingerprint, str) and SHA.fullmatch(fingerprint), 'manual_required')
            payload['fingerprint'] = fingerprint
        response = self.adapter.app_call('panel_passphrase.php', payload)
        if action == 'get':
            require(isinstance(response, dict) and set(response) == {'credential_id', 'passphrase'}
                    and response['credential_id'] == credential_id and isinstance(response['passphrase'], str)
                    and re.fullmatch(r'[A-Za-z0-9_-]{64}', response['passphrase']), 'manual_required')
        else:
            require(response == {'status': 'stored', 'credential_id': credential_id, 'fingerprint': fingerprint}, 'manual_required')
        return response


class RootAdapter:
    def __init__(self, policy, tools):
        self.policy, self.tools = validate_policy(policy), tools
        self.manager, self.pki = tools.manager, tools.pki
        self.state = Path(policy['state'])
        self.cached_status = None
        # Patch only the two pinned call sites, never the shared subprocess
        # module or immutable release files. No application-controlled output
        # reaches subprocess.run's unbounded PIPE buffering in this service.
        self.manager.command = self.bounded_command
        tools.panel_passphrase.PanelBridge = lambda context: BoundedPanelBridge(self, context)

    def bounded_command(self, arguments, timeout=45, allow_failure=False):
        require(isinstance(arguments, (list, tuple)) and arguments, 'operation_failed')
        binary = self.manager.trusted_file(shutil.which(str(arguments[0])) or str(arguments[0]), True)
        output, errors, code = bounded_capture([str(binary), *map(str, arguments[1:])], None,
            {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin',
             'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
            limit=4 * 1024 * 1024, error_limit=65536, timeout=timeout)
        require(code == 0 or allow_failure, 'operation_failed')
        return output.decode(errors='replace'), errors.decode(errors='replace'), code

    @contextmanager
    def locked(self):
        import fcntl
        self.tools.verify(self.policy['manifest_sha256'])
        self.manager.safe_state(self.state)
        marker = read_private(self.state / self.manager.STATE_MARKER)
        require(marker.get('tool') == 'NodeEndpoint' and marker.get('schema_version') == 1
                and marker.get('path') == str(self.state), 'policy_changed')
        fd = os.open(secure(self.state / 'manager.lock', 0o600), os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RpcError('busy') from None
            yield
        finally:
            os.close(fd)

    def current(self):
        plan, payload = self.tools.panel_ca.payload_from_state(self.state)
        require(all(plan.get(key) == value for key, value in self.policy['binding'].items()), 'policy_changed')
        require(plan.get('https') is True and plan.get('ws_backend') in (None, self.policy['ws_backend'])
                and plan.get('port') in self.policy['allowed_ports'], 'policy_changed')
        import pwd
        owner = pwd.getpwnam(self.policy['app_user'])
        require(owner.pw_uid == self.policy['app_uid'] and owner.pw_gid == self.policy['app_gid'], 'policy_changed')
        self.manager.trusted_file(self.policy['php'], True)
        self.manager.trusted_file(self.policy['nginx'], True)
        return plan, payload, self.pki.PKI(self.state)

    def app_call(self, helper, payload):
        require(helper in ('endpoint_settings.php', 'sync_panel_ca.php', 'panel_passphrase.php'))
        import pwd
        owner = pwd.getpwnam(self.policy['app_user'])
        require(owner.pw_uid == self.policy['app_uid'] and owner.pw_gid == self.policy['app_gid'] and owner.pw_uid > 0, 'policy_changed')
        code = secure(self.tools.root / 'tools/panel' / helper).read_text(encoding='utf-8')
        require(code.startswith('<?php\n') and len(code) <= 65536, 'unsafe_path')
        binary = self.manager.trusted_file(self.policy['php'], True)
        runuser = self.manager.trusted_file(shutil.which('runuser', path='/usr/sbin:/usr/bin:/sbin:/bin') or '/usr/sbin/runuser', True)
        env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': owner.pw_dir, 'USER': owner.pw_name,
               'LOGNAME': owner.pw_name, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
        return bounded_process([str(runuser), '-u', owner.pw_name, '--', str(binary), '-d', 'display_errors=0',
            '-d', 'log_errors=0', '-d', 'auto_prepend_file=', '-d', 'auto_append_file=',
            '-r', code[6:], '--', self.policy['app']], payload, env,
            limit=4096 if helper == 'panel_passphrase.php' else 65536)

    @staticmethod
    def target(plan):
        return {'host': plan['host'], 'port': plan['port'], 'https': True,
                'ws_mode': 'rewrite' if plan.get('ws_backend') else 'rest'}

    def public(self, plan, pki, check=True):
        checks = {}
        if check:
            try:
                env = self.manager.Environment(self.policy['nginx'])
                require(env.main == self.policy['binding']['main_config'], 'policy_changed')
                require(plan['config_path'] in env.files, 'policy_changed')
                checks = self.manager.local_probe(plan, pki)
                checks['nginx'] = 'loaded'
            except Exception:
                checks = {'nginx': 'unverified_or_unavailable', 'service': 'failed'}
        value = self.manager.Manager(self.state, None).public_status(checks)
        value = {key: value[key] for key in PUBLIC_STATUS if key in value}
        value['endpoint']['ws_mode'] = self.target(plan)['ws_mode']
        return value

    def status(self):
        try:
            with self.locked():
                plan, payload, pki = self.current()
                result = {'root_status': self.public(plan, pki),
                          'ca': {key: payload[key] for key in ('pem', 'fingerprints')}}
                self.cached_status = copy.deepcopy(result)
                return result
        except RpcError as error:
            if error.code == 'busy' and self.cached_status:
                return copy.deepcopy(self.cached_status)
            raise

    def arguments(self, target):
        b = self.policy['binding']
        argv = ['--nginx', self.policy['nginx'], 'modify', '--host', target['host'], '--port', str(target['port']),
                '--site-config', b['source_site'], '--site-name', b['site_name'], '--include-dir', b['include_dir'],
                '--app', b['app'], '--app-user', b['app_user'], '--php', b['php'],
                '--deployment', b['deployment'], '--backend', b['backend']]
        if target['ws_mode'] == 'rewrite':
            require(self.policy['ws_backend'] is not None, 'manual_required')
            argv += ['--enable-ws', '--ws-backend', self.policy['ws_backend']]
        args = self.manager.parser().parse_args(argv)
        args.confirm = 'INSTALL ' + target['host'] + ':' + str(target['port'])
        return args

    def prepare(self, action, parameters, _locked=False):
        with (nullcontext() if _locked else self.locked()):
            plan, payload, pki = self.current()
            meta = pki.meta
            require(meta and meta.get('active_ca') and not meta.get('rotation') and meta.get('host') == plan['host'], 'manual_required')
            desired = None
            target = self.target(plan)
            if action == 'configure':
                response = self.app_call('endpoint_settings.php', {'action': 'read'})
                require(response.get('status') == 'read' and response.get('uid') == self.policy['app_uid']
                        and isinstance(response.get('settings'), dict), 'settings_mismatch')
                desired = response['settings']
                require(desired.get('enabled') is True and desired.get('https') is True, 'settings_mismatch')
                target = {'host': safe_host(desired.get('host'), self.pki), 'port': desired.get('port'),
                          'https': True, 'ws_mode': desired.get('ws_mode')}
                require(type(target['port']) is int and target['port'] in self.policy['allowed_ports'], 'port_not_allowed')
                require(target['ws_mode'] in ('rest', 'rewrite'), 'settings_mismatch')
                require(all(parameters[key] == target[key] for key in parameters if key != 'action'), 'settings_mismatch')
            if action == 'renew' or target['host'] != plan['host']:
                require(pki.status().get('ca_key_online') is True and (pki.root / 'managed-passphrases.json').is_file(), 'manual_required')
            args, candidate, env_files = None, None, {}
            if action in ('configure', 'refresh', 'renew'):
                args = self.arguments(target)
                env = self.manager.Environment(self.policy['nginx'])
                require(env.main == self.policy['binding']['main_config'], 'policy_changed')
                candidate = env.plan(args, self.state, self.manager.Manager(self.state, env).installed)
                require(all(candidate.get(key) == value for key, value in self.policy['binding'].items()), 'policy_changed')
                env_files = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in env.files.items()}
            files = {}
            for name in ('installation.json', 'pki/state.json', 'pki/managed-passphrases.json', 'pki/trust-bundle.pem'):
                path = self.state / name
                files[name] = hashlib.sha256(secure(path).read_bytes()).hexdigest() if path.exists() else None
            review = {'action': action, 'target': target, 'candidate': candidate, 'desired': desired,
                      'root_digest': digest({'files': files, 'nginx': env_files, 'policy': self.policy,
                                             'ca': payload['fingerprints']})}
            current = self.public(plan, pki, check=False)
            return {'review': review, 'current': current, 'fingerprints': payload['fingerprints']}

    def assert_expected(self, target, pins):
        plan, payload, pki = self.current()
        require(self.target(plan) == target and payload['fingerprints'] == pins, 'plan_changed')
        return plan, payload, pki

    def apply_entry(self, saved):
        review = saved['review']
        # Keep the CLI lock continuously from the final plan comparison through
        # Manager.transaction; a separate manager.execute would reacquire it.
        with self.locked():
            fresh = self.prepare(review['action'], {'action': review['action'], **(
                {key: review['target'][key] for key in ('host', 'port', 'ws_mode')}
                if review['action'] == 'configure' else {})}, _locked=True)
            require(fresh['review'] == review and fresh['fingerprints'] == saved['fingerprints'], 'plan_changed')
            plan, before, pki = self.current()
            before_ca = pki.meta['active_ca']
            target, action = review['target'], review['action']
            args = self.arguments(target)
            args._approved_plan = review['candidate']
            env = self.manager.Environment(self.policy['nginx'])
            candidate = env.plan(args, self.state, self.manager.Manager(self.state, env).installed)
            require(candidate == args._approved_plan, 'plan_changed')
            self.manager.confirmation(args.confirm, args.confirm)
            def apply():
                if action == 'renew' or pki.meta['host'] != target['host']:
                    def no_manual_prompt(*_):
                        raise RpcError('manual_required')
                    self.tools.panel_passphrase.with_password(pki, lambda password: pki.renew(target['host'], password),
                        no_manual_prompt, plan=candidate)
            result = self.manager.Manager(self.state, env).transaction(apply, candidate)
            committed, after, current_pki = self.assert_expected(target, saved['fingerprints'])
            require(before['pem'] == after['pem'] and before_ca == current_pki.meta['active_ca'], 'plan_changed')
            backup = Path(result['backup']).name
            require(BACKUP.fullmatch(backup), 'operation_failed')
            return {'backup_id': backup, 'ca_preserved': True,
                    'endpoint': target, 'root_status': self.public(committed, current_pki)}

    def sync_ca(self, saved):
        with self.locked():
            _, payload, _ = self.assert_expected(saved['review']['target'], saved['fingerprints'])
            result = self.app_call('sync_panel_ca.php', payload)
            require(result.get('status') in ('imported', 'unchanged')
                    and result.get('uid') == self.policy['app_uid']
                    and result.get('fingerprints') == saved['fingerprints']
                    and type(result.get('certificates')) is int
                    and result['certificates'] == len(saved['fingerprints']), 'ca_sync_failed')
            return {key: result[key] for key in ('status', 'certificates', 'fingerprints')}

    def firewall(self, saved):
        with self.locked():
            plan, _, _ = self.assert_expected(saved['review']['target'], saved['fingerprints'])
            config = self.manager.parse(Path(plan['config_path']).read_text(), plan['config_path'])
            listeners = [node.words for node in self.manager.walk(config) if node.words[0] == 'listen']
            require(listeners and all(len(words) >= 2 and words[1] in
                ('0.0.0.0:' + str(plan['port']), '[::]:' + str(plan['port'])) for words in listeners), 'policy_changed')
            return self.tools.firewall.ensure_port(plan['port'])


class Store:
    def __init__(self, directory=PRIVATE):
        self.directory = Path(directory)
        secure(self.directory, 0o700, directory=True)

    def path(self, kind, identity):
        require(kind in ('plan', 'job') and isinstance(identity, str) and ID.fullmatch(identity))
        return self.directory / (kind + '-' + identity + '.json')

    def save(self, kind, value):
        atomic(self.path(kind, value['id']), canonical(value))

    def load_all(self, kind):
        result = {}
        for path in self.directory.glob(kind + '-*.json'):
            value = read_private(path)
            require(isinstance(value, dict) and path == self.path(kind, value.get('id')), 'unsafe_path')
            result[value['id']] = value
        require(len(result) <= (MAX_PLANS if kind == 'plan' else MAX_JOBS), 'capacity')
        return result

    def remove(self, kind, identity):
        path = self.path(kind, identity)
        secure(path, 0o600).unlink()
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Engine:
    def __init__(self, policy, adapter, store, start_worker=True, now=time.time):
        self.policy, self.adapter, self.store, self.now = policy, adapter, store, now
        self.gate = threading.RLock()
        self.operations = threading.Lock()
        self.queue = queue.Queue(MAX_PENDING)
        self.stopping = threading.Event()
        self.worker_thread = None
        self.plans, self.jobs = store.load_all('plan'), store.load_all('job')
        self.active_job = None
        for job in self.jobs.values():
            if job['state'] not in TERMINAL:
                job['state'] = 'interrupted'
                job['error'] = {'code': 'interrupted', 'message': MESSAGES['interrupted']}
                for stage in job['stages']:
                    if stage['state'] == 'running':
                        stage.update(state='interrupted', code='interrupted', message=MESSAGES['interrupted'])
                    elif stage['state'] == 'pending':
                        stage['state'] = 'skipped'
                store.save('job', job)
        if start_worker:
            self.worker_thread = threading.Thread(target=self.worker, daemon=True, name='nodeendpoint-worker')
            self.worker_thread.start()

    def prune(self):
        for identity, plan in list(self.plans.items()):
            if plan['expires_epoch'] <= self.now() and identity not in self.jobs:
                self.store.remove('plan', identity)
                del self.plans[identity]
        if len(self.jobs) >= MAX_JOBS:
            for job in sorted(self.jobs.values(), key=lambda value: value['created_at']):
                if job['state'] in TERMINAL and len(self.jobs) >= MAX_JOBS:
                    identity = job['id']
                    # A crash must never retain a still-valid plan after its
                    # consumed-job evidence disappears. Each removal fsyncs
                    # the directory before the next durable deletion.
                    if identity in self.plans:
                        self.store.remove('plan', identity)
                        del self.plans[identity]
                    self.store.remove('job', identity)
                    del self.jobs[identity]

    def status(self):
        result = self.adapter.status()
        with self.gate:
            return {'available': True, 'version': VERSION,
                    'policy': {key: self.policy[key] for key in ('app', 'app_user', 'allowed_ports')},
                    **result, 'active_job': self.active_job}

    def plan(self, parameters):
        require(not self.stopping.is_set(), 'unavailable')
        fields(parameters, ('action',), ('host', 'port', 'ws_mode'))
        action = parameters['action']
        require(action in ('configure', 'refresh', 'renew', 'firewall'))
        require(action == 'configure' or set(parameters) == {'action'})
        if 'host' in parameters:
            require(isinstance(parameters['host'], str) and len(parameters['host']) <= 253)
        if 'port' in parameters:
            require(type(parameters['port']) is int and 1 <= parameters['port'] <= 65535)
        if 'ws_mode' in parameters:
            require(parameters['ws_mode'] in ('rest', 'rewrite'))
        require(self.operations.acquire(blocking=False), 'busy')
        try:
            prepared = self.adapter.prepare(action, parameters)
        finally:
            self.operations.release()
        target = prepared['review']['target']
        confirmation = {'configure': 'INSTALL ', 'refresh': 'REFRESH ', 'renew': 'REPLACE SERVER KEY ',
                        'firewall': 'ALLOW TCP '}[action] + (str(target['port']) if action == 'firewall'
                                                          else target['host'] + ':' + str(target['port']))
        plan = {**prepared, 'id': secrets.token_hex(16), 'action': action, 'confirmation': confirmation,
                'target': target, 'stages': ['firewall'] if action == 'firewall' else ['entry', 'ca_sync', 'firewall'],
                'expires_epoch': self.now() + PLAN_TTL, 'expires_at': utc(self.now() + PLAN_TTL)}
        plan['digest'] = digest(plan)
        with self.gate:
            require(not self.stopping.is_set(), 'unavailable')
            self.prune()
            require(len(self.plans) < MAX_PLANS, 'capacity')
            self.store.save('plan', plan)
            self.plans[plan['id']] = plan
        return {key: plan[key] for key in ('id', 'digest', 'action', 'expires_at', 'confirmation', 'target', 'stages', 'current')}

    def execute(self, parameters):
        fields(parameters, ('plan_id', 'digest', 'confirmation'))
        require(isinstance(parameters['plan_id'], str) and ID.fullmatch(parameters['plan_id'])
                and isinstance(parameters['digest'], str) and SHA.fullmatch(parameters['digest'])
                and isinstance(parameters['confirmation'], str) and len(parameters['confirmation']) <= 320)
        with self.gate:
            require(not self.stopping.is_set(), 'unavailable')
            plan = self.plans.get(parameters['plan_id'])
            require(plan is not None, 'not_found')
            require(secrets.compare_digest(plan['digest'], parameters['digest'])
                    and secrets.compare_digest(plan['confirmation'], parameters['confirmation']), 'confirmation_mismatch')
            # A job's ID is its plan ID. Durable identity makes a crash between
            # writes or an HTTP retry unable to start the same operation twice.
            if plan['id'] in self.jobs:
                job = self.jobs[plan['id']]
                return {'id': job['id'], 'state': job['state']}
            require(plan['expires_epoch'] > self.now(), 'expired')
            self.prune()
            require(self.queue.qsize() < MAX_PENDING and len(self.jobs) < MAX_JOBS, 'capacity')
            job = {'id': plan['id'], 'action': plan['action'], 'state': 'queued', 'created_at': utc(self.now()),
                   'stages': [{'name': name, 'state': 'pending'} for name in plan['stages']]}
            self.store.save('job', job)
            self.jobs[job['id']] = job
            self.queue.put_nowait(job['id'])
            return {'id': job['id'], 'state': 'queued'}

    def job(self, parameters):
        fields(parameters, ('id',))
        require(isinstance(parameters['id'], str) and ID.fullmatch(parameters['id']))
        with self.gate:
            require(parameters['id'] in self.jobs, 'not_found')
            return copy.deepcopy(self.jobs[parameters['id']])

    def update(self, job, **values):
        with self.gate:
            job.update(values)
            self.store.save('job', job)

    def run(self, identity):
        with self.operations:
            with self.gate:
                plan, job = self.plans[identity], self.jobs[identity]
                if self.stopping.is_set():
                    self.interrupt_pending(identity)
                    return
                self.active_job = identity
                self.update(job, state='running')
            try:
                require(plan['expires_epoch'] > self.now(), 'expired')
                fresh = self.adapter.prepare(plan['action'], {'action': plan['action'], **(
                    {key: plan['target'][key] for key in ('host', 'port', 'ws_mode')} if plan['action'] == 'configure' else {})})
                require(fresh['review'] == plan['review'] and fresh['fingerprints'] == plan['fingerprints'], 'plan_changed')
                result = {'endpoint': plan['target']}
                failures = []
                entry_done = False
                partial_firewall = False
                for stage in job['stages']:
                    stage['state'] = 'running'
                    self.update(job)
                    try:
                        if stage['name'] == 'entry':
                            result.update(self.adapter.apply_entry(plan))
                            entry_done = True
                        elif stage['name'] == 'ca_sync':
                            result['ca_sync'] = self.adapter.sync_ca(plan)
                        else:
                            firewall = self.adapter.firewall(plan)
                            result['firewall_result'] = {key: firewall[key] for key in ('status', 'port')}
                            result['ca_preserved'] = True
                            partial_firewall = bool(firewall.get('mutation_attempted'))
                            require(firewall['status'] in ('ensured', 'saved_inactive'), 'firewall_failed')
                        stage['state'] = 'succeeded'
                        if stage['name'] == 'firewall' and result['firewall_result']['status'] == 'saved_inactive':
                            stage.update(code='saved_inactive', message='端口规则已保存；防火墙仍未启用，公网连通性未验证。')
                    except Exception as error:
                        audit(stage['name'], error, identity)
                        code = {'entry': 'entry_failed', 'ca_sync': 'ca_sync_failed', 'firewall': 'firewall_failed'}[stage['name']]
                        stage.update(state='failed', code=code, message=MESSAGES[code])
                        failures.append(code)
                        if stage['name'] == 'entry':
                            for pending in job['stages']:
                                if pending['state'] == 'pending':
                                    pending['state'] = 'skipped'
                            self.update(job)
                            break
                    self.update(job)
                try:
                    result['root_status'] = self.adapter.status()['root_status']
                except Exception:
                    pass
                state = ('partial' if entry_done or partial_firewall else 'failed') if failures else 'succeeded'
                values = {'state': state, 'result': result}
                if failures:
                    values['error'] = {'code': failures[0], 'message': MESSAGES[failures[0]]}
                self.update(job, **values)
            except Exception as error:
                audit('preflight', error, identity)
                code = error.code if isinstance(error, RpcError) else 'operation_failed'
                for stage in job['stages']:
                    if stage['state'] == 'pending':
                        stage['state'] = 'skipped'
                self.update(job, state='failed', error={'code': code, 'message': MESSAGES[code]})
            finally:
                with self.gate:
                    self.active_job = None

    def worker(self):
        while True:
            try:
                identity = self.queue.get(timeout=0.5)
            except queue.Empty:
                if self.stopping.is_set():
                    return
                continue
            try:
                if self.stopping.is_set():
                    self.interrupt_pending(identity)
                else:
                    self.run(identity)
            finally:
                self.queue.task_done()

    def interrupt_pending(self, identity):
        with self.gate:
            job = self.jobs[identity]
            if job['state'] == 'queued':
                for stage in job['stages']:
                    stage['state'] = 'skipped'
                self.update(job, state='interrupted', error={'code': 'interrupted', 'message': MESSAGES['interrupted']})

    def shutdown(self, timeout=120):
        self.stopping.set()
        while True:
            try:
                identity = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                self.interrupt_pending(identity)
            finally:
                self.queue.task_done()
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=timeout)
            if self.worker_thread.is_alive():
                # A later start marks the durable running job interrupted. Do
                # not falsely report a transaction completed or rolled back.
                audit('shutdown_timeout', RpcError('interrupted'), self.active_job)

    def dispatch(self, request):
        fields(request, ('schema', 'operation', 'parameters'))
        require(type(request['schema']) is int and request['schema'] == 1 and isinstance(request['parameters'], dict))
        operation = request['operation']
        require(operation in ('status', 'plan', 'execute', 'job'))
        if operation == 'status':
            fields(request['parameters'], ())
            return self.status()
        return getattr(self, operation)(request['parameters'])


def reply(engine, raw, uid):
    try:
        require(uid in (0, engine.policy['app_uid']), 'unauthorized')
        require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_REQUEST)
        return {'ok': True, 'data': engine.dispatch(strict_json(raw))}
    except Exception as error:
        code = error.code if isinstance(error, RpcError) else 'operation_failed'
        return {'ok': False, 'error': {'code': code, 'message': MESSAGES[code]}}


def connection(engine, client):
    try:
        client.settimeout(3)
        _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        require(uid in (0, engine.policy['app_uid']), 'unauthorized')
        data = b''
        while b'\n' not in data and len(data) <= MAX_REQUEST:
            chunk = client.recv(min(4096, MAX_REQUEST + 1 - len(data)))
            require(bool(chunk))
            data += chunk
        require(data.endswith(b'\n') and data.count(b'\n') == 1 and len(data) <= MAX_REQUEST + 1)
        response = reply(engine, data[:-1], uid)
    except Exception as error:
        code = error.code if isinstance(error, RpcError) else 'invalid_request'
        response = {'ok': False, 'error': {'code': code, 'message': MESSAGES[code]}}
    encoded = canonical(response)
    if len(encoded) > MAX_RESPONSE:
        encoded = canonical({'ok': False, 'error': {'code': 'operation_failed', 'message': MESSAGES['operation_failed']}})
    try:
        client.sendall(encoded + b'\n')
    except OSError:
        pass


def create_directory(path, mode=0o700):
    path = Path(path)
    if path.exists() or path.is_symlink():
        secure(path, directory=True)
    else:
        secure(path.parent, directory=True)
        path.mkdir(mode=mode)
    os.chmod(path, mode)
    return secure(path, mode, directory=True)


def systemctl(*arguments, timeout=45):
    binary = secure(shutil.which('systemctl', path='/usr/bin:/bin') or '/usr/bin/systemctl')
    result = subprocess.run([str(binary), *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, shell=False,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    require(result.returncode == 0, 'unavailable')


def service_text(policy):
    # No application path is interpolated into ExecStart or shell syntax.
    python = str(secure(Path(sys.executable).resolve(strict=True)))
    require(re.fullmatch(r'/[A-Za-z0-9_./+-]+', python), 'unsafe_path')
    return ('[Unit]\nDescription=NodeEndpoint restricted local web manager\nAfter=network.target\n\n'
        '[Service]\nType=simple\nUser=root\nGroup=root\nUMask=0077\n'
        'Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin\n'
        f'ExecStart={python} -I -B {CODE} serve\n'
        'Restart=on-failure\nRestartSec=3\nTimeoutStopSec=150\nKillMode=mixed\n'
        'PrivateTmp=false\nProtectHome=true\nProtectKernelTunables=true\nProtectKernelModules=true\n'
        'ProtectControlGroups=true\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n'
        'LockPersonality=true\nLimitNOFILE=256\nTasksMax=64\n\n[Install]\nWantedBy=multi-user.target\n')


def install(arguments):
    root_only()
    require(arguments.confirm == 'ENABLE WEB MANAGEMENT' or (arguments.confirm is None and sys.stdin.isatty()
            and input('输入 ENABLE WEB MANAGEMENT 授权本机应用管理既有入口：').strip() == 'ENABLE WEB MANAGEMENT'), 'confirmation_mismatch')
    tools = PinnedTools()
    manager = tools.manager
    state = manager.safe_state(arguments.state)
    plan, _ = tools.panel_ca.payload_from_state(state)
    require(plan.get('deployment') in ('fpm', 'octane') and plan.get('https') is True
            and all(isinstance(plan.get(key), str) and plan[key] for key in BOUND_FIELDS), 'manual_required')
    for key in ('app', 'app_user', 'php'):
        expected = getattr(arguments, key, None)
        require(expected is None or expected == plan[key], 'policy_changed')
    import pwd
    owner = pwd.getpwnam(plan['app_user'])
    require(owner.pw_uid > 0 and owner.pw_gid > 0, 'manual_required')
    env = manager.Environment(arguments.nginx)
    require(env.main == plan['main_config'] and plan['config_path'] in env.files, 'policy_changed')
    manager.trusted_file(plan['php'], True)
    ports = sorted(set([plan['port'], *(arguments.allow_port or [])]))
    policy = validate_policy({'schema': 1, 'version': VERSION, 'tools_sha256': TOOLS_SHA256,
        'manifest_sha256': tools.manifest_sha256, 'app': plan['app'], 'app_user': plan['app_user'],
        'app_uid': owner.pw_uid, 'app_gid': owner.pw_gid, 'php': plan['php'], 'nginx': env.binary, 'state': str(state),
        'binding': {key: plan[key] for key in BOUND_FIELDS}, 'ws_backend': plan.get('ws_backend'), 'allowed_ports': ports})
    if PRIVATE.exists() or PRIVATE.is_symlink():
        previous_jobs = Store().load_all('job')
        require(all(job.get('state') in TERMINAL for job in previous_jobs.values()), 'busy')
    if SERVICE.exists() or SERVICE.is_symlink():
        secure(SERVICE, 0o644)
        # Stop accepting RPC before replacement. The old process drains an
        # operation that raced the idle check; never restart over its writes.
        systemctl('stop', 'nodeendpoint-web-manager.service', timeout=170)
        if PRIVATE.exists():
            require(all(job.get('state') in TERMINAL for job in Store().load_all('job').values()), 'busy')
    create_directory(CODE.parent.parent)
    create_directory(CODE.parent)
    create_directory(PRIVATE)
    source = Path(__file__).resolve()
    info = source.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and info.st_nlink == 1 and info.st_size <= 262144, 'unsafe_path')
    atomic(CODE, source.read_bytes())
    atomic(CONFIG, canonical(policy))
    atomic(SERVICE, service_text(policy).encode('utf-8'), 0o644)
    systemctl('daemon-reload')
    systemctl('enable', 'nodeendpoint-web-manager.service')
    systemctl('start', 'nodeendpoint-web-manager.service')
    systemctl('is-active', '--quiet', 'nodeendpoint-web-manager.service')
    return {'installed': True, 'version': VERSION,
            'policy': {key: policy[key] for key in ('app', 'app_user', 'allowed_ports')}}


def serve():
    root_only()
    secure(CODE, 0o600)
    policy = validate_policy(read_private(CONFIG))
    tools = PinnedTools(policy['manifest_sha256'])
    create_directory(PRIVATE)
    import fcntl
    lock_path = PRIVATE / 'service.lock'
    if not lock_path.exists():
        atomic(lock_path, b'')
    fd = os.open(secure(lock_path, 0o600), os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RpcError('busy') from None
        adapter = RootAdapter(policy, tools)
        adapter.status()  # Fail closed before exposing a socket for a stale binding.
        engine = Engine(policy, adapter, Store())
        create_directory(RUNTIME, 0o750)
        os.chown(RUNTIME, 0, policy['app_gid'])
        if SOCKET.exists() or SOCKET.is_symlink():
            info = SOCKET.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, 'unsafe_path')
            SOCKET.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(SOCKET))
            os.chown(SOCKET, 0, policy['app_gid'])
            os.chmod(SOCKET, 0o660)
            listener.listen(8)
            listener.settimeout(0.5)
            import signal
            def stop(_signal, _frame):
                engine.stopping.set()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                while not engine.stopping.is_set():
                    try:
                        client, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with client:
                        connection(engine, client)
            finally:
                engine.shutdown()
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    installer = commands.add_parser('install')
    for name in ('app', 'app-user', 'php', 'nginx'):
        installer.add_argument('--' + name)
    installer.add_argument('--allow-port', type=int, action='append')
    installer.add_argument('--state', default=str(STATE), help='Root-only existing entry state; never accepted by RPC')
    installer.add_argument('--confirm', choices=('ENABLE WEB MANAGEMENT',))
    commands.add_parser('serve')
    commands.add_parser('status-policy')
    arguments = parser.parse_args(argv)
    try:
        root_only()
        # Root tools invoke fixed system binaries; never inherit PHP/loader or
        # OpenSSL configuration overrides from a caller's environment.
        os.environ.clear()
        os.environ.update({'PATH': '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin',
                           'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        if arguments.command == 'install':
            result = install(arguments)
        elif arguments.command == 'serve':
            serve()
            return 0
        else:
            policy = validate_policy(read_private(CONFIG))
            PinnedTools(policy['manifest_sha256'])
            result = {'version': VERSION, 'policy': {key: policy[key] for key in ('app', 'app_user', 'allowed_ports')}}
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
        return 0
    except Exception as error:
        audit('startup', error)
        code = error.code if isinstance(error, RpcError) else 'operation_failed'
        print(json.dumps({'ok': False, 'error': {'code': code, 'message': MESSAGES[code]}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
