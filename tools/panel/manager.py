#!/usr/bin/env python3
"""SSH-only Linux Root manager. Original site/configuration is read-only."""
from __future__ import annotations
import argparse
import datetime as dt
import getpass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pki import PKI, SafetyError, VERSION, atomic_write, read_json, write_json, validate_host
from nginx import (API_METHODS, parse, dump_files, expanded, http_include_patterns, values,
                   walk, check_port, literal_loopback_backend, render, sha256)


def command(args, timeout=45, allow_failure=False):
    if sys.platform == 'linux':
        executable = shutil.which(str(args[0])) or str(args[0])
        args = [trusted_file(executable, True), *args[1:]]
    try:
        result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SafetyError('Required command failed: ' + str(args[0])) from exc
    if result.returncode and not allow_failure:
        # Configuration may contain secrets: do not echo full stderr/stdout.
        raise SafetyError(f'{Path(str(args[0])).name} exited {result.returncode}; inspect it locally')
    return result.stdout.decode(errors='replace'), result.stderr.decode(errors='replace'), result.returncode


def trusted_file(path, executable=False):
    original = Path(path).absolute()
    # Reload uses the original invocation path, so a safe symlink target must
    # not conceal a replaceable link or writable directory on that path.
    for component in (original, *original.parents):
        entry = component.lstat()
        if entry.st_uid != 0 or (not component.is_symlink() and entry.st_mode & 0o022):
            raise SafetyError('Original command/config path must be root-owned without group/other writes')
    path = original.resolve(strict=True)
    details = path.stat()
    if details.st_uid != 0 or details.st_mode & 0o022:
        raise SafetyError('Root command/config must be owned by root and not writable by group/others: ' + str(path))
    if executable and not os.access(path, os.X_OK):
        raise SafetyError('Selected executable is not executable')
    for ancestor in path.parents:
        item = ancestor.stat()
        if item.st_uid != 0 or item.st_mode & 0o022:
            raise SafetyError('Command/config ancestry must be owned by Root without group/other writes')
    return path


def linux_root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SafetyError('Mutation requires Linux Root over SSH; this tool never grants Root to the Web process')


def safe_state(path):
    path = Path(path)
    if not path.is_absolute() or path == Path('/') or len(path.parts) < 3 or '..' in path.parts:
        raise SafetyError('State directory requires a dedicated absolute private location')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise SafetyError('State path must not contain symlinks')
        if ancestor.exists() and (ancestor.stat().st_uid != 0 or ancestor.stat().st_mode & 0o022):
            raise SafetyError('State directory ancestry must be root-owned without group/other writes')
    return path.resolve()


STATE_MARKER = '.node-endpoint-state.json'


