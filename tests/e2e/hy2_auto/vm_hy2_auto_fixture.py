"""Owned Node-managed UDP hopping regression. No host firewall writes/log reads."""
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

BASE = Path('/opt/xboard-hy2-auto-20261025')
UPLOAD = Path('/tmp/codex-hy2-auto-upload-20261025')
OWNER = {'tool': 'Codex isolated automatic HY2 hopping test', 'path': str(BASE), 'date': '2026-10-25'}
SERVER, CLIENT = 'codex-hy2-auto-s-1025', 'codex-hy2-auto-c-1025'
CLIENT_SHA = '115aaf17049a259241fe7c0118f351f39f7d71fba2eff372b63dd06a5d24b86e'
REPORT, PROCESSES = [], []


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def write(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def run(args, *, data=None, timeout=20):
    task = subprocess.run(list(map(str, args)), input=data, capture_output=True, timeout=timeout)
    require(task.returncode == 0, 'controlled-command-failed-' + Path(str(args[0])).name)
    return task.stdout


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
            process.wait(timeout=22)
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


def wait(predicate, reason, process=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            require(process.poll() is None, 'fixture-child-exited-' + reason)
        if predicate():
            return
        time.sleep(.1)
    raise RuntimeError('fixture-timeout-' + reason)


def record(kernel, name, passed, **details):
    row = {'kernel': kernel, 'test': name, 'pass': bool(passed), **details}
    REPORT.append(row)
    write(BASE / 'partial-report.json', REPORT)
    print(json.dumps({'kernel': kernel, 'test': name, 'pass': row['pass']}), flush=True)


def prepare():
    require(os.geteuid() == 0 and not BASE.exists(), 'fresh-root-case-required')
    source = Path(__file__).resolve().parent
    require(source == UPLOAD, 'upload-path-differs')
    manifest = json.loads((source / 'candidate-manifest.json').read_text())
    require(re.fullmatch('[0-9a-f]{64}', manifest.get('node_sha256', '')) is not None, 'candidate-sha-required')
    require(manifest.get('client_sha256') == CLIENT_SHA, 'client-manifest-differs')
    for name, expected in [('xboard-node-linux-amd64', manifest['node_sha256']), ('hy2-client-linux-amd64', CLIENT_SHA)]:
        path = source / name
        require(path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                'binary-sha-differs-' + name)
    BASE.mkdir(mode=0o700)
    write(BASE / 'owner.json', OWNER)
    for name in ['xboard-node-linux-amd64', 'hy2-client-linux-amd64', 'wire_helpers.py',
                 'vm_hy2_auto_fixture.py', 'panel-runtime.private.json', 'candidate-manifest.json']:
        path = source / name
        require(path.is_file() and not path.is_symlink(), 'source-file-differs')
        shutil.copyfile(path, BASE / name)
        (BASE / name).chmod(0o700 if name.endswith('amd64') else 0o600)
    packet = json.loads((BASE / 'panel-runtime.private.json').read_text())
    require(packet['node']['protocol'] == 'hysteria' and packet['node']['version'] == 2
            and packet['node']['server_port'] == 42443, 'panel-node-config-differs')
    require(packet['node']['port_hopping'] == {'start': 42450, 'end': 42453}, 'panel-auto-hopping-config-differs')
    require(packet['client_outbound']['server_ports'] == ['42450:42453']
            and packet['client_outbound']['hop_interval'] == '5s', 'panel-client-hopping-differs')
    require(packet['client_outbound']['password'] == packet['user']['uuid'], 'synthetic-identity-differs')
    run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', BASE / 'ca.private.key'])
    run(['openssl', 'req', '-new', '-x509', '-key', BASE / 'ca.private.key', '-out', BASE / 'ca.pem',
         '-days', '2', '-subj', '/CN=Codex isolated auto HY2 CA', '-addext', 'basicConstraints=critical,CA:TRUE'])
    run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', BASE / 'server.private.key'])
    run(['openssl', 'req', '-new', '-key', BASE / 'server.private.key', '-out', BASE / 'server.private.csr', '-subj', '/CN=hy2.fixture.test'])
    (BASE / 'certificate.ext').write_text('subjectAltName=DNS:hy2.fixture.test,IP:10.213.25.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature\nbasicConstraints=CA:FALSE\n')
    run(['openssl', 'x509', '-req', '-in', BASE / 'server.private.csr', '-CA', BASE / 'ca.pem', '-CAkey', BASE / 'ca.private.key',
         '-CAcreateserial', '-out', BASE / 'server.pem', '-days', '2', '-extfile', BASE / 'certificate.ext'])
    write(BASE / 'network-state.json', {'namespaces': []})
    print(json.dumps({'result': 'PASS', 'phase': 'prepared', **manifest}), flush=True)


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
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        run(['ip', 'netns', 'del', name])
    write(BASE / 'network-state.json', {'namespaces': []})


def nft_state():
    raw = json.loads(ns(SERVER, 'nft', '-j', 'list', 'ruleset'))
    tables = [row['table'] for row in raw.get('nftables', []) if 'table' in row]
    rules = [row['rule'] for row in raw.get('nftables', []) if 'rule' in row]
    return {'tables': tables, 'rules': rules}


def contains(value, predicate):
    if predicate(value):
        return True
    if isinstance(value, dict):
        return any(contains(item, predicate) for item in value.values())
    if isinstance(value, list):
        return any(contains(item, predicate) for item in value)
    return False


def has_range(state, start, end):
    for rule in state['rules']:
        same_range = contains(rule, lambda x: isinstance(x, dict) and x.get('range') == [start, end])
        redirect = contains(rule, lambda x: isinstance(x, dict) and isinstance(x.get('redirect'), dict)
                            and x['redirect'].get('port') == 42443)
        if same_range and redirect:
            return True
    return False


def valid_tables(state):
    return bool(state['tables']) and all(row.get('family') == 'inet'
        and re.fullmatch('xbnode_hop_[0-9a-f]{24}', row.get('name', ''))
        and row.get('comment') for row in state['tables'])


def listener():
    return b':42443 ' in ns(SERVER, 'ss', '-H', '-lun')


def client_case(kernel, name, outbound, duration=4, negative=False, hopping=False, expected_ports=None):
    directory = BASE / 'artifacts' / kernel / name
    directory.mkdir(parents=True, mode=0o700)
    config = directory / 'client.private.json'
    write(config, outbound)
    ready, summary = directory / 'observer.ready.json', directory / 'wire-summary.json'
    observer = start(SERVER, ['python3', '-I', '-B', BASE / 'wire_helpers.py', 'observe', '--ready', ready, '--output', summary], directory / 'observer')
    wait(ready.is_file, 'observer-ready', observer)
    timeout = 10 if negative else duration + 15
    args = ['ip', 'netns', 'exec', CLIENT, str(BASE / 'hy2-client-linux-amd64'), '--config', str(config),
            '--tcp-target', '10.213.25.1:42901', '--udp-target', '10.213.25.1:42902',
            '--duration', str(duration) + 's', '--interval', '150ms', '--overall-timeout', str(timeout) + 's']
    if negative:
        args.append('--expect-failure')
    try:
        task = subprocess.run(args, capture_output=True, timeout=timeout + 10)
        (directory / 'client.stderr.private').write_bytes(task.stderr)
        result = json.loads(task.stdout)
    finally:
        stop(observer)
    wire = json.loads(summary.read_text())
    flows = result.get('flows', [])
    success = task.returncode == 0 and result.get('passed') is True and len(flows) == 2
    success = success and {f['kind'] for f in flows} == {'tcp', 'udp'} and all(f['dial_count'] == 1 for f in flows)
    if not negative:
        success = success and all(f['passed'] and f['verified_bytes'] > 0 and f['elapsed_ms'] >= duration * 1000 for f in flows)
    else:
        success = success and all(not f['passed'] and f['verified_bytes'] == 0 for f in flows)
    if hopping:
        success = success and len(wire['distinct_destination_ports']) >= 2 and len(wire['distinct_source_ports']) >= 3
    if expected_ports is not None:
        success = success and bool(wire['distinct_destination_ports']) and set(wire['distinct_destination_ports']).issubset(set(expected_ports))
    record(kernel, name, success, client_exit=task.returncode, client=result, wire=wire,
           mapping_source='xboard-node-managed', fixture_installed_nat=False)


def kernel_tests(kernel):
    network()
    directory = BASE / 'artifacts' / kernel
    directory.mkdir(mode=0o700)
    ready = directory / 'echo.ready.json'
    echo = start(SERVER, ['python3', '-I', '-B', BASE / 'wire_helpers.py', 'echo', '--ready', ready], directory / 'echo')
    wait(ready.is_file, 'echo-ready', echo)
    packet = json.loads((BASE / 'panel-runtime.private.json').read_text())
    nc = {key: packet['node'][key] for key in ['protocol', 'listen_ip', 'server_port', 'version', 'host', 'server_name',
          'tls_settings', 'up_mbps', 'down_mbps', 'port_hopping'] if packet['node'].get(key) is not None}
    nc['custom_route_rules'] = [{'match': {'ip_cidrs': ['10.213.25.0/30']}, 'action': {'type': 'direct'}}]
    cfg = {'kernel': {'type': kernel, 'config_dir': str(directory / 'generated'), 'log_level': 'error'},
           'log': {'level': 'warn', 'output': str(directory / 'node.private.log')},
           'cert': {'cert_mode': 'file', 'cert_file': str(BASE / 'server.pem'), 'key_file': str(BASE / 'server.private.key')},
           'standalone': {'enabled': True, 'node': nc, 'users': [packet['user']]}}
    config = directory / 'server.private.json'
    write(config, cfg)
    node = start(SERVER, [BASE / 'xboard-node-linux-amd64', '-c', config], directory / 'node')
    try:
        wait(lambda: listener() and has_range(nft_state(), 42450, 42453), 'automatic-startup-mapping', node)
        state = nft_state()
        record(kernel, 'startup-created-owned-rules', valid_tables(state) and has_range(state, 42450, 42453), nft=state)
        outbound = copy.deepcopy(packet['client_outbound'])
        outbound['tls'].update({'enabled': True, 'insecure': False, 'certificate_path': str(BASE / 'ca.pem'), 'server_name': 'hy2.fixture.test'})
        client_case(kernel, 'automatic-hopping-same-tcp-and-udp', outbound, duration=45, hopping=True, expected_ports=range(42450, 42454))
        cfg['standalone']['node']['port_hopping'] = {'start': 42454, 'end': 42457}
        changed = time.monotonic()
        write(config, cfg)
        wait(lambda: listener() and has_range(nft_state(), 42454, 42457) and not has_range(nft_state(), 42450, 42453), 'live-range-replaced', node)
        state = nft_state()
        record(kernel, 'live-range-replaced-without-process-restart', node.poll() is None and valid_tables(state),
               elapsed_ms=round((time.monotonic() - changed) * 1000), nft=state,
               update_method='standalone file watcher; services restart inside same Go process')
        client_case(kernel, 'old-range-rejected-after-change', outbound, duration=2, negative=True, expected_ports=range(42450, 42454))
        changed_outbound = copy.deepcopy(outbound)
        changed_outbound['server_ports'] = ['42454:42457']
        client_case(kernel, 'changed-range-continuous-tcp-and-udp', changed_outbound, duration=12, expected_ports=range(42454, 42458))
        cfg['standalone']['node'].pop('port_hopping')
        disabled = time.monotonic()
        write(config, cfg)
        wait(lambda: listener() and not nft_state()['tables'], 'disabled-mapping-removed', node)
        record(kernel, 'disable-removed-owned-table', True, elapsed_ms=round((time.monotonic() - disabled) * 1000))
        client_case(kernel, 'disabled-range-rejected', changed_outbound, duration=2, negative=True, expected_ports=range(42454, 42458))
        single = copy.deepcopy(outbound)
        single.pop('server_ports', None)
        single.pop('hop_interval', None)
        single['server_port'] = 42443
        client_case(kernel, 'disabled-primary-port-still-works', single, expected_ports=[42443])
        cfg['standalone']['node']['port_hopping'] = {'start': 42450, 'end': 42453}
        write(config, cfg)
        wait(lambda: listener() and has_range(nft_state(), 42450, 42453), 'reenabled-mapping', node)
        stop(node)
        wait(lambda: not nft_state()['tables'], 'sigterm-table-cleanup')
        record(kernel, 'sigterm-cleaned-owned-table', node.returncode == 0 and not nft_state()['tables'], exit_code=node.returncode)
        invalid_cfg = copy.deepcopy(cfg)
        invalid_cfg['standalone']['node']['port_hopping'] = {'start': 42457, 'end': 42454}
        invalid_path = directory / 'invalid-range.private.json'
        write(invalid_path, invalid_cfg)
        invalid = start(SERVER, [BASE / 'xboard-node-linux-amd64', '-c', invalid_path], directory / 'invalid-node')
        try:
            exit_code = invalid.wait(timeout=10)
        except subprocess.TimeoutExpired:
            exit_code = None
            stop(invalid)
        record(kernel, 'invalid-reversed-range-rejected-at-startup', exit_code not in [None, 0] and not nft_state()['tables'],
               exit_code=exit_code, remaining_nft_tables=nft_state()['tables'])
    finally:
        stop(node)
        stop(echo)
        clean_network()


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['prepare']:
        prepare()
        return
    require(sys.argv[1:] == ['run'], 'unknown-case-action')
    owned()
    (BASE / 'artifacts').mkdir(mode=0o700)
    manifest = json.loads((BASE / 'candidate-manifest.json').read_text())
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
        protected = before == after and status == 200
        result = 'PASS' if len(REPORT) == 20 and all(row['pass'] for row in REPORT) and protected else 'FUNCTIONAL_FAILURES'
        report = {'result': result, 'date': '2026-10-25', 'candidate': manifest, 'tests': REPORT,
                  'host_nftables_unchanged': before == after, 'original_panel_http_status': status,
                  'test_namespaces_removed': not json.loads((BASE / 'network-state.json').read_text())['namespaces'],
                  'owned_processes_exited': all(p.poll() is not None for p in PROCESSES),
                  'private_runtime_logs_read': False, 'fixture_installed_nat': False,
                  'traffic_scope': 'isolated IPv4 veth; actual QUIC; node-owned nftables only; no public network'}
        write(BASE / 'report.json', report)
        public = Path('/tmp/codex-hy2-auto-result-20261025.json')
        write(public, report)
        public.chmod(0o644)
        print(json.dumps({'result': result, 'checks': len(REPORT), 'failed': [x['kernel'] + ':' + x['test'] for x in REPORT if not x['pass']],
                          'host_nftables_unchanged': before == after, 'original_panel_http_status': status,
                          'test_namespaces_removed': report['test_namespaces_removed']}), flush=True)
        require(protected, 'host-protection-check-failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        print(json.dumps({'result': 'HARNESS_ERROR', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'controlled-fixture-error',
                          'fixture_frames': [{'function': frame.name, 'line': frame.lineno} for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]}), flush=True)
        raise SystemExit(1)
