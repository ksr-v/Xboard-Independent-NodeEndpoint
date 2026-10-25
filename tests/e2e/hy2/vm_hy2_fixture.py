"""Owned HY2 port-hopping acceptance. No host firewall edits or private log reads."""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

BASE = Path('/opt/xboard-hy2-hop-20261025')
ARTIFACTS = BASE / 'attempt-2'
OWNER = {'tool': 'Codex isolated Hysteria2 hopping test', 'path': str(BASE), 'date': '2026-10-25'}
SERVER, CLIENT = 'codex-hy2-s-1025', 'codex-hy2-c-1025'
HELPER = BASE / 'wire_helpers.py'
NODE_SHA = '0f3aeda43036081c668826a87cf4c35d227ac8a0a4488ccebab2a4c74db1b78f'
CLIENT_SHA = '115aaf17049a259241fe7c0118f351f39f7d71fba2eff372b63dd06a5d24b86e'
REPORT = []
PROCESSES = []


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def write(path, data):
    path.write_bytes((json.dumps(data, indent=2) + '\n').encode())
    path.chmod(0o600)


def run(args, *, data=None, timeout=20):
    result = subprocess.run([str(x) for x in args], input=data, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'controlled-command-failed-' + Path(str(args[0])).name)
    return result.stdout


def ns(name, *args, **kwargs):
    require(name in [SERVER, CLIENT], 'unknown-namespace')
    return run(['ip', 'netns', 'exec', name, *args], **kwargs)


