"""Real owned SQLite panel -> machine API/WS -> Node automatic HY2 NAT test.

Private credentials are used only in memory/private configs. Runtime logs and
relay contents are never read. No synthetic API server or manual NAT is used.
"""
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
BASE = Path('/opt/xboard-hy2-auto-20261025')
MANAGED = BASE / 'managed-panel'
PANEL = Path('/opt/xboard-hy2-auto-panel-20261025')
PRIVATE = PANEL / 'private'
NAME = 'codex-xboard-hy2-auto-panel-20261025'
PANEL_OWNER = {'tool': 'Codex Xboard HY2 auto panel fixture', 'path': str(PANEL), 'date': '2026-10-25'}
NODE_SHA = 'd8310fd9960e732b971e83038945434b6af775390a5582f10655e3ed92f6d5a6'
REPORT = []
SPEC = importlib.util.spec_from_file_location('owned_hy2_fixture', BASE / 'vm_hy2_auto_fixture.py')
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)


def record(kernel, name, passed, **details):
    row = {'kernel': kernel.removeprefix('managed-'), 'test': name, 'pass': bool(passed), **details}
    REPORT.append(row)
    f.write(MANAGED / 'partial-report.json', REPORT)
    print(json.dumps({'kernel': row['kernel'], 'test': name, 'pass': row['pass']}), flush=True)


f.record = record


def php(action, data=None):
    f.require(action in ['get-safe-config', 'mutate-range', 'disable', 'private-credentials'], 'unknown-panel-action')
    args = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', str(PRIVATE / 'docker-config'),
            'exec', '--user', '1000:1000', '--workdir', '/www', '--env', 'XBOARD_HY2_AUTO_PANEL_FIXTURE=20261025',
            '-i', NAME, 'php', '/tmp/codex-hy2-auto-panel-seed-20261025.php', action]
    task = subprocess.run(args, input=json.dumps(data or {}).encode(), capture_output=True, timeout=25)
    f.require(task.returncode == 0, 'owned-panel-action-failed-' + action)
    return json.loads(task.stdout)


def credentials():
    value = php('private-credentials')
    f.require(value.get('private') is True and value['node']['protocol'] == 'hysteria'
              and value['node']['version'] == 2 and value['node']['server_port'] == 42443, 'private-panel-identity-differs')
    return value