def initialize_state(path):
    """Claim only a new/empty dedicated directory, before changing any mode."""
    path = Path(path)
    marker = path / STATE_MARKER
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SafetyError('State location must be a dedicated directory')
    if marker.is_symlink():
        raise SafetyError('State ownership marker must not be a symlink')
    if marker.exists():
        details = marker.stat()
        if not marker.is_file() or details.st_nlink != 1:
            raise SafetyError('Invalid state ownership marker file')
        if os.name == 'posix' and (details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600):
            raise SafetyError('State ownership marker must be private and root-owned')
        try:
            owned = read_json(marker)
        except (ValueError, OSError) as exc:
            raise SafetyError('Invalid state ownership marker') from exc
        if not isinstance(owned, dict) or owned.get('tool') != 'NodeEndpoint' or owned.get('schema_version') != 1 \
                or owned.get('path') != str(path.resolve()) or not isinstance(owned.get('ownership'), str) \
                or not re.fullmatch(r'[a-f0-9]{32}', owned['ownership']):
            raise SafetyError('State directory ownership differs')
    else:
        if path.exists() and any(path.iterdir()):
            raise SafetyError('Refusing nonempty directory without NodeEndpoint ownership marker')
        path.mkdir(mode=0o755, parents=True, exist_ok=True)
        owned = {'tool': 'NodeEndpoint', 'schema_version': 1, 'path': str(path.resolve()),
                 'ownership': secrets.token_hex(16)}
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(owned, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    # Validate all existing private objects before changing the directory.
    for name in ('pki', 'backups'):
        child = path / name
        if child.is_symlink() or (child.exists() and not child.is_dir()):
            raise SafetyError('Private state subdirectory must be a real directory')
        if child.exists() and os.name == 'posix' and (child.stat().st_uid != 0 or child.stat().st_mode & 0o077):
            raise SafetyError('Private state subdirectory must already be root-owned and private')
    lock = path / 'manager.lock'
    if lock.is_symlink() or (lock.exists() and (not lock.is_file() or lock.stat().st_nlink != 1)):
        raise SafetyError('Invalid manager lock file')
    if lock.exists() and os.name == 'posix' and (lock.stat().st_uid != 0 or lock.stat().st_mode & 0o077):
        raise SafetyError('Manager lock must already be root-owned and private')
    os.chmod(path, 0o755)
    for name in ('pki', 'backups'):
        (path / name).mkdir(mode=0o700, exist_ok=True)
    return path


def assert_export_destination(state, destination):
    apps = read_json(Path(state) / 'protected-apps.json', [])
    current = read_json(Path(state) / 'installation.json', {}).get('plan', {}).get('app')
    if not isinstance(apps, list) or any(not isinstance(x, str) or not Path(x).is_absolute() for x in apps):
        raise SafetyError('Invalid protected application registry')
    if current:
        apps = [*apps, current]
    destination = Path(destination).resolve()
    for app in apps:
        application = Path(app).resolve()
        if destination == application or application in destination.parents:
            raise SafetyError('CA private export must never be placed in the application/website tree')


def active_master(nginx=None):
    selected = Path(nginx).resolve(strict=True) if nginx else None
    selected_identity = selected.stat() if selected else None
    masters = []
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            raw = (folder / 'cmdline').read_bytes().replace(b'\x00', b' ').decode().strip()
            if re.match(r'^(nginx|openresty): master process ', raw):
                text = re.sub(r'^(nginx|openresty): master process ', '', raw)
                executable = folder / 'exe'
                resolved = executable.resolve()
                if selected:
                    running = executable.stat()
                    # Count aliases of the same physical executable too: two
                    # masters running hard-linked binary paths remain ambiguous.
                    if (running.st_dev, running.st_ino) != (selected_identity.st_dev, selected_identity.st_ino):
                        continue
                masters.append({'pid': int(folder.name), 'executable': str(resolved), 'argv': shlex.split(text)})
        except (OSError, UnicodeError, ValueError):
            continue
    if len(masters) != 1:
        qualifier = ' matching the explicitly selected executable/inode' if selected else ''
        raise SafetyError('Exactly one running Nginx/OpenResty master' + qualifier + ' must be identified; found ' + str(len(masters)))
    return masters[0]


class Environment:
    def __init__(self, nginx=None):
        if sys.platform != 'linux':
            raise SafetyError('Environment inspection is supported on Linux only')
        self.master = active_master(nginx)
        selected = Path(nginx).resolve() if nginx else Path(self.master['executable'])
        if selected != Path(self.master['executable']):
            raise SafetyError('Selected Nginx is not the executable of the running master')
        process_exe = Path('/proc') / str(self.master['pid']) / 'exe'
        if (selected.stat().st_dev, selected.stat().st_ino) != (process_exe.stat().st_dev, process_exe.stat().st_ino):
            raise SafetyError('Running Nginx is in another mount namespace; use a verified host reverse proxy')
        self.binary = str(trusted_file(selected, True))
        argv = self.master['argv']
        self.args = []
        for flag in ('-c', '-p'):
            if flag in argv:
                index = argv.index(flag)
                if index + 1 >= len(argv):
                    raise SafetyError('Unrecognized active Nginx invocation')
                self.args += [flag, argv[index + 1]]
        if '-g' in argv:
            # Process titles can lose the original shell quotes. Only trailing
            # global directives can be reconstructed without guessing options.
            global_words = argv[argv.index('-g') + 1:]
            if not global_words or any(x.startswith('-') for x in global_words):
                raise SafetyError('Cannot safely reconstruct active Nginx -g directives')
            self.args += ['-g', ' '.join(global_words)]
        _, version, _ = command([self.binary, '-V'])
        self.version = version.splitlines()[0] if version else 'unknown'
        prefix = next((x.split('=', 1)[1] for x in shlex.split(version) if x.startswith('--prefix=')), None)
        self.prefix = argv[argv.index('-p') + 1] if '-p' in argv else prefix
        if not self.prefix or not Path(self.prefix).is_absolute():
            raise SafetyError('Cannot resolve active Nginx prefix')
        self.refresh()

    def nginx(self, option):
        return command([self.binary, *self.args, *option])

    def refresh(self):
        raw, _, _ = self.nginx(['-T'])
        self.files = dump_files(raw)
        self.main = next(iter(self.files))
        trusted_file(self.main)
        # Nginx resolves relative includes against the main configuration directory.
        self.prefix = str(Path(self.main).parent)
        self.nodes = expanded(parse(self.files[self.main], self.main), self.files, self.prefix, (self.main,))
        self.includes = http_include_patterns(parse(self.files[self.main], self.main), self.prefix, self.files, seen=(self.main,))
        self.ss, _, _ = command(['ss', '-H', '-ltnp'])
        self.unix, _, _ = command(['ss', '-H', '-lxnp'])

    def report(self):
        firewall = {}
        for name, argv in (('ufw', ['ufw', 'status']), ('firewalld', ['firewall-cmd', '--state']),
                           ('nftables', ['nft', 'list', 'ruleset']), ('iptables', ['iptables', '-S'])):
            if shutil.which(argv[0]):
                out, _, code = command(argv, allow_failure=True)
                firewall[name] = {'exit_code': code, 'present': True,
                                  'active': 'active' in out.lower() if name == 'ufw' else code == 0,
                                  'rules_sha256': sha256(out)}
        docker = []
        if shutil.which('docker'):
            out, _, code = command(['docker', 'ps', '--format', '{{.ID}} {{.Ports}}'], allow_failure=True)
            if code == 0:
                docker = out.splitlines()
        candidates = []
        for name, text in self.files.items():
            for node in parse(text, name):
                if node.words == ['server']:
                    entry = {'config': name, 'server_names': values(node.children, 'server_name'),
                             'listeners': values(node.children, 'listen'), 'document_roots': values(node.children, 'root'),
                             'fpm_detected': any(x.words[0] == 'fastcgi_pass' for x in walk(node.children)),
                             'http_proxy_detected': any(x.words[0] == 'proxy_pass' for x in walk(node.children)),
                             'websocket_upgrade_detected': any(x.words[:2] == ['proxy_set_header', 'Upgrade']
                                                               for x in walk(node.children))}
                    candidates.append(entry)
        return {'version': VERSION, 'nginx': self.version, 'main_config': self.main,
                'loaded_files': [{'path': x, 'sha256': sha256(text)} for x, text in self.files.items()],
                'http_include_patterns': self.includes,
                'listeners': self.ss.splitlines(), 'docker_published_ports': docker, 'deployment_candidates': candidates,
                'firewall': firewall,
                'warnings': ['Cloud security groups cannot be verified or modified locally.',
                             'Backend, Xboard route table and selected source site must be confirmed before installation.']}

    def plan(self, args, state, installed):
        host, _ = validate_host(args.host)
        port = args.port
        if port in (80, 443):
            raise SafetyError('Independent entry cannot use original website ports 80 or 443')
        target = Path(args.include_dir).absolute() / 'node-endpoint.conf'
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise SafetyError('Selected include directory is missing or a symlink')
        trusted_file(target.parent)
        if not any(fnmatch_path(str(target), pattern) for pattern in self.includes):
            raise SafetyError('Managed file does not match an observed HTTP-level include; main configuration is never edited')
        own = installed.get('config_path') == str(target)
        if target.exists() and not own:
            raise SafetyError('Refusing existing configuration file without matching ownership state')
        if installed and not own:
            raise SafetyError('Existing entry uses another include directory; preserve it and use modify')
        if own and (target.is_symlink() or sha256(target.read_bytes()) != installed.get('config_sha256')):
            raise SafetyError('Managed configuration was changed externally; inspect it before proceeding')
        without_own = {name: text for name, text in self.files.items() if name != str(target)}
        nodes = expanded(parse(without_own[self.main], self.main), without_own, self.prefix)
        check_port(port, nodes, self.ss, own_config=own and installed.get('plan', {}).get('port') == port)
        site_path = str(Path(args.site_config).absolute())
        if site_path not in self.files:
            raise SafetyError('Selected source site is not loaded by active Nginx')
        trusted_file(site_path)
        site_nodes = expanded(parse(self.files[site_path], site_path), self.files, self.prefix)
        sites = [x for x in site_nodes if x.words == ['server'] and
                 any(args.site_name in names for names in values(x.children, 'server_name'))]
        if len(sites) != 1:
            raise SafetyError('Source site must match exactly one loaded server block')
        site = sites[0]
        app = Path(args.app).resolve(strict=True)
        if state == app or app in state.parents or state == app / 'public' or app / 'public' in state.parents:
            raise SafetyError('PKI/state must be outside the entire Xboard application tree')
        if not (app / 'artisan').is_file() or not (app / 'public/index.php').is_file():
            raise SafetyError('Selected application does not contain Laravel artisan and public/index.php')
        backend_nodes = [x for x in walk(site.children) if x.words[0] in ('proxy_pass', 'fastcgi_pass')]
        ws_locations = [x for x in walk(site.children) if x.words[0] == 'location' and
                        any(word in ('/ws', '/ws/') for word in x.words)]
        ws_nodes = [x for location in ws_locations for x in walk(location.children)]
        backend_nodes = [x for x in backend_nodes if x not in ws_nodes]
        proxy_profile = None
        if args.deployment == 'fpm':
            roots = values(site.children, 'root')
            if roots != [[str(app / 'public')]]:
                raise SafetyError('Observed FPM site document root does not exactly match application public directory')
            candidates = set(x.words[1] for x in backend_nodes if x.words[0] == 'fastcgi_pass')
            if len(candidates) != 1 or args.backend not in candidates:
                raise SafetyError('FPM backend must exactly match one observed fastcgi_pass')
            if args.backend.startswith('unix:'):
                location = Path(args.backend[5:])
                if not location.is_absolute() or not stat.S_ISSOCK(location.stat().st_mode) or str(location) not in self.unix:
                    raise SafetyError('Observed FPM Unix socket is not an active socket')
            else:
                _, backend_port = literal_loopback_backend('http://' + args.backend)
                require_listener(self.ss, backend_port)
            backend = args.backend
        else:
            from nginx import verify_proxy_trust
            proxy_profile = verify_proxy_trust(app)
            upstreams = {x.words[1]: x.children for x in walk(self.nodes) if x.words[0] == 'upstream'}
            candidates = set()
            for node in backend_nodes:
                if node.words[0] == 'proxy_pass':
                    value = node.words[1]
                    alias = value[7:] if value.startswith('http://') else value
                    candidates.add(literal_loopback_backend(alias if alias in upstreams else value, upstreams)[0])
            backend, backend_port = literal_loopback_backend(args.backend)
            if candidates != {backend}:
                raise SafetyError('Backend must exactly match the single observed loopback HTTP proxy target')
            require_listener(self.ss, backend_port)
            if args.deployment == 'docker':
                if not args.container:
                    raise SafetyError('Docker deployment requires explicit container identity')
                inspect, _, _ = command(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', args.container])
                mapping = json.loads(inspect)
                if not any(str(backend_port) == binding.get('HostPort') for bindings in mapping.values() if bindings for binding in bindings):
                    raise SafetyError('Selected Docker container does not publish the observed backend port')
                mounts_json, _, _ = command(['docker', 'inspect', '--format', '{{json .Mounts}}', args.container])
                mounts = json.loads(mounts_json)
                if not args.container_app or not any(Path(mount.get('Source', '')).resolve() == app and
                       mount.get('Destination') == args.container_app for mount in mounts):
                    raise SafetyError('Docker application must match an observed host bind mount exactly')
            probe_backend(backend, host, port, args.http)
        routes = application_routes(args, app)
        from nginx import verify_routes
        verify_routes(routes)
        ws_backend = None
        if args.enable_ws:
            if not args.ws_backend:
                raise SafetyError('WebSocket requires explicitly confirmed --ws-backend')
            ws_backend, ws_port = literal_loopback_backend(args.ws_backend)
            observed = set(x.words[1].rstrip('/') for x in ws_nodes if x.words[0] == 'proxy_pass')
            # Current all-in-one Xboard Caddy forwards REST and /ws through one
            # observed root proxy. Require explicit Upgrade handling there.
            for location in walk(site.children):
                if location.words[0] != 'location' or '/' not in location.words:
                    continue
                directives = list(walk(location.children))
                if any(x.words[:2] == ['proxy_set_header', 'Upgrade'] for x in directives) and any(
                       x.words[:2] == ['proxy_set_header', 'Connection'] for x in directives):
                    observed.update(x.words[1].rstrip('/') for x in directives if x.words[0] == 'proxy_pass')
            if ws_backend not in observed:
                raise SafetyError('WebSocket backend is not observed in the source site /ws location')
            require_listener(self.ss, ws_port)
        return {'version': VERSION, 'ownership': installed.get('ownership') or secrets.token_hex(16),
                'host': host, 'port': port, 'https': not args.http, 'ipv6': Path('/proc/net/if_inet6').exists(),
                'deployment': args.deployment, 'backend': backend, 'app': str(app),
                'ws_backend': ws_backend, 'config_path': str(target), 'main_config': self.main,
                'source_site': site_path, 'source_site_sha256': sha256(self.files[site_path]),
                'route_profile_sha256': sha256(json.dumps(routes, sort_keys=True)),
                'proxy_trust_profile_sha256': proxy_profile,
                'container': args.container, 'app_user': args.app_user, 'php': args.php,
                'container_app': args.container_app, 'container_user': args.container_user,
                'site_name': args.site_name, 'include_dir': str(target.parent)}


def fnmatch_path(path, pattern):
    import fnmatch
    return fnmatch.fnmatchcase(path, pattern)


def require_listener(ss_output, port):
    from nginx import port_from_listener
    if not any(len(line.split()) >= 5 and port_from_listener(line.split()[3]) == port
               for line in ss_output.splitlines()):
        raise SafetyError('Confirmed backend does not have an observed listening TCP socket')


def application_routes(args, app):
    if args.deployment == 'docker':
        if not args.container_app or not args.container_user or args.container_user in ('0', 'root'):
            raise SafetyError('Docker route audit requires explicit non-root --container-user and --container-app')
        route_args = ['docker', 'exec', '--user', args.container_user, '--workdir', args.container_app,
                      args.container, 'php', 'artisan', 'route:list', '--json']
    else:
        import pwd
        php = trusted_file(args.php or shutil.which('php') or '', True)
        owner = pwd.getpwnam(args.app_user) if args.app_user else pwd.getpwuid((app / 'artisan').stat().st_uid)
        if owner.pw_uid == 0:
            raise SafetyError('Never execute application PHP as Root; supply a verified non-root --app-user')
        route_args = ['runuser', '-u', owner.pw_name, '--', str(php), str(app / 'artisan'), 'route:list', '--json']
    out, _, _ = command(route_args, timeout=60)
    try:
        return json.loads(out)
    except ValueError as exc:
        raise SafetyError('Laravel route:list did not return JSON; unknown build refused') from exc


def probe_backend(backend, host, port, http_mode):
    from urllib.parse import urlsplit
    url = urlsplit(backend)
    authority = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=8)
    try:
        connection.request('GET', '/api/v2/server/handshake', headers={'Host': authority,
                           'X-Forwarded-Proto': 'http' if http_mode else 'https', 'X-Forwarded-Port': str(port)})
        response = connection.getresponse()
        if response.status not in (400, 401, 403, 422) or response.getheader('Location'):
            raise SafetyError('Unauthenticated backend Node API probe failed or redirected; installation refused')
        response.read(65536)
    finally:
        connection.close()


def local_probe(plan, pki):
    connection = http.client.HTTPConnection('127.0.0.1', plan['port'], timeout=8)
    connection.connect()
    if plan['https']:
        context = ssl.create_default_context(cafile=str(pki.ca_dir() / 'ca.pem'))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection.sock = context.wrap_socket(connection.sock, server_hostname=plan['host'])
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if actual != pki.status()['server']['sha256']:
            connection.close()
            raise SafetyError('Reload did not serve the expected certificate')
    authority = f'[{plan["host"]}]:{plan["port"]}' if ':' in plan['host'] else f'{plan["host"]}:{plan["port"]}'
    try:
        connection.request('GET', '/api/v2/server/handshake', headers={'Host': authority})
        response = connection.getresponse()
        if response.status not in (400, 401, 403, 422) or response.getheader('Location'):
            raise SafetyError('Independent entry API probe failed or redirected')
        response.read(65536)
        return {'tls': 'verified' if plan['https'] else 'http_explicitly_confirmed',
                'rest_unauthenticated': 'reachable_authentication_required',
                'rest_authenticated': 'NOT TESTED', 'websocket_authenticated': 'NOT TESTED'}
    finally:
        connection.close()


def probe_after_reload(plan, pki):
    """Nginx reload is asynchronous; verify the expected version with a bound."""
    import time
    last = None
    for _ in range(10):
        try:
            return local_probe(plan, pki)
        except (OSError, ssl.SSLError, http.client.HTTPException, SafetyError) as exc:
            last = exc
            time.sleep(0.3)
    raise SafetyError('Post-reload probe failed; expected certificate/routing was not served') from last


class Manager:
    SNAPSHOT = {'installation': 'installation.json', 'pki_state': 'pki/state.json',
                'trust_bundle': 'pki/trust-bundle.pem', 'public_status': 'public-status.json',
                'managed_passphrases': 'pki/managed-passphrases.json'}

    def __init__(self, state, env, openssl='openssl'):
        self.state, self.env = Path(state), env
        self.pki = PKI(state, openssl)

    @property
    def installed(self):
        return read_json(self.state / 'installation.json', {})

    def assert_owned(self):
        meta = self.installed
        if not meta or not re.fullmatch(r'[a-f0-9]{32}', meta.get('ownership', '')):
            raise SafetyError('No owned entry installation state')
        config = Path(meta['config_path'])
        if config.is_symlink() or not config.is_file() or sha256(config.read_bytes()) != meta['config_sha256']:
            raise SafetyError('Managed configuration changed externally; refusing to overwrite it')
        if ('ownership ' + meta['ownership']).encode() not in config.read_bytes():
            raise SafetyError('Configuration ownership marker differs')
        return meta

    def backup(self, config_path=None):
        identity = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4)
        folder = self.state / 'backups' / identity
        folder.mkdir(parents=True, mode=0o700)
        os.chmod(folder.parent, 0o700)
        files = dict(self.SNAPSHOT)
        meta = self.installed
        config_path = config_path or meta.get('config_path')
        manifest = {'version': VERSION, 'config_path': str(config_path) if config_path else None, 'files': {}}
        paths = {label: self.state / relative for label, relative in files.items()}
        if config_path:
            paths['nginx_config'] = Path(config_path)
        for label, path in paths.items():
            if path.is_symlink():
                raise SafetyError('Cannot back up symlink')
            exists = path.is_file()
            data = path.read_bytes() if exists else b''
            mode = stat.S_IMODE(path.stat().st_mode) if exists else 0o600
            # Windows has no POSIX permission bits; record the intended Linux
            # role while preserving and validating actual Linux modes.
            if os.name != 'posix':
                mode = 0o644 if label in ('nginx_config', 'trust_bundle', 'public_status') else 0o600
            if mode not in (0o600, 0o644):
                raise SafetyError('Managed snapshot file has unexpected permissions')
            manifest['files'][label] = {'present': exists, 'sha256': sha256(data), 'mode': mode}
            if exists:
                atomic_write(folder / label, data)
        write_json(folder / 'manifest.json', manifest)
        return folder

    def restore(self, folder):
        manifest = read_json(Path(folder) / 'manifest.json', {})
        if manifest.get('version') != VERSION:
            raise SafetyError('Unknown backup version')
        pending = []
        required = set(self.SNAPSHOT) - {'managed_passphrases'}
        if not isinstance(manifest.get('files'), dict) or not required.issubset(manifest['files']):
            raise SafetyError('Incomplete backup manifest')
        for label, item in manifest['files'].items():
            if label == 'nginx_config':
                path = Path(manifest['config_path'])
            elif label in self.SNAPSHOT:
                path = self.state / self.SNAPSHOT[label]
            else:
                raise SafetyError('Unknown backup file')
            if path.is_symlink():
                raise SafetyError('Refusing symlink during restore')
            if not isinstance(item.get('present'), bool) or item.get('mode') not in (0o600, 0o644):
                raise SafetyError('Invalid backup file metadata')
            if not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
                raise SafetyError('Invalid backup hash')
            data = None
            if item['present']:
                data = (Path(folder) / label).read_bytes()
                if sha256(data) != item['sha256']:
                    raise SafetyError('Backup checksum differs; original evidence preserved')
            pending.append((path, item, data))
        # Validate the complete snapshot before the first write, preventing a
        # corrupt later file from causing a partial restore.
        for path, item, data in pending:
            if item['present']:
                atomic_write(path, data, item['mode'])
            elif path.exists():
                path.unlink()

    def public_status(self, checks=None):
        status = self.pki.status()
        meta = self.installed
        data = {'schema_version': 1, 'version': VERSION, 'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'installed': bool(meta), 'endpoint': {x: meta.get('plan', {}).get(x) for x in ('host', 'port', 'https')},
                'ca': status.get('ca'), 'server': status.get('server'), 'trust': status.get('trust', []),
                'rotation': status.get('rotation'), 'checks': checks or {}, 'revocation_enforced': False}
        data['ca_key_online'] = status.get('ca_key_online', False)
        write_json(self.state / 'public-status.json', data, 0o644)
        return data

    def transaction(self, action, plan=None, reload=True, remove=False):
        existing = self.installed
        if existing:
            self.assert_owned()
        plan = plan or existing.get('plan')
        if not plan:
            raise SafetyError('No entry plan')
        target = Path(plan['config_path'])
        self.env.nginx(['-t'])
        backup = self.backup(target)
        try:
            if plan.get('app'):
                apps = read_json(self.state / 'protected-apps.json', [])
                if not isinstance(apps, list) or any(not isinstance(x, str) or not Path(x).is_absolute() for x in apps):
                    raise SafetyError('Invalid protected application registry')
                write_json(self.state / 'protected-apps.json', sorted(set([*apps, plan['app']])))
            action()
            checks = {}
            if reload:
                if remove:
                    target.unlink()
                    if (self.state / 'installation.json').exists():
                        (self.state / 'installation.json').unlink()
                else:
                    if plan['https']:
                        self.pki.openssl.verify(self.pki.leaf_dir() / 'server.pem', self.pki.ca_dir() / 'ca.pem', plan['host'])
                    text = render(plan, self.pki.leaf_dir() / 'server.pem' if plan['https'] else None,
                                  self.pki.leaf_dir() / 'server.key.pem' if plan['https'] else None)
                    if existing and plan == existing['plan'] and target.read_text() == text and self.pki.meta == read_json(backup / 'pki_state', {}):
                        return {'changed': False, 'backup': str(backup), 'status': self.public_status()}
                    atomic_write(target, text, 0o644)
                    write_json(self.state / 'installation.json', {'version': VERSION, 'ownership': plan['ownership'],
                               'config_path': str(target), 'config_sha256': sha256(text), 'plan': plan})
                self.env.nginx(['-t'])
                self.env.refresh()
                if not remove and str(target) not in self.env.files:
                    raise SafetyError('Generated independent configuration is not loaded')
                self.env.nginx(['-s', 'reload'])
                if not remove:
                    checks = probe_after_reload(plan, self.pki)
            result = {'changed': True, 'backup': str(backup), 'status': self.public_status(checks)}
            # Only a committed change advances the rollback pointer. Read-only
            # manual backups, idempotent runs and failed changes preserve it.
            atomic_write(self.state / 'last-backup', backup.name + '\n')
            return result
        except Exception as original:
            try:
                self.restore(backup)
                self.env.nginx(['-t'])
                if reload:
                    self.env.nginx(['-s', 'reload'])
                self.env.refresh()
            except Exception as restore_error:
                write_json(backup / 'RECOVERY_REQUIRED.json', {'error': type(original).__name__,
                           'restore_error': type(restore_error).__name__, 'config_path': str(target)})
                raise SafetyError('Automatic rollback failed; recovery evidence preserved in ' + str(backup)) from original
            raise SafetyError('Change failed; previous configuration restored. Evidence: ' + str(backup)) from original

    def rollback(self):
        current = self.assert_owned() if self.installed else None
        identity = (self.state / 'last-backup').read_text().strip()
        if not re.fullmatch(r'[0-9]{8}T[0-9]{6}-[a-f0-9]{8}', identity):
            raise SafetyError('Invalid backup identity')
        previous = self.state / 'backups' / identity
        manifest = read_json(previous / 'manifest.json', {})
        previous_install = read_json(previous / 'installation', {})
        if not current:
            if not previous_install or not manifest.get('files', {}).get('nginx_config', {}).get('present'):
                raise SafetyError('No owned configuration in last backup to restore')
            if Path(previous_install['config_path']).exists():
                raise SafetyError('Refusing to overwrite a configuration created after uninstall')
            current = previous_install
        if manifest.get('config_path') != current['config_path']:
            raise SafetyError('Backup configuration path differs from owned entry')
        rescue = self.backup(current['config_path'])
        try:
            self.restore(previous)
            self.env.nginx(['-t'])
            self.env.refresh()
            self.env.nginx(['-s', 'reload'])
            if self.installed:
                probe_after_reload(self.installed['plan'], self.pki)
            result = {'restored': str(previous), 'rescue': str(rescue), 'status': self.public_status()}
            atomic_write(self.state / 'last-backup', rescue.name + '\n')
            return result
        except Exception as exc:
            try:
                self.restore(rescue)
                self.env.nginx(['-t'])
                self.env.nginx(['-s', 'reload'])
                if self.installed:
                    probe_after_reload(self.installed['plan'], self.pki)
            except Exception as restore_error:
                write_json(rescue / 'RECOVERY_REQUIRED.json', {'error': type(exc).__name__,
                           'restore_error': type(restore_error).__name__, 'config_path': current['config_path']})
                raise SafetyError('Rollback and rescue failed; recovery evidence preserved in ' + str(rescue)) from exc
            raise SafetyError('Rollback failed; current entry restored. Evidence: ' + str(rescue)) from exc


