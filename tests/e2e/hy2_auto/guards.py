"""Owned real-nft safety regressions; no private Node log reads or host NAT writes."""
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

BASE = Path('/opt/xboard-hy2-auto-20261025')
spec = importlib.util.spec_from_file_location('owned_hy2_fixture', BASE / 'vm_hy2_auto_fixture.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
ROOT = BASE / 'artifacts' / 'guards'
ROWS = []


def require(ok, reason):
    f.require(ok, reason)


def own_tables():
    return [row for row in f.nft_state()['tables'] if row['name'].startswith('xbnode_hop_')]


def stateless_table(name):
    require(name.startswith('xbnode_hop_') or name == 'codex_hy2_guard_foreign', 'guard-table-scope-differs')
    return hashlib.sha256(f.ns(f.SERVER, 'nft', '-s', 'list', 'table', 'inet', name)).hexdigest()


def listening(port=42443):
    return (':' + str(port) + ' ').encode() in f.ns(f.SERVER, 'ss', '-H', '-lun')


def row(name, passed, **details):
    result = {'test': name, 'pass': bool(passed), **details}
    ROWS.append(result)
    f.write(ROOT / 'partial-report.json', ROWS)
    print(json.dumps({'test': name, 'pass': result['pass']}), flush=True)


def configuration(name, target=42443, hopping=True):
    directory = ROOT / name
    directory.mkdir(mode=0o700)
    packet = json.loads((BASE / 'panel-runtime.private.json').read_text())
    nc = {key: packet['node'][key] for key in ['protocol', 'listen_ip', 'server_port', 'version', 'host', 'server_name',
          'tls_settings', 'up_mbps', 'down_mbps', 'port_hopping'] if packet['node'].get(key) is not None}
    nc['server_port'] = target
    if not hopping:
        nc.pop('port_hopping', None)
    nc['custom_route_rules'] = [{'match': {'ip_cidrs': ['10.213.25.0/30']}, 'action': {'type': 'direct'}}]
    cfg = {'kernel': {'type': 'singbox', 'config_dir': str(directory / 'generated'), 'log_level': 'error'},
           'log': {'level': 'warn', 'output': str(directory / 'node.private.log')},
           'cert': {'cert_mode': 'file', 'cert_file': str(BASE / 'server.pem'), 'key_file': str(BASE / 'server.private.key')},
           'standalone': {'enabled': True, 'node': nc, 'users': [packet['user']]}}
    path = directory / 'server.private.json'
    f.write(path, cfg)
    return directory, path


def launch(directory, config, name='node', no_net_admin=False):
    args = [BASE / 'xboard-node-linux-amd64', '-c', config]
    if no_net_admin:
        args = ['setpriv', '--bounding-set=-net_admin', '--', *args]
    return f.start(f.SERVER, args, directory / name)


def rejected(process):
    try:
        return process.wait(timeout=12) not in [0, None]
    except subprocess.TimeoutExpired:
        f.stop(process)
        return False


def ready(process, target=42443):
    f.wait(lambda: listening(target) and bool(own_tables()), 'guard-startup-owned-mapping', process)


def clean_node(process):
    f.stop(process)
    f.wait(lambda: not own_tables(), 'guard-owned-table-cleanup')


def client(name, direct=False):
    directory = ROOT / ('client-' + name)
    directory.mkdir(mode=0o700)
    packet = json.loads((BASE / 'panel-runtime.private.json').read_text())
    outbound = copy.deepcopy(packet['client_outbound'])
    outbound['tls'].update({'enabled': True, 'insecure': False, 'certificate_path': str(BASE / 'ca.pem'), 'server_name': 'hy2.fixture.test'})
    if direct:
        outbound.pop('server_ports', None)
        outbound.pop('hop_interval', None)
        outbound['server_port'] = 42443
    config = directory / 'client.private.json'
    f.write(config, outbound)
    args = ['ip', 'netns', 'exec', f.CLIENT, str(BASE / 'hy2-client-linux-amd64'), '--config', str(config),
            '--tcp-target', '10.213.25.1:42901', '--udp-target', '10.213.25.1:42902',
            '--duration', '3s', '--interval', '150ms', '--overall-timeout', '18s']
    task = subprocess.run(args, capture_output=True, timeout=25)
    (directory / 'client.stderr.private').write_bytes(task.stderr)
    result = json.loads(task.stdout)
    flows = result.get('flows', [])
    passed = task.returncode == 0 and result.get('passed') is True and len(flows) == 2
    passed = passed and {item['kind'] for item in flows} == {'tcp', 'udp'}
    passed = passed and all(item.get('passed') and item['dial_count'] == 1 and item['verified_bytes'] > 0
                            and item['elapsed_ms'] >= 3000 for item in flows)
    return {'passed': passed, 'client_exit': task.returncode, 'client': result}


def foreign_udp():
    directory, config = configuration('foreign-udp')
    marker = directory / 'socket.ready.json'
    code = ('import json,socket,time; from pathlib import Path; '
            's=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(("10.213.25.1",42452)); '
            'Path(' + repr(str(marker)) + ').write_text(json.dumps({"ready":True})); time.sleep(120)')
    foreign = f.start(f.SERVER, ['python3', '-I', '-c', code], directory / 'foreign-socket')
    f.wait(marker.is_file, 'foreign-udp-ready', foreign)
    node = launch(directory, config)
    try:
        denied = rejected(node)
        preserved = foreign.poll() is None and listening(42452)
        absent = not own_tables()
        f.stop(foreign)
        control = launch(directory, config, 'after-foreign-removed')
        try:
            ready(control)
            works = client('foreign-removed-control')
        finally:
            clean_node(control)
        row('foreign-udp-listener-refused-without-interception', denied and preserved and absent and works['passed'],
            rejected_exit=node.returncode, foreign_socket_preserved=preserved, no_node_nat_on_rejection=absent,
            conflict_removed_control=works)
    finally:
        f.stop(node)
        f.stop(foreign)


def foreign_nat():
    directory, config = configuration('foreign-nat')
    rule = ('table inet codex_hy2_guard_foreign {\n comment "Codex isolated guard foreign NAT"\n'
            ' chain prerouting {\n type nat hook prerouting priority dstnat; policy accept;\n'
            ' ip saddr 10.213.25.2 udp dport 42450-42453 counter redirect to :42599\n }\n}\n')
    f.ns(f.SERVER, 'nft', '-f', '-', data=rule.encode())
    before = stateless_table('codex_hy2_guard_foreign')
    node = launch(directory, config)
    try:
        denied = rejected(node)
        preserved = stateless_table('codex_hy2_guard_foreign') == before
        absent = not own_tables()
        row('foreign-nat-overlap-refused-without-overwrite', denied and preserved and absent,
            rejected_exit=node.returncode, foreign_rules_preserved=preserved, no_node_nat_on_rejection=absent)
    finally:
        f.stop(node)
        tables = [t for t in f.nft_state()['tables'] if t['name'] == 'codex_hy2_guard_foreign']
        require(len(tables) == 1 and tables[0].get('comment') == 'Codex isolated guard foreign NAT', 'foreign-fixture-marker-differs')
        f.ns(f.SERVER, 'nft', 'delete', 'table', 'inet', 'codex_hy2_guard_foreign')


def no_net_admin():
    require(shutil.which('setpriv') is not None, 'setpriv-required-for-real-capability-test')
    probe = f.ns(f.SERVER, 'setpriv', '--bounding-set=-net_admin', '--', 'python3', '-I', '-c',
                 'from pathlib import Path; rows=Path("/proc/self/status").read_text().splitlines(); '
                 'print([int(next(x.split()[1] for x in rows if x.startswith(k)),16)&(1<<12) for k in ["CapBnd:","CapEff:"]])')
    require(probe.strip() == b'[0, 0]', 'net-admin-was-not-dropped')
    directory, config = configuration('no-net-admin-range')
    node = launch(directory, config, no_net_admin=True)
    denied = rejected(node)
    absent = not own_tables()
    f.stop(node)
    plain_dir, plain_config = configuration('no-net-admin-no-range', hopping=False)
    plain = launch(plain_dir, plain_config, no_net_admin=True)
    try:
        f.wait(lambda: listening() and not own_tables(), 'unrelated-no-range-compatible', plain)
        works = client('no-net-admin-primary', direct=True)
        row('missing-net-admin-rejected-only-when-hopping-requested', denied and absent and works['passed'],
            cap_net_admin_bounding_and_effective_bits_cleared=True, range_rejected_exit=node.returncode,
            no_node_nat_on_rejection=absent, no_range_compatible=works)
    finally:
        f.stop(plain)
        require(not own_tables(), 'no-range-created-nat')


def overlapping_nodes():
    first_dir, first_config = configuration('overlap-first')
    second_dir, second_config = configuration('overlap-second', target=42444)
    first = launch(first_dir, first_config)
    second = None
    try:
        ready(first)
        table = own_tables()[0]['name']
        before = stateless_table(table)
        second = launch(second_dir, second_config)
        denied = rejected(second)
        preserved = first.poll() is None and len(own_tables()) == 1 and stateless_table(table) == before
        mapped, primary = client('overlap-first-range'), client('overlap-first-primary', direct=True)
        row('overlapping-node-rejected-first-instance-preserved', denied and preserved and mapped['passed'] and primary['passed'],
            second_exit=second.returncode, first_rules_preserved=preserved, range_control=mapped, primary_control=primary)
    finally:
        if second is not None:
            f.stop(second)
        clean_node(first)


def stale_restart():
    directory, config = configuration('stale-same-owner')
    first = launch(directory, config, 'before-sigkill')
    second = None
    try:
        ready(first)
        table = own_tables()[0]['name']
        before = stateless_table(table)
        os.killpg(first.pid, signal.SIGKILL)
        require(first.wait(timeout=6) == -signal.SIGKILL, 'sigkill-did-not-exit-owned-node')
        stale = len(own_tables()) == 1 and stateless_table(table) == before and not listening()
        second = launch(directory, config, 'same-owner-restart')
        ready(second)
        same_owner = len(own_tables()) == 1 and own_tables()[0]['name'] == table
        mapped = client('same-owner-after-sigkill')
        clean_node(second)
        cleaned = not own_tables()
        row('sigkill-stale-table-reclaimed-and-sigterm-cleaned', stale and same_owner and mapped['passed'] and cleaned,
            sigkill_left_owned_stale_table=stale, restarted_same_owner_table=same_owner,
            restarted_range_control=mapped, sigterm_cleaned_owned_table=cleaned)
    finally:
        f.stop(first)
        if second is not None:
            f.stop(second)


def main():
    os.umask(0o077)
    require(sys.argv[1:] == ['run'], 'guards-run-action-required')
    f.owned()
    require(not ROOT.exists(), 'guards-artifact-directory-must-be-fresh')
    ROOT.mkdir(mode=0o700)
    manifest = json.loads((BASE / 'candidate-manifest.json').read_text())
    require(hashlib.sha256((BASE / 'xboard-node-linux-amd64').read_bytes()).hexdigest() == manifest['node_sha256'], 'guard-candidate-sha-differs')
    before = hashlib.sha256(f.run(['nft', '-s', 'list', 'ruleset'])).hexdigest()
    harness_error = None
    try:
        f.network()
        ready_file = ROOT / 'echo.ready.json'
        echo = f.start(f.SERVER, ['python3', '-I', '-B', BASE / 'wire_helpers.py', 'echo', '--ready', ready_file], ROOT / 'echo')
        f.wait(ready_file.is_file, 'guards-echo-ready', echo)
        for case in [foreign_udp, foreign_nat, no_net_admin, overlapping_nodes, stale_restart]:
            case()
            require(not own_tables(), 'guard-case-left-node-nat')
    except Exception as error:
        harness_error = type(error).__name__
        raise
    finally:
        for process in reversed(f.PROCESSES):
            f.stop(process)
        if json.loads((BASE / 'network-state.json').read_text())['namespaces']:
            f.clean_network()
        after = hashlib.sha256(f.run(['nft', '-s', 'list', 'ruleset'])).hexdigest()
        connection = http.client.HTTPConnection('127.0.0.1', 17001, timeout=5)
        connection.request('GET', '/')
        response = connection.getresponse()
        status = response.status
        response.read(1024)
        connection.close()
        result = 'PASS' if len(ROWS) == 5 and all(x['pass'] for x in ROWS) and before == after and status == 200 else 'FAILED_OR_INCOMPLETE'
        report = {'result': result, 'date': '2026-10-25', 'candidate': manifest, 'kernel': 'singbox',
                  'tests': ROWS, 'harness_error_type': harness_error, 'host_nftables_unchanged': before == after,
                  'original_panel_http_status': status, 'owned_processes_exited': all(p.poll() is not None for p in f.PROCESSES),
                  'test_namespaces_removed': not json.loads((BASE / 'network-state.json').read_text())['namespaces'],
                  'private_runtime_logs_read': False, 'traffic_scope': 'isolated IPv4 veth; shared portforward safety guards',
                  'fixture_nat_scope': 'one owned foreign conflict rule, only inside new server namespace',
                  'guard_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        f.write(ROOT / 'report.json', report)
        public = Path('/tmp/codex-hy2-auto-guards-result-20261025.json')
        f.write(public, report)
        public.chmod(0o644)
        print(json.dumps({'result': result, 'checks': len(ROWS), 'host_nftables_unchanged': before == after,
                          'original_panel_http_status': status, 'private_runtime_logs_read': False}), flush=True)
        require(before == after and status == 200, 'guard-host-protection-check-failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'result': 'HARNESS_ERROR', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'controlled-guard-error'}), flush=True)
        raise SystemExit(1)