def host_start(args):
    process = subprocess.Popen(list(map(str, args)), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    f.PROCESSES.append(process)
    return process


def outbound(value):
    result = copy.deepcopy(value['client_outbound'])
    result['tls'].update({'enabled': True, 'insecure': False,
                          'certificate_path': str(BASE / 'ca.pem'), 'server_name': 'hy2.fixture.test'})
    return result


def wait_ws(expected, node=None, timeout=20):
    state = {}
    def ready():
        state.clear()
        state.update(php('get-safe-config'))
        return state.get('ws_node_alive') is expected and state.get('ws_worker_heartbeat') is True
    f.wait(ready, 'real-panel-ws-alive-' + str(expected), node, timeout=timeout)
    return state


def mapping_matches(start, end, absent=None):
    state = f.nft_state()
    return f.listener() and f.has_range(state, start, end) and (absent is None or not f.has_range(state, *absent))


def kernel_test(kernel):
    php('mutate-range', {'start': 42450, 'end': 42453})
    pre_ws = wait_ws(False)
    value = credentials()
    f.require(value['node']['port_hopping'] == {'start': 42450, 'end': 42453}, 'initial-live-panel-range-differs')
    f.network()
    directory = MANAGED / kernel
    directory.mkdir(mode=0o700)
    socket_path = MANAGED / 'relay.sock'
    host_ready = directory / 'host-relay.ready.json'
    host_relay = host_start(['python3', '-I', '-B', BASE / 'byte_relay.py', 'host', '--socket', socket_path, '--ready', host_ready])
    f.wait(host_ready.is_file, 'host-relay-ready', host_relay)
    ns_ready = directory / 'ns-relay.ready.json'
    ns_relay = f.start(f.SERVER, ['python3', '-I', '-B', BASE / 'byte_relay.py', 'namespace', '--socket', socket_path, '--ready', ns_ready], directory / 'relay')
    f.wait(ns_ready.is_file, 'ns-relay-ready', ns_relay)
    echo_ready = directory / 'echo.ready.json'
    echo = f.start(f.SERVER, ['python3', '-I', '-B', BASE / 'wire_helpers.py', 'echo', '--ready', echo_ready], directory / 'echo')
    f.wait(echo_ready.is_file, 'echo-ready', echo)
    if kernel == 'singbox':
        rules = [{'ip_cidr': ['10.213.25.0/30'], 'action': 'route', 'outbound': 'direct'}]
    else:
        rules = [{'type': 'field', 'ip': ['10.213.25.0/30'], 'outboundTag': 'direct'}]
    cfg = {'kernel': {'type': kernel, 'config_dir': str(directory / 'generated'),
                      'log_level': 'error', 'custom_route': rules},
           'log': {'level': 'warn', 'output': str(directory / 'node.private.log')},
           'cert': {'cert_mode': 'file', 'cert_file': str(BASE / 'server.pem'), 'key_file': str(BASE / 'server.private.key')},
           'panel': {'url': 'http://127.0.0.1:17006'},
           'machine': {'machine_id': value['machine_id'], 'token': value['machine_token']},
           'node': {'pull_interval': 2, 'push_interval': 2, 'track_interval': 1},
           'ws': {'status_interval': 2, 'handshake_timeout': 5, 'backoff_initial': 1,
                  'backoff_max': 2, 'discovery_interval': 5}}
    path = directory / 'machine.private.json'
    f.write(path, cfg)
    node = f.start(f.SERVER, [BASE / 'xboard-node-linux-amd64', '-c', path], directory / 'node')
    try:
        f.wait(lambda: mapping_matches(42450, 42453), 'machine-http-bootstrap-mapping', node, timeout=25)
        state = f.nft_state()
        record(kernel, 'real-machine-api-bootstrap-created-rules', f.valid_tables(state), nft=state,
               config_source='real owned panel machine HTTP API; no standalone config')
        connected = wait_ws(True, node)
        record(kernel, 'real-panel-websocket-connected', pre_ws['ws_node_alive'] is False
               and connected['ws_node_alive'] is True and connected['ws_worker_heartbeat'] is True,
               safe_backend_state=connected, ws_alive_before_start=False)
        f.client_case('managed-' + kernel, 'real-panel-initial-range-traffic', outbound(value), duration=12,
                      expected_ports=range(42450, 42454))
        began = time.monotonic()
        changed = php('mutate-range', {'start': 42454, 'end': 42457})
        saved = time.monotonic()
        f.wait(lambda: mapping_matches(42454, 42457, (42450, 42453)), 'live-real-panel-range-update', node, timeout=12)
        matched = time.monotonic()
        after = php('get-safe-config')
        record(kernel, 'live-real-panel-range-replaced-same-process', node.poll() is None
               and after['port_hopping'] == {'start': 42454, 'end': 42457},
               observed_after_save_ms=round((matched - saved) * 1000),
               save_command_ms=round((saved - began) * 1000), controller_save_ms=changed.get('save_ms'),
               ws_alive_at_save=changed['config']['ws_node_alive'], ws_alive_after=after['ws_node_alive'],
               update_transport='real backend WS connected; REST fallback configured at 2 seconds', nft=f.nft_state())
        f.client_case('managed-' + kernel, 'managed-old-range-rejected', outbound(value), duration=2, negative=True,
                      expected_ports=range(42450, 42454))
        changed_value = credentials()
        f.require(changed_value['client_outbound']['server_ports'] == ['42454:42457'], 'real-subscription-range-not-updated')
        f.client_case('managed-' + kernel, 'real-panel-changed-range-traffic', outbound(changed_value), duration=12,
                      expected_ports=range(42454, 42458))
        began = time.monotonic()
        disabled = php('disable')
        saved = time.monotonic()
        f.wait(lambda: f.listener() and not f.nft_state()['tables'], 'live-real-panel-disable', node, timeout=12)
        record(kernel, 'live-real-panel-disable-removed-own-table', node.poll() is None
               and disabled['config']['port_hopping'] is None,
               observed_after_save_ms=round((time.monotonic() - saved) * 1000),
               save_command_ms=round((saved - began) * 1000), controller_save_ms=disabled.get('save_ms'),
               ws_alive_at_save=disabled['config']['ws_node_alive'])
        disabled_value = credentials()
        f.require('server_ports' not in disabled_value['client_outbound'], 'disabled-subscription-still-hops')
        f.client_case('managed-' + kernel, 'real-panel-disabled-primary-port-works', outbound(disabled_value),
                      expected_ports=[42443])
        f.client_case('managed-' + kernel, 'managed-disabled-range-rejected', outbound(changed_value), duration=2,
                      negative=True, expected_ports=range(42454, 42458))
        php('mutate-range', {'start': 42450, 'end': 42453})
        f.wait(lambda: mapping_matches(42450, 42453), 'live-real-panel-reenable', node, timeout=12)
        record(kernel, 'live-real-panel-reenable-created-rules', node.poll() is None and f.valid_tables(f.nft_state()))
        f.stop(node)
        f.wait(lambda: not f.nft_state()['tables'], 'managed-sigterm-cleanup')
        record(kernel, 'managed-sigterm-cleaned-own-table', node.returncode == 0 and not f.nft_state()['tables'],
               exit_code=node.returncode)
    finally:
        f.stop(node)
        f.stop(echo)
        f.stop(ns_relay)
        f.stop(host_relay)
        f.clean_network()


def main():
    os.umask(0o077)
    f.require(sys.argv[1:] == ['run'], 'unknown-managed-action')
    f.owned()
    f.require(json.loads((PANEL / 'owner.json').read_text()) == PANEL_OWNER, 'owned-panel-marker-differs')
    f.require(not json.loads((BASE / 'network-state.json').read_text())['namespaces'], 'standalone-test-still-running')
    f.require(not MANAGED.exists() and not MANAGED.is_symlink(), 'fresh-managed-directory-required')
    f.require(hashlib.sha256((BASE / 'xboard-node-linux-amd64').read_bytes()).hexdigest() == NODE_SHA, 'candidate-sha-differs')
    credential_file = PRIVATE / 'credentials.private.json'
    f.require(credential_file.is_file() and not credential_file.is_symlink()
              and credential_file.stat().st_uid == 0 and stat.S_IMODE(credential_file.stat().st_mode) == 0o600,
              'root-private-credentials-required')
    initial = json.loads(credential_file.read_text())
    f.require(initial.get('private') is True and initial.get('machine_id') and initial.get('machine_token'), 'owned-credentials-shape-differs')
    MANAGED.mkdir(mode=0o700)
    before = hashlib.sha256(f.run(['nft', '-s', 'list', 'ruleset'])).hexdigest()
    try:
        for kernel in ['singbox', 'xray']:
            kernel_test(kernel)
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
        protected = before == after and status == 200
        report = {'result': 'PASS' if len(REPORT) == 22 and all(row['pass'] for row in REPORT) and protected else 'FUNCTIONAL_FAILURES',
                  'date': '2026-10-25', 'node_sha256': NODE_SHA, 'tests': REPORT,
                  'host_nftables_unchanged': before == after, 'original_panel_http_status': status,
                  'test_namespaces_removed': not json.loads((BASE / 'network-state.json').read_text())['namespaces'],
                  'owned_processes_exited': all(p.poll() is not None for p in f.PROCESSES),
                  'relay_socket_removed': not (MANAGED / 'relay.sock').exists(),
                  'private_runtime_logs_read': False, 'fixture_installed_nat': False,
                  'control_plane': 'real owned Laravel/SQLite/Redis/NodeWorker via byte-transparent Unix relay',
                  'traffic_scope': 'isolated IPv4 veth; actual QUIC; no public network'}
        f.write(MANAGED / 'report.json', report)
        public = Path('/tmp/codex-hy2-auto-managed-result-20261025.json')
        f.write(public, report)
        public.chmod(0o644)
        print(json.dumps({'result': report['result'], 'checks': len(REPORT),
                          'failed': [row['kernel'] + ':' + row['test'] for row in REPORT if not row['pass']],
                          'host_nftables_unchanged': before == after, 'original_panel_http_status': status}), flush=True)
        f.require(protected, 'host-protection-check-failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        print(json.dumps({'result': 'HARNESS_ERROR', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'controlled-managed-fixture-error',
                          'fixture_frames': [{'function': frame.name, 'line': frame.lineno} for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]}), flush=True)
        raise SystemExit(1)