def confirmation(expected, provided=None):
    if provided is None and expected.startswith('INSTALL '):
        while True:
            try:
                answer = input('请核对上方计划，完整输入 "' + expected + '"（CANCEL 取消）：').strip()
            except (EOFError, KeyboardInterrupt):
                raise SafetyError('已取消入口确认，未进入证书或 Nginx 变更事务') from None
            if answer.upper() == 'CANCEL':
                raise SafetyError('已取消入口确认，未进入证书或 Nginx 变更事务')
            if answer == expected:
                return
            print('输入不匹配，请包含完整地址和端口：' + expected)
    answer = provided if provided is not None else input('Type exactly "' + expected + '": ').strip()
    if answer != expected:
        raise SafetyError('Explicit operation confirmation did not match')


def passphrase(new=False):
    value = getpass.getpass('Encrypted CA passphrase (never logged): ')
    if new and value != getpass.getpass('Repeat new CA passphrase: '):
        raise SafetyError('Passphrase confirmation differs')
    return value


def parser():
    result = argparse.ArgumentParser(description='NodeEndpoint V1.0 SSH-only Root manager')
    result.add_argument('--state', default='/var/lib/node-endpoint')
    result.add_argument('--nginx', help='Must match running master executable')
    result.add_argument('--openssl', default='openssl')
    sub = result.add_subparsers(dest='action', required=True)
    for name in ('detect', 'status', 'check-expiry', 'backup', 'rollback', 'menu'):
        sub.add_parser(name)
    for name in ('install', 'modify'):
        item = sub.add_parser(name)
        item.add_argument('--host', required=True)
        item.add_argument('--port', type=int, default=8443)
        item.add_argument('--include-dir', required=True)
        item.add_argument('--site-config', required=True)
        item.add_argument('--site-name', required=True)
        item.add_argument('--app', required=True)
        item.add_argument('--app-user')
        item.add_argument('--php')
        item.add_argument('--deployment', choices=('fpm', 'octane', 'docker'), required=True)
        item.add_argument('--backend', required=True)
        item.add_argument('--container')
        item.add_argument('--container-app')
        item.add_argument('--container-user')
        item.add_argument('--enable-ws', action='store_true')
        item.add_argument('--ws-backend')
        item.add_argument('--http', action='store_true')
        item.add_argument('--confirm-http', choices=('TOKEN_AND_CONTENT_SENT_IN_PLAINTEXT',))
        item.add_argument('--ca-key')
        item.add_argument('--manual-ca-passphrase', action='store_true', help='Use a manually entered CA passphrase instead of panel-managed credentials')
        item.add_argument('--confirm')
    item = sub.add_parser('renew')
    item.add_argument('--ca-key')
    item.add_argument('--emergency', action='store_true', help='New server key under same CA; no Node re-trust needed')
    item.add_argument('--confirm')
    for name in ('export-ca', 'verify-export', 'remove-online-ca-key'):
        item = sub.add_parser(name)
        item.add_argument('--destination', required=True)
        item.add_argument('--ca-id')
        if name != 'export-ca':
            item.add_argument('--fingerprint', required=True)
        item.add_argument('--confirm')
    item = sub.add_parser('rotate-prepare')
    item.add_argument('--manual-ca-passphrase', action='store_true')
    for name in ('rotate-activate', 'rotate-finish'):
        item = sub.add_parser(name)
        item.add_argument('--fingerprint', required=True)
        item.add_argument('--confirm')
    item = sub.add_parser('uninstall')
    item.add_argument('--confirm')
    return result


