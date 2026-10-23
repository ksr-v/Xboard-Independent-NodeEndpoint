"""Pinned local Docker execution for Root-approved, non-root panel bridges.

Container application code never runs on the host. Helpers come from the
verified Root tool directory; their JSON data travels through private pipes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading

import manager
from pki import SafetyError, validate_host

CLIENT_CONFIG = Path('/opt/nodeendpoint-docker-client')
SOCKET = Path('/var/run/docker.sock')
APP = '/www'
PHP = '/usr/local/bin/php'
FAILURE = 'Docker panel operation failed; application output and credentials were not displayed.'
LOOPBACK_FAILURE = 'Docker backend must be published only on loopback (127.0.0.1); change Compose ports and recreate the container.'
HEX = re.compile(r'[a-f0-9]{64}\Z')
IMAGE = re.compile(r'sha256:[a-f0-9]{64}\Z')
LABEL = re.compile(r'[a-z0-9][a-z0-9_-]{0,62}\Z')
VOLUME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z')
DAEMON = re.compile(r'[A-Za-z0-9:_.-]{8,128}\Z')
REQUIRED_BINDS = {'/www/.env', '/www/storage/app', '/www/.docker/.data', '/www/plugins'}
CONTEXT_KEYS = {'schema_version', 'container_id', 'image_id', 'daemon_id', 'project', 'service',
                'working_dir', 'app', 'uid', 'gid', 'php', 'host_port', 'container_port',
                'published_ip', 'bind_sources', 'volume_sources'}
HELPERS = {'panel_passphrase': 'panel_passphrase.php', 'sync_panel_ca': 'sync_panel_ca.php',
           'endpoint_settings': 'endpoint_settings.php'}
PROTECTED = ('/proc', '/sys', '/dev', '/run', '/var/run', '/etc', '/boot',
             '/var/lib/xboard-docker-installer',
             '/var/lib/node-endpoint', '/opt/nodeendpoint-quick', '/opt/xboard-root-tools',
             '/opt/nodeendpoint-docker-client', '/root/.ssh', '/root/.docker')


def _require(condition, message=FAILURE):
    if not condition:
        raise SafetyError(message)


def _path(value):
    return (isinstance(value, str) and value.startswith('/') and not value.startswith('//') and value != '/' and len(value) <= 4096
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
            and str(PurePosixPath(value)) == value and '..' not in PurePosixPath(value).parts)


def _overlaps(path, protected):
    a, b = PurePosixPath(path), PurePosixPath(protected)
    return a == b or a in b.parents or b in a.parents


def validate_context(context):
    """Pure structure validation, also usable when reading an offline sidecar."""
    _require(isinstance(context, dict) and set(context) == CONTEXT_KEYS)
    _require(context['schema_version'] == 1 and type(context['schema_version']) is int)
    _require(isinstance(context['container_id'], str) and HEX.fullmatch(context['container_id']))
    _require(isinstance(context['image_id'], str) and IMAGE.fullmatch(context['image_id']))
    _require(isinstance(context['daemon_id'], str) and DAEMON.fullmatch(context['daemon_id']))
    _require(isinstance(context['project'], str) and LABEL.fullmatch(context['project'])
             and context['service'] == 'xboard' and _path(context['working_dir']))
    _require(context['app'] == APP and context['php'] == PHP and context['container_port'] == 7001
             and type(context['container_port']) is int and context['published_ip'] == '127.0.0.1')
    _require(all(type(context[key]) is int and 1 <= context[key] <= 2147483647 for key in ('uid', 'gid')))
    _require(type(context['host_port']) is int and 1 <= context['host_port'] <= 65535)
    binds, volumes = context['bind_sources'], context['volume_sources']
    _require(isinstance(binds, dict) and REQUIRED_BINDS.issubset(binds) and len(binds) <= 64
             and all(_path(destination) and _path(source) for destination, source in binds.items()))
    _require(isinstance(volumes, dict) and len(volumes) <= 64 and not set(binds).intersection(volumes)
             and all(_path(destination) and isinstance(name, str) and VOLUME.fullmatch(name)
                     for destination, name in volumes.items()))
    for source in binds.values():
        _require(not any(_overlaps(source, protected) for protected in (*PROTECTED, '/var/lib/docker')))
    for destination in (*binds, *volumes):
        _require(not any(_overlaps(destination, protected) for protected in PROTECTED))
    return context


def _client_config():
    manager.linux_root()
    parent = manager.trusted_file(CLIENT_CONFIG.parent)
    _require(parent.is_dir())
    CLIENT_CONFIG.mkdir(mode=0o700, exist_ok=True)
    _require(not CLIENT_CONFIG.is_symlink())
    directory = manager.trusted_file(CLIENT_CONFIG)
    details = directory.stat()
    _require(directory.is_dir() and details.st_uid == 0 and stat.S_IMODE(details.st_mode) == 0o700)
    _require(all(path.name == 'config.json' for path in directory.iterdir()))
    config = directory / 'config.json'
    _require(not config.is_symlink())
    try:
        descriptor = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(b'{}\n')
    config = manager.trusted_file(config)
    info = config.stat()
    _require(config.is_file() and info.st_uid == 0 and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 64
             and json.loads(config.read_bytes()) == {})
    socket = SOCKET.resolve(strict=True)
    _require(str(socket) in ('/run/docker.sock', '/var/run/docker.sock'))
    for ancestor in socket.parents:
        info = ancestor.stat()
        _require(info.st_uid == 0 and not info.st_mode & 0o022)
    info = socket.stat()
    _require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o002)
    return directory


def _bounded_process(argv, input_data=None, max_output=4096, timeout=90):
    """Drain both pipes concurrently, terminating on a limit instead of buffering unbounded output."""
    process = None
    failed = threading.Event()
    output, errors = [], []
    threads = []
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   cwd='/', env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                                                'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})

        def stop():
            failed.set()
            try:
                process.kill()
            except OSError:
                pass

        def read(pipe, target, limit):
            size = 0
            try:
                while True:
                    block = pipe.read1(4096)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        stop()
                        break
                    target.append(block)
            except OSError:
                stop()

        def write():
            try:
                process.stdin.write(input_data)
                process.stdin.close()
            except (OSError, ValueError):
                stop()

        for pipe, target, limit in ((process.stdout, output, max_output), (process.stderr, errors, 8192)):
            thread = threading.Thread(target=read, args=(pipe, target, limit), daemon=True)
            thread.start()
            threads.append(thread)
        if input_data is not None:
            thread = threading.Thread(target=write, daemon=True)
            thread.start()
            threads.append(thread)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop()
            process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        _require(not failed.is_set() and process.returncode == 0 and not any(thread.is_alive() for thread in threads))
        return b''.join(output)
    except Exception:
        raise SafetyError(FAILURE) from None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass


def _docker(arguments, input_data=None, max_output=4096, timeout=90):
    try:
        directory = _client_config()
        binary = manager.trusted_file(shutil.which('docker') or '/usr/bin/docker', True)
        return _bounded_process([str(binary), '--config', str(directory), '--host', 'unix:///var/run/docker.sock',
                                 *arguments], input_data, max_output, timeout)
    except Exception:
        raise SafetyError(FAILURE) from None


def _json(data, limit):
    _require(isinstance(data, bytes) and len(data) <= limit)
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise SafetyError(FAILURE) from None


def _exec(context, code, input_data=None, max_output=4096):
    return _docker(['exec', '-i', '--user', str(context['uid']) + ':' + str(context['gid']),
                    '--workdir', APP, context['container_id'], PHP,
                    '-d', 'display_errors=0', '-d', 'log_errors=0', '-d', 'auto_prepend_file=',
                    '-d', 'auto_append_file=', '-r', code, '--', APP], input_data, max_output)


def _host_bind_path(source):
    """Reject an innocent-looking bind path that resolves into privileged host data."""
    _require(_path(source))
    try:
        resolved = str(Path(source).resolve(strict=True))
    except OSError:
        raise SafetyError(FAILURE) from None
    _require(_path(resolved) and not any(_overlaps(resolved, protected) for protected in (*PROTECTED, '/var/lib/docker')))
    return resolved


def assert_state_isolated(context, state):
    """Root state, including its PKI child, must never be inside a mounted source."""
    validate_context(context)
    root = Path(state).resolve()
    sources = list(context['bind_sources'].values())
    for name in set(context['volume_sources'].values()):
        volume = _json(_docker(['volume', 'inspect', name], max_output=65536), 65536)
        _require(isinstance(volume, list) and len(volume) == 1 and isinstance(volume[0], dict)
                 and volume[0].get('Name') == name and volume[0].get('Driver') == 'local'
                 and volume[0].get('Options') in (None, {}) and _path(volume[0].get('Mountpoint')))
        sources.append(volume[0]['Mountpoint'])
    for source in sources:
        mounted = Path(source).resolve(strict=True)
        _require(root != mounted and mounted not in root.parents,
                 'Root state and private PKI must remain outside every Docker mount source.')


def list_contexts(app_dir=None):
    """Discover only fully audited full-CID candidates; unrelated containers stay silent."""
    if app_dir is not None:
        app_dir = str(app_dir)
        _require(_path(app_dir))
    try:
        if not shutil.which('docker') or not SOCKET.exists() or not stat.S_ISSOCK(SOCKET.stat().st_mode):
            return []
    except OSError:
        return []
    try:
        output = _docker(['ps', '--no-trunc', '--filter', 'status=running', '--filter',
                          'label=com.docker.compose.service=xboard', '--format', '{{.ID}}'],
                         max_output=65536).decode('ascii')
    except (SafetyError, OSError, ValueError, UnicodeError):
        return []
    candidates = []
    seen = set()
    for identity in output.splitlines()[:64]:
        if not HEX.fullmatch(identity) or identity in seen:
            continue
        seen.add(identity)
        try:
            candidate = inspect_context(identity)
            validate_context(candidate)
            if app_dir is None or candidate['working_dir'] == app_dir:
                candidates.append(candidate)
        except (SafetyError, OSError, ValueError, TypeError):
            continue
    return candidates


def inspect_context(container_id, backend=None, expected=None):
    """Pin a running Compose xboard container; never follow a reused container name."""
    _require(isinstance(container_id, str) and HEX.fullmatch(container_id))
    if expected is not None:
        validate_context(expected)
        _require(expected['container_id'] == container_id, 'Docker container was replaced; review and confirm the new container as Root.')
    inspected = _json(_docker(['inspect', '--type', 'container', container_id], max_output=2097152), 2097152)
    _require(isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict))
    item = inspected[0]
    _require(item.get('Id') == container_id and isinstance(item.get('Image'), str) and IMAGE.fullmatch(item['Image']))
    state, host, config = item.get('State'), item.get('HostConfig'), item.get('Config')
    _require(isinstance(state, dict) and state.get('Running') is True and not state.get('Paused')
             and isinstance(host, dict) and isinstance(config, dict))
    _require(host.get('Privileged') is False and host.get('NetworkMode') not in ('host', 'none')
             and not str(host.get('NetworkMode', '')).startswith('container:')
             and host.get('PidMode', '') == '' and host.get('IpcMode', '') in ('', 'private', 'shareable')
             and host.get('UTSMode', '') == '' and host.get('CgroupnsMode', '') != 'host'
             and host.get('UsernsMode', '') != 'host' and not host.get('CapAdd') and not host.get('Devices'))
    labels = config.get('Labels')
    _require(isinstance(labels, dict) and config.get('WorkingDir') == APP)
    environment = config.get('Env', [])
    _require(isinstance(environment, list) and all(isinstance(entry, str) for entry in environment))
    for entry in environment:
        name, separator, value = entry.partition('=')
        _require(separator and name)
        dangerous = (name in ('PHPRC', 'PHP_INI_SCAN_DIR', 'PHP_VALUE', 'PHP_ADMIN_VALUE', 'BASH_ENV', 'ENV')
                     or name.startswith(('LD_', 'DYLD_'))
                     or (name.startswith('PHP') and name.endswith('_DIR')
                         and not (name == 'PHP_INI_DIR' and value == '/usr/local/etc/php')))
        _require(not dangerous, 'Docker PHP or loader environment overrides are not allowed for the Root bridge.')
    project, service, working = (labels.get(key) for key in ('com.docker.compose.project', 'com.docker.compose.service',
                                                           'com.docker.compose.project.working_dir'))
    _require(isinstance(project, str) and LABEL.fullmatch(project) and service == 'xboard' and _path(working))
    network = item.get('NetworkSettings')
    _require(isinstance(network, dict) and isinstance(network.get('Ports'), dict))
    bindings = network['Ports'].get('7001/tcp')
    _require(isinstance(bindings, list) and bindings, LOOPBACK_FAILURE)
    ports = set()
    ipv4 = False
    for binding in bindings:
        _require(isinstance(binding, dict) and binding.get('HostIp') in ('127.0.0.1', '::1'), LOOPBACK_FAILURE)
        port = binding.get('HostPort')
        _require(isinstance(port, str) and port.isdecimal() and 1 <= int(port) <= 65535, LOOPBACK_FAILURE)
        ports.add(int(port))
        ipv4 = ipv4 or binding['HostIp'] == '127.0.0.1'
    _require(ipv4 and len(ports) == 1, LOOPBACK_FAILURE)
    port = next(iter(ports))
    _require(backend is None or backend == 'http://127.0.0.1:' + str(port), LOOPBACK_FAILURE)
    binds, volumes = {}, {}
    mounts = item.get('Mounts')
    _require(isinstance(mounts, list) and len(mounts) <= 64)
    for mount in mounts:
        _require(isinstance(mount, dict) and _path(mount.get('Destination')) and mount.get('RW') is True)
        destination, source, kind = mount['Destination'], mount.get('Source'), mount.get('Type')
        _require(destination not in binds and destination not in volumes)
        _require(not any(_overlaps(destination, protected) for protected in PROTECTED))
        if kind == 'bind':
            _require(_path(source) and not any(_overlaps(source, protected) for protected in (*PROTECTED, '/var/lib/docker')))
            binds[destination] = _host_bind_path(source)
        elif kind == 'volume':
            name = mount.get('Name')
            _require(isinstance(name, str) and VOLUME.fullmatch(name) and mount.get('Driver') == 'local')
            volume = _json(_docker(['volume', 'inspect', name], max_output=65536), 65536)
            _require(isinstance(volume, list) and len(volume) == 1 and isinstance(volume[0], dict)
                     and volume[0].get('Name') == name and volume[0].get('Driver') == 'local'
                     and volume[0].get('Options') in (None, {}))
            volumes[destination] = name
        else:
            raise SafetyError(FAILURE)
    _require(REQUIRED_BINDS.issubset(binds), 'Docker panel requires persistent .env, storage/app, SQLite data and plugins bind mounts.')
    daemon = _json(_docker(['info', '--format', '{{json .ID}}']), 4096)
    context = {'schema_version': 1, 'container_id': container_id, 'image_id': item['Image'], 'daemon_id': daemon,
               'project': project, 'service': service, 'working_dir': working, 'app': APP, 'uid': 1, 'gid': 1,
               'php': PHP, 'host_port': port, 'container_port': 7001, 'published_ip': '127.0.0.1',
               'bind_sources': binds, 'volume_sources': volumes}
    code = ("if (PHP_SAPI !== 'cli' || !function_exists('posix_geteuid') || !function_exists('posix_getegid')"
            " || posix_geteuid() === 0 || posix_getegid() === 0 || realpath('/www') !== '/www'"
            " || !is_file('/www/artisan') || !is_file('/www/vendor/autoload.php') || !is_file('/www/bootstrap/app.php')) exit(1);"
            "echo json_encode(['uid'=>posix_geteuid(),'gid'=>posix_getegid(),'app'=>realpath('/www'),'php'=>PHP_BINARY]);")
    probe = _json(_docker(['exec', '--user', 'www', '--workdir', APP, container_id, PHP,
                          '-d', 'display_errors=0', '-d', 'log_errors=0', '-d', 'auto_prepend_file=',
                          '-d', 'auto_append_file=', '-r', code]), 4096)
    _require(isinstance(probe, dict) and set(probe) == {'uid', 'gid', 'app', 'php'}
             and probe['app'] == APP and probe['php'] == PHP)
    context.update(uid=probe['uid'], gid=probe['gid'])
    validate_context(context)
    _require(expected is None or context == expected, 'Docker execution identity changed; review and confirm its context as Root.')
    return context


def _refresh(context):
    validate_context(context)
    return inspect_context(context['container_id'], 'http://127.0.0.1:' + str(context['host_port']), expected=context)


def run_helper(context, helper_key, payload, max_output=4096):
    _require(isinstance(helper_key, str) and helper_key in HELPERS and isinstance(payload, dict)
             and type(max_output) is int and 1 <= max_output <= 65536)
    try:
        context = _refresh(context)
        helper = manager.trusted_file(Path(__file__).with_name(HELPERS[helper_key]))
        _require(helper.is_file() and helper.stat().st_nlink == 1 and helper.stat().st_size <= 65536)
        code = helper.read_text(encoding='utf-8')
        _require(code.startswith('<?php\n'))
        data = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode('ascii')
        _require(len(data) <= 65536)
        result = _json(_exec(context, code[6:], data, max_output), max_output)
        _require(isinstance(result, dict))
        return result
    except Exception:
        raise SafetyError(FAILURE) from None


def run_artisan(context, arguments):
    _require(isinstance(arguments, list) and all(isinstance(value, str) for value in arguments))
    allowed = arguments in (['route:list', '--json'], ['list', '--raw'])
    if arguments[:1] == ['xboard:configure-endpoint']:
        options = {}
        for argument in arguments[1:]:
            key, separator, value = argument.partition('=')
            _require(separator and key in ('--host', '--port', '--ws-mode') and key not in options)
            options[key] = value
        _require(set(options) == {'--host', '--port', '--ws-mode'})
        _require(validate_host(options['--host'])[0] == options['--host']
                 and options['--port'].isdecimal() and 1 <= int(options['--port']) <= 65535
                 and options['--ws-mode'] in ('rest', 'rewrite'))
        allowed = True
    _require(allowed)
    context = _refresh(context)
    data = _docker(['exec', '--user', str(context['uid']) + ':' + str(context['gid']), '--workdir', APP,
                    context['container_id'], PHP, '-d', 'display_errors=0', '-d', 'log_errors=0',
                    '-d', 'auto_prepend_file=', '-d', 'auto_append_file=',
                    'artisan', *arguments], max_output=2097152)
    try:
        return data.decode('utf-8')
    except UnicodeError:
        raise SafetyError(FAILURE) from None


def read_proxy_profile(context):
    from nginx import AUDITED_PROXY_TRUST_SHA256
    context = _refresh(context)
    code = ("if (!function_exists('posix_geteuid') || posix_geteuid() === 0) exit(1);"
            "echo json_encode(['sha256'=>hash_file('sha256','/www/app/Http/Middleware/TrustProxies.php')]);")
    value = _json(_exec(context, code), 4096)
    _require(isinstance(value, dict) and set(value) == {'sha256'} and value['sha256'] in AUDITED_PROXY_TRUST_SHA256,
             'Docker TrustProxies profile is not audited; installation refused.')
    return value['sha256']
