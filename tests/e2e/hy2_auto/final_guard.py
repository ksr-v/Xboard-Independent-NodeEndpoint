import http.client
import json
import os
from pathlib import Path
import socket
import subprocess

base = Path('/opt/xboard-hy2-auto-20261025')
assert os.geteuid() == 0
assert json.loads((base / 'owner.json').read_text()) == {'tool': 'Codex isolated automatic HY2 hopping test', 'path': str(base), 'date': '2026-10-25'}
names = json.loads(subprocess.check_output(['ip', '-j', 'netns', 'list']) or b'[]')
remaining = [r['name'] for r in names if r['name'] in ['codex-hy2-auto-s-1025', 'codex-hy2-auto-c-1025']]
processes = 0
for path in Path('/proc').iterdir():
    if not path.name.isdigit() or int(path.name) == os.getpid(): continue
    try:
        words = (path / 'cmdline').read_bytes().split(b'\0')
    except (OSError, ProcessLookupError): continue
    if words and (words[0] == str(base / 'xboard-node-linux-amd64').encode()
                  or any(x == str(base / name).encode() for x in words[1:] for name in ['wire_helpers.py', 'byte_relay.py', 'hy2-client-linux-amd64'])):
        processes += 1
rules = json.loads(subprocess.check_output(['nft', '-j', 'list', 'ruleset']))
tables = [r['table']['name'] for r in rules['nftables'] if 'table' in r and r['table']['name'].startswith('xbnode_hop_')]
container = subprocess.run(['docker', 'container', 'inspect', 'codex-xboard-hy2-auto-panel-20261025'], capture_output=True)
try:
    c = socket.create_connection(('127.0.0.1', 17006), timeout=.5)
except OSError:
    released = True
else:
    c.close(); released = False
http = http.client.HTTPConnection('127.0.0.1', 17001, timeout=5)
http.request('GET','/'); response = http.getresponse(); status = response.status; response.read(1024); http.close()
report = {'result': 'PASS' if not remaining and processes == 0 and not tables and container.returncode == 1 and released and status == 200 else 'FAIL',
          'test_namespaces_remaining': remaining, 'owned_test_processes': processes, 'host_managed_hy2_tables': len(tables),
          'test_container_removed': container.returncode == 1, 'loopback_17006_released': released,
          'original_panel_http_status': status, 'private_runtime_logs_read': False, 'private_fixture_data_retained': True}
out = Path('/tmp/codex-hy2-auto-final-guard-20261025.json')
out.write_text(json.dumps(report, indent=2)+'\n'); out.chmod(0o644)
print(json.dumps(report))
assert report['result'] == 'PASS'