def menu(args):
    choices = [('只读环境检测', 'detect'), ('服务和证书状态', 'status'), ('到期检查', 'check-expiry'),
               ('备份独立入口', 'backup'), ('回滚最近变更', 'rollback'), ('重新签发 / 紧急替换服务器私钥', 'renew'),
               ('安装 / 修改入口（保留当前设置作为默认）', 'install'), ('卸载独立入口', 'uninstall'),
               ('导出加密 CA 到离线介质', 'export-ca'), ('验证离线 CA 导出', 'verify-export'),
               ('验证后删除在线 CA 私钥', 'remove-online-ca-key'), ('准备新 CA / 双信任', 'rotate-prepare'),
               ('确认全部节点双信任后切换新 CA', 'rotate-activate'), ('确认全部节点新 CA 后结束轮换', 'rotate-finish'),
               ('退出', None)]
    def prompt(label, default=''):
        answer = input(label + (' [' + str(default) + ']' if default else '') + ': ').strip()
        return answer or str(default or '')
    def yes(label, default):
        answer = input(label + (' [Y/n]: ' if default else ' [y/N]: ')).strip().lower()
        return default if not answer else answer in ('y', 'yes', '1', '是')
    while True:
        print('\nNodeEndpoint 1.0.0 — 原站点保持独立；Root 仅通过 SSH 操作')
        for index, (label, _) in enumerate(choices, 1):
            print(f'{index}. {label}')
        value = input('选择: ').strip()
        if not value.isdigit() or not 1 <= int(value) <= len(choices):
            continue
        action = choices[int(value) - 1][1]
        if action is None:
            return
        current = read_json(Path(args.state) / 'installation.json', {}).get('plan', {})
        if action == 'install' and current:
            action = 'modify'
        argv = ['--state', args.state, '--openssl', args.openssl]
        if args.nginx:
            argv += ['--nginx', args.nginx]
        argv += [action]
        if action in ('install', 'modify'):
            for flag in ('host', 'include-dir', 'site-config', 'site-name', 'app', 'app-user', 'deployment', 'backend'):
                field = {'site-config': 'source_site'}.get(flag, flag.replace('-', '_'))
                entered = prompt(flag, current.get(field, ''))
                if entered:
                    argv += ['--' + flag, entered]
            argv += ['--port', prompt('port', current.get('port', 8443))]
            if not yes('使用 HTTPS', current.get('https', True)):
                print('HTTP 会使 Token 和通信内容可能明文传输。')
                argv += ['--http', '--confirm-http', prompt('输入 TOKEN_AND_CONTENT_SENT_IN_PLAINTEXT 明确确认风险')]
            if yes('启用 WebSocket（需确认实际 /ws 后端）', bool(current.get('ws_backend'))):
                argv += ['--enable-ws', '--ws-backend', prompt('ws-backend', current.get('ws_backend', ''))]
            if '--deployment' in argv and argv[argv.index('--deployment') + 1] == 'docker':
                for flag in ('container', 'container-app', 'container-user'):
                    argv += ['--' + flag, prompt(flag, current.get(flag.replace('-', '_'), ''))]
            if current.get('php'):
                argv += ['--php', prompt('php', current['php'])]
            pki = PKI(Path(args.state), args.openssl)
            if pki.meta and not (pki.ca_dir() / 'ca.key.pem').exists():
                entered = prompt('离线加密 CA key 路径（地址不变无需重签，可留空）')
                if entered:
                    argv += ['--ca-key', entered]
        elif action == 'renew':
            pki = PKI(Path(args.state), args.openssl)
            if pki.meta and not (pki.ca_dir() / 'ca.key.pem').exists():
                argv += ['--ca-key', prompt('离线加密 CA key 路径（必填）')]
            if yes('服务器私钥泄露紧急换钥（不会吊销旧证书）', False):
                argv += ['--emergency']
        elif action in ('export-ca', 'verify-export', 'remove-online-ca-key'):
            argv += ['--destination', prompt('离线导出目录')]
            identity = prompt('CA identity（留空使用当前CA）')
            if identity:
                argv += ['--ca-id', identity]
            if action != 'export-ca':
                argv += ['--fingerprint', prompt('独立核对的完整 CA SHA-256 指纹')]
        elif action in ('rotate-activate', 'rotate-finish'):
            argv += ['--fingerprint', prompt('独立核对的新 CA SHA-256 指纹')]
        try:
            execute(parser().parse_args(argv))
        except (SafetyError, OSError, ValueError) as exc:
            print('拒绝/失败:', exc, file=sys.stderr)


