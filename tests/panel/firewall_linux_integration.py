#!/usr/bin/env python3
"""Real UFW checks inside private Linux mount/network namespaces.

Run as Root on the disposable test VM. The outer process only reads host UFW
configuration/rules and creates a private /opt fixture. The child bind-mounts
copies of /etc/ufw and /etc/default/ufw and uses a private /run before invoking
UFW. No host service is enabled, disabled, reloaded, flushed or saved.
Firewalld is deliberately refused: its shared D-Bus daemon would escape this
network namespace. This fixture does not test external network reachability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
SYSTEM_PATH = '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin'
PREFIX = 'node-endpoint-firewall-integration-'
MARKER = '.fixture-owner.json'
PORTS = (8443, 18443)


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, log=None):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=35, env={'PATH': SYSTEM_PATH, 'LC_ALL': 'C', 'LANG': 'C'})
    if log is not None:
        with log.open('ab') as stream:
            stream.write((json.dumps([str(arg) for arg in argv]) + '\n').encode())
            stream.write(result.stdout + result.stderr + b'\n')
        log.chmod(0o600)
    require(result.returncode == 0, Path(argv[0]).name + ' failed (diagnostics retained privately)')
    return result.stdout.decode('utf-8', errors='strict')


def safe_root(path, private=False):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), 'Unsafe fixture/configuration path')
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022, 'Path must be Root-owned without writable ancestors')
    if private:
        require(path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700,
                'Fixture directory must be private 0700')
    return path


def file_snapshot(paths):
    """Bytes and metadata, excluding read-sensitive access times."""
    result = {}
    for root in paths:
        root = Path(root)
        safe_root(root)
        for path in [root, *sorted(root.rglob('*'))] if root.is_dir() else [root]:
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                    and not info.st_mode & 0o022, 'Unsafe UFW fixture/configuration member')
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'Special configuration file refused')
            result[str(path)] = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                                 'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns,
                                 'sha256': digest(path.read_bytes()) if path.is_file() else None}
    return result


def rules():
    result = {}
    for family, binary in ((4, 'iptables-save'), (6, 'ip6tables-save')):
        text = run([shutil.which(binary, path=SYSTEM_PATH)])
        # Packet counters and generation timestamps can legitimately change as
        # the host continues serving SSH/traffic; actual rules must not change.
        result[str(family)] = '\n'.join(re.sub(r'\[\d+:\d+\]', '[0:0]', line)
                                       for line in text.splitlines() if not line.startswith('#'))
    return result


def without_test_rules(value):
    return {family: '\n'.join(line for line in text.splitlines()
                             if not re.search(r'--dport (?:8443|18443)(?:\s|$)', line))
            for family, text in value.items()}


def assert_rule(value, port):
    for family, text in value.items():
        matched = [line for line in text.splitlines()
                   if re.search(r'--dport ' + str(port) + r'(?:\s|$)', line)]
        chain = 'ufw6-user-input' if family == '6' else 'ufw-user-input'
        require(len(matched) == 1 and '-A ' + chain + ' ' in matched[0]
                and '-p tcp ' in matched[0] and '-j ACCEPT' in matched[0],
                'Expected exactly one incoming TCP allow in IPv' + family)


def write(path, value):
    path.write_text(value, encoding='utf-8')
    path.chmod(0o600)


def validate_fixture(path):
    path = safe_root(path, private=True)
    require(path.resolve() == path and path.parent == Path('/opt') and path.name.startswith(PREFIX),
            'Fixture is outside its dedicated /opt directory')
    marker = path / MARKER
    safe_root(marker)
    data = json.loads(marker.read_text())
    require(data.get('tool') == 'NodeEndpoint firewall namespace integration'
            and data.get('path') == str(path) and re.fullmatch('[a-f0-9]{32}', data.get('token', '')),
            'Fixture ownership marker differs')
    return path


def namespace_checks(args):
    require(type(args.parent_net) is int and args.parent_net > 0
            and type(args.parent_mount) is int and args.parent_mount > 0,
            'Namespace child must be launched by this fixture parent')
    require(os.stat('/proc/self/ns/net').st_ino != args.parent_net,
            'Refusing UFW mutation in the parent network namespace')
    require(os.stat('/proc/self/ns/mnt').st_ino != args.parent_mount,
            'Refusing mounts in the parent mount namespace')
    require(os.stat('/proc/self/ns/net').st_ino != os.stat('/proc/1/ns/net').st_ino
            and os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino,
            'Refusing mutation in the VM init process namespaces')
    work = validate_fixture(Path(args.namespace_child))
    require(shutil.which('firewall-cmd', path=SYSTEM_PATH) is None,
            'Firewalld daemon access is outside the isolated UFW fixture scope')
    log = work / 'commands.private.log'
    run(['mount', '--make-rprivate', '/'], log)
    run(['mount', '--bind', str(work / 'ufw'), '/etc/ufw'], log)
    run(['mount', '--bind', str(work / 'default-ufw'), '/etc/default/ufw'], log)
    # Keep xtables/UFW run-time locks off the host filesystem as well.
    run(['mount', '-t', 'tmpfs', '-o', 'mode=755,nosuid,nodev', 'tmpfs', '/run'], log)
    Path('/run/lock').mkdir(mode=0o755)
    require(os.path.samefile(work / 'ufw', '/etc/ufw')
            and os.path.samefile(work / 'default-ufw', '/etc/default/ufw'), 'Private configuration bind failed')
    ufw = shutil.which('ufw', path=SYSTEM_PATH)
    run([ufw, '--force', 'reset'], log)
    run([ufw, 'default', 'deny', 'incoming'], log)
    run([ufw, 'default', 'allow', 'outgoing'], log)
    run([ufw, 'allow', '22/tcp', 'comment', 'Fixture SSH preservation'], log)
    run([ufw, 'allow', '18444/tcp', 'comment', 'Fixture existing user rule'], log)
    run([ufw, '--force', 'enable'], log)
    require(run([ufw, 'status'], log).startswith('Status: active'), 'Isolated UFW did not become active')
    before = rules()
    defaults = Path('/etc/default/ufw').read_bytes()
    enabled = Path('/etc/ufw/ufw.conf').read_bytes()
    from firewall import ensure_port
    first = ensure_port(PORTS[0])
    require(first.get('status') == 'ensured' and first.get('manager') == 'ufw',
            'Real active UFW ensure failed: ' + json.dumps(first, sort_keys=True))
    once = rules()
    assert_rule(once, PORTS[0])
    saved_once = [Path('/etc/ufw/' + name).read_bytes() for name in ('user.rules', 'user6.rules')]
    second = ensure_port(PORTS[0])
    require(second.get('status') == 'ensured' and second.get('changed') is False, 'Duplicate ensure was not idempotent')
    require(rules() == once and saved_once == [Path('/etc/ufw/' + name).read_bytes()
                                             for name in ('user.rules', 'user6.rules')], 'Duplicate ensure changed rules')
    require(without_test_rules(once) == before, 'Existing SSH/user rules or default policies changed')

    import manager
    import quick_install
    state = work / 'owned-state'
    state.mkdir(mode=0o700)
    pki = state / 'pki'
    pki.mkdir(mode=0o700)
    write(pki / 'state.json', '{"fixture":"must remain untouched"}\n')
    write(pki / 'ca.key.pem', 'isolated canary; no cryptographic key material\n')
    write(pki / 'trust-bundle.pem', 'isolated canary; no certificate material\n')
    config = work / 'node-endpoint.conf'
    ownership = 'a' * 32
    write(config, '# NodeEndpoint ownership ' + ownership + '\nserver { listen 0.0.0.0:18443 ssl; '
          'listen [::]:18443 ssl; }\n')
    write(state / manager.STATE_MARKER, json.dumps({'tool': 'NodeEndpoint', 'schema_version': 1,
          'path': str(state), 'ownership': 'b' * 32}))
    write(state / 'manager.lock', '')
    plan = {'ownership': ownership, 'config_path': str(config), 'port': PORTS[1],
            'host': '192.0.2.10', 'https': True}
    installed = {'ownership': ownership, 'config_path': str(config),
                 'config_sha256': digest(config.read_bytes()), 'plan': plan}
    write(state / 'installation.json', json.dumps(installed))
    pki_before = file_snapshot([pki, config])
    configured = quick_install.configure_firewall(state)
    require(configured.get('status') == 'ensured' and configured.get('port') == PORTS[1],
            'Configured firewall did not use the committed custom port')
    configured_rules = rules()
    for port in PORTS:
        assert_rule(configured_rules, port)
    require(without_test_rules(configured_rules) == before, 'Custom port changed unrelated rules or policies')
    require(file_snapshot([pki, config]) == pki_before, 'Firewall-only operation touched PKI or Nginx configuration')
    plan['port'] = 18445
    write(state / 'installation.json', json.dumps(installed))
    try:
        quick_install.configure_firewall(state)
    except ValueError:
        pass
    else:
        raise RuntimeError('Tampered plan port was not refused')
    require(rules() == configured_rules and file_snapshot([pki, config]) == pki_before,
            'Rejected tampered state changed rules, PKI or configuration')
    run([ufw, 'reload'], log)
    require(rules() == configured_rules, 'UFW reload did not preserve all rules')
    require(Path('/etc/default/ufw').read_bytes() == defaults
            and Path('/etc/ufw/ufw.conf').read_bytes() == enabled, 'Default policies or enabled state changed')
    return {'result': 'PASS', 'checks': [
        'separate mount and network namespaces', 'active UFW IPv4/IPv6 TCP allow',
        'repeat ensure leaves one persistent rule per IP family', 'SSH/user rules and default policies preserved',
        'firewall-only uses owned custom port 18443', 'firewall-only leaves PKI and Nginx bytes/metadata unchanged',
        'tampered installed port rejected before firewall mutation', 'UFW reload preserves rules'],
        'network_reachability': 'not_tested', 'firewalld': 'not_tested'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace-child', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--parent-net', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--parent-mount', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Requires Linux Root on the disposable test VM')
    os.umask(0o077)
    sys.dont_write_bytecode = True
    for name in ('unshare', 'mount', 'ufw', 'iptables-save', 'ip6tables-save'):
        require(shutil.which(name, path=SYSTEM_PATH), 'Missing test dependency: ' + name)
    if args.namespace_child:
        print(json.dumps(namespace_checks(args), ensure_ascii=False))
        return
    require(shutil.which('firewall-cmd', path=SYSTEM_PATH) is None,
            'This real UFW fixture refuses hosts with firewall-cmd installed')
    safe_root(Path('/opt'))
    host_files = [Path('/etc/ufw'), Path('/etc/default/ufw')]
    before_files, before_rules = file_snapshot(host_files), rules()
    work = Path(tempfile.mkdtemp(prefix=PREFIX, dir='/opt')).resolve()
    identity = (work.stat().st_dev, work.stat().st_ino)
    token = secrets.token_hex(16)
    write(work / MARKER, json.dumps({'tool': 'NodeEndpoint firewall namespace integration',
                                   'path': str(work), 'token': token}))
    shutil.copytree('/etc/ufw', work / 'ufw')
    shutil.copy2('/etc/default/ufw', work / 'default-ufw')
    # Never execute copied administrator hooks. Extra module loading and sysctl
    # writes are irrelevant to this port rule fixture and are disabled in copies.
    for hook in ('before.init', 'after.init'):
        write(work / 'ufw' / hook, '#!/bin/sh\nexit 0\n')
        (work / 'ufw' / hook).chmod(0o700)
    defaults = (work / 'default-ufw').read_text()
    for name, value in (('IPT_MODULES', '""'), ('IPT_SYSCTL', '""'), ('IPV6', 'yes')):
        defaults, count = re.subn(r'^' + name + r'=.*$', name + '=' + value, defaults, flags=re.MULTILINE)
        if count == 0:
            defaults += '\n' + name + '=' + value + '\n'
    write(work / 'default-ufw', defaults)
    command = ['unshare', '--mount', '--net', '--fork', '--kill-child=KILL',
               sys.executable, str(Path(__file__).resolve()), '--namespace-child', str(work),
               '--parent-net', str(os.stat('/proc/self/ns/net').st_ino),
               '--parent-mount', str(os.stat('/proc/self/ns/mnt').st_ino)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True,
                               env={'PATH': SYSTEM_PATH, 'LC_ALL': 'C', 'LANG': 'C'})
    try:
        stdout, stderr = process.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    write(work / 'child.stdout.private', stdout.decode('utf-8', errors='replace'))
    write(work / 'child.stderr.private', stderr.decode('utf-8', errors='replace'))
    require(file_snapshot(host_files) == before_files and rules() == before_rules,
            'Host firewall/configuration differs; fixture retained at ' + str(work))
    require(process.returncode == 0, 'Namespace fixture failed; private diagnostics retained at ' + str(work))
    report = json.loads(stdout)
    require(report.get('result') == 'PASS', 'Namespace report is incomplete')
    report['checks'].append('outer host UFW files and IPv4/IPv6 rules unchanged')
    report['host_rules_comparison'] = 'exact after excluding counters and generated timestamps'
    validate_fixture(work)
    require((work.stat().st_dev, work.stat().st_ino) == identity
            and json.loads((work / MARKER).read_text())['token'] == token, 'Cleanup ownership differs')
    shutil.rmtree(work)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'result': 'FAIL', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
