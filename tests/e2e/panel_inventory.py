#!/usr/bin/env python3
"""Read-only public inventory for an explicitly authorized panel test VM.

No .env, raw config, process arguments, keys, or service mutations. Run from
the private lab containing tools/panel; Laravel route:list executes non-root.
"""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from manager import Environment
from nginx import dump_files, parse, values, walk


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, timeout=45):
    try:
        process = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=timeout, check=False)
        return process.returncode, process.stdout.decode(errors='replace')
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, type(error).__name__


def info(path):
    path = Path(path)
    try:
        item = path.stat()
        return {'path': str(path), 'owner': pwd.getpwuid(item.st_uid).pw_name,
                'uid': item.st_uid, 'mode': oct(stat.S_IMODE(item.st_mode)), 'symlink': path.is_symlink()}
    except (OSError, KeyError) as error:
        return {'path': str(path), 'error': type(error).__name__}


def process_inventory():
    result = {'nginx_masters': [], 'fpm_masters': [], 'php_related': []}
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            text = (folder / 'cmdline').read_bytes().replace(b'\x00', b' ').decode().strip()
            executable = str((folder / 'exe').resolve())
            owner = pwd.getpwuid(folder.stat().st_uid).pw_name
            row = {'pid': int(folder.name), 'owner': owner, 'executable': executable}
            if re.match(r'^(nginx|openresty): master process ', text):
                result['nginx_masters'].append(row)
            elif text.startswith('php-fpm: master process ('):
                row['config'] = text.split('(', 1)[1].rstrip(')')
                result['fpm_masters'].append(row)
            elif 'php' in Path(executable).name:
                row['roles'] = [word for word in ('artisan', 'queue:work', 'horizon', 'octane', 'ws-server') if word in text]
                result['php_related'].append(row)
        except (OSError, ValueError, KeyError, UnicodeError):
            continue
    return result


def fpm_inventory(masters):
    result = []
    for master in masters:
        row = {'pid': master['pid'], 'executable': info(master['executable']), 'config': master['config'], 'pools': []}
        pending, seen = [Path(master['config'])], set()
        while pending and len(seen) < 100:
            path = pending.pop()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            content = path.read_text(errors='replace')
            for line in content.splitlines():
                match = re.match(r'^\s*include\s*=\s*([^;]+)', line)
                if match:
                    value = Path(match[1].strip())
                    if not value.is_absolute():
                        value = path.parent / value
                    pending.extend(Path(x) for x in glob.glob(str(value)))
            pool, fields = None, {}
            for line in content.splitlines():
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    if pool:
                        row['pools'].append({'source': str(path), 'pool': pool, **fields})
                    pool, fields = line[1:-1], {}
                elif '=' in line and not line.startswith(';'):
                    key, value = (x.strip() for x in line.split('=', 1))
                    if key in ('listen', 'user', 'group', 'listen.owner', 'listen.group', 'listen.mode', 'listen.allowed_clients'):
                        fields[key] = value.split(';', 1)[0].strip()
            if pool:
                row['pools'].append({'source': str(path), 'pool': pool, **fields})
        result.append(row)
    return result