def execute(args):
    if args.action == 'menu':
        return menu(args)
    if args.action == 'detect':
        print(json.dumps(Environment(args.nginx).report(), ensure_ascii=False, indent=2))
        return
    linux_root()
    args.openssl = str(trusted_file(shutil.which(args.openssl) or args.openssl, True))
    state = safe_state(args.state)
    if args.action in ('install', 'modify'):
        application = Path(args.app).resolve()
        if state == application or application in state.parents:
            raise SafetyError('PKI/state must be outside the entire Xboard application tree')
    state = initialize_state(state)
    # Root-only exclusive lock; lock content never includes secrets.
    import fcntl
    fd = os.open(state / 'manager.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SafetyError('Another Root manager operation is in progress')
    try:
        # Certificate-only maintenance remains available if Nginx is temporarily down.
        pki = PKI(state, args.openssl)
        if args.action in ('export-ca', 'verify-export', 'remove-online-ca-key'):
            assert_export_destination(state, args.destination)
            from panel_passphrase import with_password
            def maintain(password):
                if args.action == 'export-ca':
                    return pki.export_ca(args.destination, password, args.ca_id)
                if args.action == 'verify-export':
                    pki.verify_export(args.destination, password, args.fingerprint, args.ca_id)
                    return {'verified': True}
                confirmation('REMOVE ONLINE CA KEY ' + args.fingerprint, args.confirm)
                pki.remove_online_key(args.destination, password, args.fingerprint, args.ca_id)
                return {'online_key_removed': True, 'notice': 'Verify offline storage separately; SSD/COW erasure cannot be guaranteed.'}
            output = with_password(pki, maintain, passphrase, ca_id=args.ca_id)
        elif args.action in ('status', 'check-expiry'):
            manager = Manager(state, None, args.openssl)
            checks = {}
            if manager.installed:
                try:
                    manager.env = Environment(args.nginx)
                    manager.assert_owned()
                    if manager.installed['config_path'] not in manager.env.files:
                        raise SafetyError('Owned independent entry is not loaded')
                    checks = local_probe(manager.installed['plan'], pki)
                    checks['nginx'] = 'loaded'
                except (SafetyError, OSError, ssl.SSLError, http.client.HTTPException) as exc:
                    checks = {'nginx': 'unverified_or_unavailable', 'service': 'failed', 'error_type': type(exc).__name__}
            output = manager.public_status(checks)
            if args.action == 'check-expiry':
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 2 if any(x and (x['status'] != 'valid' or x['reminder_days']) for x in (output['ca'], output['server'])) else 0
        else:
            env = Environment(args.nginx)
            manager = Manager(state, env, args.openssl)
            if args.action in ('install', 'modify'):
                if args.http and args.confirm_http != 'TOKEN_AND_CONTENT_SENT_IN_PLAINTEXT':
                    raise SafetyError('HTTP requires explicit --confirm-http TOKEN_AND_CONTENT_SENT_IN_PLAINTEXT; never a TLS fallback')
                if args.action == 'modify' and not manager.installed:
                    raise SafetyError('No installed entry to modify')
                plan = env.plan(args, state, manager.installed)
                print(json.dumps({'reviewable_plan': plan}, ensure_ascii=False, indent=2))
                confirmation('INSTALL ' + plan['host'] + ':' + str(plan['port']), args.confirm)
                def apply():
                    if plan['https']:
                        if not pki.meta:
                            if args.manual_ca_passphrase:
                                pki.create(plan['host'], passphrase(True))
                            else:
                                from panel_passphrase import ManagedPassphrases
                                ManagedPassphrases(state, plan).create(pki, plan['host'])
                        elif pki.meta['host'] != plan['host']:
                            from panel_passphrase import with_password
                            with_password(pki, lambda password: pki.renew(plan['host'], password, args.ca_key),
                                          passphrase, plan=plan, ca_key=args.ca_key, manual=args.manual_ca_passphrase)
                        else:
                            from panel_passphrase import existing_password
                            existing_password(pki, plan, ca_key=args.ca_key)
                output = manager.transaction(apply, plan)
            elif args.action == 'backup':
                manager.assert_owned()
                output = {'backup': str(manager.backup())}
            elif args.action == 'rollback':
                confirmation('ROLLBACK NODE ENDPOINT')
                output = manager.rollback()
            elif args.action == 'renew':
                meta = manager.assert_owned()
                confirmation('REPLACE SERVER KEY ' + meta['plan']['host'], args.confirm)
                from panel_passphrase import with_password
                output = manager.transaction(lambda: with_password(pki,
                    lambda password: pki.renew(meta['plan']['host'], password, args.ca_key),
                    passphrase, plan=meta['plan'], ca_key=args.ca_key))
            elif args.action == 'rotate-prepare':
                meta = manager.assert_owned()
                if args.manual_ca_passphrase:
                    output = manager.transaction(lambda: pki.prepare_rotation(passphrase(True)), reload=False)
                else:
                    from panel_passphrase import ManagedPassphrases
                    output = manager.transaction(lambda: ManagedPassphrases(state, meta['plan']).prepare_rotation(pki), reload=False)
            elif args.action == 'rotate-activate':
                confirmation('ALL NODES TRUST BOTH CA ' + args.fingerprint, args.confirm)
                output = manager.transaction(lambda: pki.activate_rotation(args.fingerprint))
            elif args.action == 'rotate-finish':
                confirmation('ALL NODES USE NEW CA ' + args.fingerprint, args.confirm)
                output = manager.transaction(lambda: pki.finish_rotation(args.fingerprint), reload=False)
            elif args.action == 'uninstall':
                manager.assert_owned()
                confirmation('UNINSTALL NODE ENDPOINT ALL NODES MIGRATED', args.confirm)
                output = manager.transaction(lambda: None, remove=True)
            else:
                raise SafetyError('Unsupported action')
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == '__main__':
    try:
        sys.exit(execute(parser().parse_args()) or 0)
    except (SafetyError, OSError, ValueError, KeyError) as exc:
        print('NodeEndpoint refused/failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