def owned():
    require(os.geteuid() == 0 and BASE.resolve() == BASE and not BASE.is_symlink(), 'owned-root-case-required')
    require(json.loads((BASE / 'owner.json').read_text()) == OWNER, 'case-owner-differs')


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def start(name, args, directory):
    directory.mkdir(exist_ok=True, mode=0o700)
    with (directory / 'stdout.private').open('wb') as output:
        process = subprocess.Popen(['ip', 'netns', 'exec', name, *map(str, args)],
                                   stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    PROCESSES.append(process)
    return process


def wait_file(path, process):
    for _ in range(100):
        require(process.poll() is None, 'fixture-child-exited-before-ready')
        if path.is_file():
            return
        time.sleep(.05)
    raise RuntimeError('fixture-readiness-timeout')


def prepare():
    require(os.geteuid() == 0 and not BASE.exists(), 'fresh-root-case-required')
    source = Path(__file__).resolve().parent
    require(source == Path('/tmp/codex-hy2-upload-20261025'), 'upload-path-differs')
    for name, expected in [('xboard-node-linux-amd64', NODE_SHA), ('hy2-client-linux-amd64', CLIENT_SHA)]:
        path = source / name
        require(path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                'binary-sha-differs-' + name)
    BASE.mkdir(mode=0o700)
    write(BASE / 'owner.json', OWNER)
    for name in ['xboard-node-linux-amd64', 'hy2-client-linux-amd64', 'wire_helpers.py', 'vm_hy2_fixture.py', 'panel-runtime.private.json']:
        path = source / name
        require(path.is_file() and not path.is_symlink(), 'source-file-differs')
        shutil.copyfile(path, BASE / name)
        (BASE / name).chmod(0o700 if name.endswith('amd64') else 0o600)
    data = json.loads((BASE / 'panel-runtime.private.json').read_text())
    require(data['node']['protocol'] == 'hysteria' and data['node']['version'] == 2 and data['node']['server_port'] == 42443, 'panel-node-config-differs')
    require(data['client_outbound']['server_ports'] == ['42450:42453'] and data['client_outbound']['hop_interval'] == '5s', 'panel-hopping-config-differs')
    require(data['client_outbound']['password'] == data['user']['uuid'], 'synthetic-identity-differs')
    # CA/key/CSR are newly generated for this case, never exported or printed.
    for prefix in ['ca', 'wrong-ca']:
        run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', BASE / (prefix + '.private.key')])
        run(['openssl', 'req', '-new', '-x509', '-key', BASE / (prefix + '.private.key'), '-out', BASE / (prefix + '.pem'),
             '-days', '2', '-subj', '/CN=Codex HY2 test ' + prefix, '-addext', 'basicConstraints=critical,CA:TRUE'])
    run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', BASE / 'server.private.key'])
    run(['openssl', 'req', '-new', '-key', BASE / 'server.private.key', '-out', BASE / 'server.private.csr', '-subj', '/CN=hy2.fixture.test'])
    (BASE / 'certificate.ext').write_text('subjectAltName=DNS:hy2.fixture.test,IP:10.213.25.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature\nbasicConstraints=CA:FALSE\n')
    run(['openssl', 'x509', '-req', '-in', BASE / 'server.private.csr', '-CA', BASE / 'ca.pem', '-CAkey', BASE / 'ca.private.key',
         '-CAcreateserial', '-out', BASE / 'server.pem', '-days', '2', '-extfile', BASE / 'certificate.ext'])
    write(BASE / 'network-state.json', {'namespaces': []})
    print(json.dumps({'result': 'PASS', 'phase': 'prepared', 'node_sha256': NODE_SHA, 'client_sha256': CLIENT_SHA}), flush=True)


def network():
    existing = {row['name'] for row in json.loads(run(['ip', '-j', 'netns', 'list']) or b'[]')}
    require(not existing.intersection({SERVER, CLIENT}), 'namespace-already-exists')
    links = {row['ifname'] for row in json.loads(run(['ip', '-j', 'link', 'show']))}
    require(not links.intersection({'hys-1025', 'hyc-1025'}), 'veth-already-exists')
    state = {'namespaces': []}
    for name in [SERVER, CLIENT]:
        run(['ip', 'netns', 'add', name])
        state['namespaces'].append(name)
        write(BASE / 'network-state.json', state)
    run(['ip', 'link', 'add', 'hys-1025', 'type', 'veth', 'peer', 'name', 'hyc-1025'])
    for name, interface, ip in [(SERVER, 'hys-1025', '10.213.25.1/30'), (CLIENT, 'hyc-1025', '10.213.25.2/30')]:
        run(['ip', 'link', 'set', interface, 'netns', name])
        ns(name, 'ip', 'addr', 'add', ip, 'dev', interface)
        ns(name, 'ip', 'link', 'set', interface, 'up')
        ns(name, 'ip', 'link', 'set', 'lo', 'up')
    require(ns(SERVER, 'nft', 'list', 'ruleset').strip() == b'', 'new-namespace-has-rules')


def clean_network():
    owned()
    state = json.loads((BASE / 'network-state.json').read_text())
    require(set(state['namespaces']).issubset({SERVER, CLIENT}), 'network-state-differs')
    for name in reversed(state['namespaces']):
        for pid in ns(name, 'ip', 'netns', 'pids', name).decode().split():
            # Only processes launched inside these newly created namespaces.
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        run(['ip', 'netns', 'del', name])
    write(BASE / 'network-state.json', {'namespaces': []})


def nat(enabled):
    if enabled:
        rules = ('table inet codex_hy2 {\n chain ingress {\n type nat hook prerouting priority dstnat; policy accept;\n '
                 'iifname "hys-1025" ip saddr 10.213.25.2 udp dport 42450-42453 counter redirect to :42443\n }\n}\n')
        check = subprocess.run(['ip', 'netns', 'exec', SERVER, 'nft', '-f', '-'], input=rules.encode(), capture_output=True)
        if check.returncode:
            # Only stderr for this fixed synthetic nft rule; never runtime logs.
            print(json.dumps({'fixture_nft_error': check.stderr.decode(errors='replace')}), flush=True)
        require(check.returncode == 0, 'fixture-nft-rule-rejected')
    else:
        ns(SERVER, 'nft', 'delete', 'table', 'inet', 'codex_hy2')


def client_case(kernel, label, outbound, duration=4, expected_failure=False, hop=False):
    directory = ARTIFACTS / kernel / label
    directory.mkdir(parents=True, mode=0o700)
    path = directory / 'client.private.json'
    write(path, outbound)
    ready, summary = directory / 'observer.ready.json', directory / 'wire-summary.json'
    observer = start(SERVER, ['python3', '-I', '-B', HELPER, 'observe', '--ready', ready, '--output', summary], directory / 'observer')
    wait_file(ready, observer)
    timeout = 10 if expected_failure else duration + 15
    args = ['ip', 'netns', 'exec', CLIENT, str(BASE / 'hy2-client-linux-amd64'), '--config', str(path),
            '--tcp-target', '10.213.25.1:42901', '--udp-target', '10.213.25.1:42902',
            '--duration', str(duration) + 's', '--interval', '150ms', '--overall-timeout', str(timeout) + 's']
    if expected_failure:
        args.append('--expect-failure')
    try:
        task = subprocess.run(args, capture_output=True, timeout=timeout + 10)
        (directory / 'client.stderr.private').write_bytes(task.stderr)
        result = json.loads(task.stdout)
    finally:
        stop(observer)
    wire = json.loads(summary.read_text())
    success = task.returncode == 0 and result.get('passed') is True
    if hop:
        success = success and len(wire['distinct_destination_ports']) >= 2 and len(wire['distinct_source_ports']) >= 3
    report = {'kernel': kernel, 'test': label, 'pass': success, 'client_exit': task.returncode, 'client': result,
              'wire': wire, 'nft_redirect_enabled': label not in ['single-port', 'range-without-redirect']}
    REPORT.append(report)
    write(BASE / 'partial-report.json', REPORT)
    print(json.dumps({'kernel': kernel, 'test': label, 'pass': success,
                      'destination_ports': wire['distinct_destination_ports'], 'source_port_count': len(wire['distinct_source_ports']),
                      'flows': [{'kind': f['kind'], 'passed': f['passed'], 'error_class': f.get('error_class'), 'rounds': f['rounds']} for f in result.get('flows', [])]}), flush=True)


def kernel_tests(kernel):
    network()
    directory = ARTIFACTS / kernel
    directory.mkdir(mode=0o700)
    ready = directory / 'echo.ready.json'
    echo = start(SERVER, ['python3', '-I', '-B', HELPER, 'echo', '--ready', ready], directory / 'echo')
    wait_file(ready, echo)
    packet = json.loads((BASE / 'panel-runtime.private.json').read_text())
    raw = packet['node']
    nc = {key: raw[key] for key in ['protocol', 'listen_ip', 'server_port', 'version', 'host', 'server_name', 'tls_settings', 'up_mbps', 'down_mbps'] if raw.get(key) is not None}
    nc['custom_route_rules'] = [{'match': {'ip_cidrs': ['10.213.25.0/30']}, 'action': {'type': 'direct'}}]
    cfg = {'kernel': {'type': kernel, 'config_dir': str(directory / 'generated'), 'log_level': 'error'},
           'log': {'level': 'warn', 'output': str(directory / 'node.private.log')},
           'cert': {'cert_mode': 'file', 'cert_file': str(BASE / 'server.pem'), 'key_file': str(BASE / 'server.private.key')},
           'standalone': {'enabled': True, 'node': nc, 'users': [packet['user']]}}
    write(directory / 'server.private.json', cfg)
    node = start(SERVER, [BASE / 'xboard-node-linux-amd64', '-c', directory / 'server.private.json'], directory / 'node')
    try:
        for _ in range(100):
            require(node.poll() is None, 'node-startup-failed-' + kernel)
            if b':42443 ' in ns(SERVER, 'ss', '-H', '-lun'):
                break
            time.sleep(.05)
        else:
            raise RuntimeError('node-udp-not-listening-' + kernel)
        outbound = copy.deepcopy(packet['client_outbound'])
        outbound['tls'].update({'enabled': True, 'insecure': False, 'certificate_path': str(BASE / 'ca.pem'), 'server_name': 'hy2.fixture.test'})
        single = copy.deepcopy(outbound)
        single.pop('server_ports', None)
        single.pop('hop_interval', None)
        single['server_port'] = 42443
        client_case(kernel, 'single-port', single)
        client_case(kernel, 'range-without-redirect', outbound, duration=2, expected_failure=True)
        nat(True)
        mapped = copy.deepcopy(single)
        mapped['server_port'] = 42451
        client_case(kernel, 'mapped-single-port', mapped)
        client_case(kernel, 'hopping-same-tcp-and-udp', outbound, duration=45, hop=True)
        wrong = copy.deepcopy(outbound)
        wrong['password'] = 'deliberately-wrong-synthetic-identity'
        client_case(kernel, 'wrong-password-rejected', wrong, duration=2, expected_failure=True)
        wrong = copy.deepcopy(outbound)
        wrong['tls']['certificate_path'] = str(BASE / 'wrong-ca.pem')
        client_case(kernel, 'wrong-ca-rejected', wrong, duration=2, expected_failure=True)
        rules = json.loads(ns(SERVER, 'nft', '-j', 'list', 'table', 'inet', 'codex_hy2'))
        counters = [expression['counter'] for row in rules['nftables'] if 'rule' in row
                    for expression in row['rule'].get('expr', []) if 'counter' in expression]
        require(counters and sum(row['packets'] for row in counters) > 0, 'redirect-counter-did-not-increase')
        write(directory / 'redirect-counters.json', counters)
    finally:
        stop(node)
        stop(echo)
        clean_network()


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['prepare']:
        prepare()
        return
    if sys.argv[1:] == ['check-nft']:
        owned()
        try:
            network()
            nat(True)
            print(json.dumps({'fixture_nft_check': 'PASS'}), flush=True)
        finally:
            clean_network()
        return
    require(sys.argv[1:] == ['run'], 'unknown-case-action')
    owned()
    ARTIFACTS.mkdir(mode=0o700)
    before = hashlib.sha256(run(['nft', '-s', 'list', 'ruleset'])).hexdigest()
    try:
        for kernel in ['singbox', 'xray']:
            kernel_tests(kernel)
    finally:
        for process in reversed(PROCESSES):
            stop(process)
        if json.loads((BASE / 'network-state.json').read_text())['namespaces']:
            clean_network()
        after = hashlib.sha256(run(['nft', '-s', 'list', 'ruleset'])).hexdigest()
        connection = http.client.HTTPConnection('127.0.0.1', 17001, timeout=5)
        connection.request('GET', '/')
        response = connection.getresponse()
        status = response.status
        response.read(1024)
        connection.close()
        report = {'result': 'PASS' if len(REPORT) == 12 and all(row['pass'] for row in REPORT) else 'FUNCTIONAL_FAILURES',
                  'date': '2026-10-25', 'node_version': 'v1.13-orphan.5', 'node_sha256': NODE_SHA,
                  'client_sha256': CLIENT_SHA, 'tests': REPORT, 'host_nftables_unchanged': before == after,
                  'test_namespaces_removed': not json.loads((BASE / 'network-state.json').read_text())['namespaces'],
                  'owned_processes_exited': all(p.poll() is not None for p in PROCESSES),
                  'original_panel_http_status': status, 'private_runtime_logs_read': False,
                  'traffic_scope': 'isolated IPv4 veth pair; actual QUIC; no public network'}
        require(before == after and status == 200, 'host-protection-check-failed')
        write(BASE / 'report.json', report)
        public = Path('/tmp/codex-hy2-hop-result-20261025.json')
        write(public, report)
        public.chmod(0o644)
        print(json.dumps({'result': report['result'], 'checks': len(REPORT), 'failed': [x['kernel'] + ':' + x['test'] for x in REPORT if not x['pass']],
                          'host_nftables_unchanged': before == after, 'original_panel_http_status': status,
                          'test_namespaces_removed': report['test_namespaces_removed']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        print(json.dumps({'result': 'HARNESS_ERROR', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'controlled-fixture-error',
                          'fixture_frames': [{'function': frame.name, 'line': frame.lineno} for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]}), flush=True)
        raise SystemExit(1)