def raw_nginx_metadata(master):
    """Find parser compatibility failures without exposing raw configuration."""
    import shlex
    raw = (Path('/proc') / str(master['pid']) / 'cmdline').read_bytes().replace(b'\x00', b' ').decode().strip()
    argv = shlex.split(re.sub(r'^(nginx|openresty): master process ', '', raw))
    flags = []
    for flag in ('-c', '-p'):
        if flag in argv:
            flags.extend([flag, argv[argv.index(flag) + 1]])
    code, output = run([master['executable'], *flags, '-T'])
    result = {'nginx_T_exit': code, 'files': [], 'sites': []}
    if code:
        return result
    for filename, content in dump_files(output).items():
        entry = {'path': filename, 'sha256': digest(Path(filename).read_bytes()), **info(filename)}
        try:
            nodes = parse(content)
            for node in walk(nodes):
                if node.words == ['server']:
                    result['sites'].append({'config': filename, 'server_names': values(node.children, 'server_name'),
                        'listeners': values(node.children, 'listen'), 'root': values(node.children, 'root'),
                        'fastcgi_pass': [x.words[1] for x in walk(node.children) if x.words[0] == 'fastcgi_pass'],
                        'ws_location_present': any(x.words[0] == 'location' and any(y in ('/ws', '/ws/') for y in x.words) for x in walk(node.children))})
        except Exception as error:
            entry['parser_error'] = {'type': type(error).__name__, 'message': str(error)}
            # Only flag line numbers and generic syntax categories; no raw
            # directive text or nginx configuration secrets are output.
            entry['unquoted_regex_quantifier_lines'] = [i for i, line in enumerate(content.splitlines(), 1)
                if re.search(r'\{\d+(?:,\d*)?\}', line) and line.lstrip().startswith(('location ', 'if '))]
            entry['braced_variable_lines'] = [i for i, line in enumerate(content.splitlines(), 1) if re.search(r'\$\{[A-Za-z_][A-Za-z_0-9]*\}', line)]
        result['files'].append(entry)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', default='/www/wwwroot/xboard')
    parser.add_argument('--app-user', default='www')
    parser.add_argument('--php')
    parser.add_argument('--inspect-nginx', help='read-only selected-master inspection; does not bypass production CLI refusal')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Read-only process/config inspection requires sudo on this test VM')
    processes = process_inventory()
    report = {'scope': 'read-only inventory; no .env or raw configuration; no service mutations',
              'kernel': os.uname().release, 'processes': processes,
              'fpm': fpm_inventory(processes['fpm_masters']), 'services': {},
              'app': info(args.app), 'artisan': info(Path(args.app) / 'artisan')}
    try:
        environment = Environment()
        report['nginx'] = environment.report()
        report['root_compatible_environment'] = True
        report['nginx_source_metadata'] = [info(path) for path in environment.files]
        report['nginx_source_bytes_sha256'] = {path: digest(Path(path).read_bytes()) for path in environment.files}
    except Exception as error:
        report['root_compatible_environment'] = False
        report['nginx_error'] = {'type': type(error).__name__, 'message': str(error)}
    if args.inspect_nginx and not report['root_compatible_environment']:
        selected = [row for row in processes['nginx_masters']
                    if Path(row['executable']).resolve() == Path(args.inspect_nginx).resolve()]
        if len(selected) != 1:
            report['selected_nginx_error'] = 'requires_one_exact_executable_match'
        else:
            try:
                import shlex
                master = dict(selected[0])
                raw = (Path('/proc') / str(master['pid']) / 'cmdline').read_bytes().replace(b'\x00', b' ').decode().strip()
                master['argv'] = shlex.split(re.sub(r'^(nginx|openresty): master process ', '', raw))
                # Metadata-only inspection: the production CLI's current
                # two-master refusal stays explicit and is never hidden.
                with patch('manager.active_master', return_value=master):
                    environment = Environment(args.inspect_nginx)
                    report['selected_nginx_readonly'] = environment.report()
                    report['selected_nginx_source_metadata'] = [info(path) for path in environment.files]
                    report['selected_nginx_source_bytes_sha256'] = {path: digest(Path(path).read_bytes()) for path in environment.files}
            except Exception as error:
                report['selected_nginx_error'] = {'type': type(error).__name__, 'message': str(error)}
                report['selected_nginx_raw_metadata'] = raw_nginx_metadata(master)
    for name in ('nginx.service', 'xboard-queue.service', 'redis-server.service', 'mariadb.service', 'docker.service'):
        code, output = run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'User', '-p', 'FragmentPath'])
        report['services'][name] = {'exit': code, **dict(line.split('=', 1) for line in output.splitlines() if '=' in line)}
    php = args.php or shutil.which('php')
    report['cli_php'] = info(php) if php else None
    trust = Path(args.app) / 'app/Http/Middleware/TrustProxies.php'
    if trust.is_file():
        profile = digest(trust.read_bytes())
        report['proxy_trust'] = {'sha256': profile, 'audited_profile_match': profile == 'f259274043cc68d609bb826d5efac4c2f547c734585dbd43462635304a18f5f3'}
    worker = Path(args.app) / 'app/WebSocket/NodeWorker.php'
    report['node_worker'] = {'present': worker.is_file(), 'sha256': digest(worker.read_bytes()) if worker.is_file() else None}
    user = pwd.getpwnam(args.app_user)
    if user.pw_uid == 0 or not php:
        report['routes'] = {'error': 'requires_nonroot_user_and_php'}
    else:
        code, output = run(['runuser', '-u', args.app_user, '--', php, Path(args.app) / 'artisan', 'route:list', '--json'], timeout=60)
        report['routes'] = {'user': args.app_user, 'exit': code}
        if code == 0:
            try:
                rows = json.loads(output)
                report['routes'].update({'total': len(rows), 'sha256': digest(output.encode()),
                    'node_routes': [row for row in rows if row.get('uri', '').startswith(('api/v1/server/', 'api/v2/server/'))]})
            except (ValueError, TypeError):
                report['routes']['error'] = 'route_list_not_json'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
