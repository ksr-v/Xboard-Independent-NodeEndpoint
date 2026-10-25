"""Read-only public cleanup guard for this exact owned release fixture."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess

BASE = Path('/opt/xboard-node-menu-release-20261025')
UNIT = 'codex-node-menu-release-20261025.service'
CONTAINER = 'codex-xboard-hy2-auto-panel-20261025'


def run(argv):
    return subprocess.run(argv, capture_output=True, timeout=15)


assert os.geteuid() == 0 and not BASE.is_symlink()
assert json.loads((BASE / 'owner.json').read_text()) == {
    'tool': 'Codex isolated node management menu fixture', 'path': str(BASE), 'date': '2026-10-25'}
unit_removed = not (Path('/etc/systemd/system') / UNIT).exists()
inactive = run(['systemctl', 'is-active', UNIT]).returncode != 0
ports = []
for port, kind in [(17006, socket.SOCK_STREAM), (19631, socket.SOCK_STREAM),
                   (42443, socket.SOCK_DGRAM)]:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port)); free = True
        except OSError:
            free = False
    ports.append({'port': port, 'protocol': 'udp' if kind == socket.SOCK_DGRAM else 'tcp', 'released': free})
container_removed = run(['docker', 'container', 'inspect', CONTAINER]).returncode == 1
rules = json.loads(run(['nft', '-j', 'list', 'ruleset']).stdout)
count = sum('table' in row and row['table']['name'].startswith('xbnode_hop_') for row in rules['nftables'])
connection = http.client.HTTPConnection('127.0.0.1', 17001, timeout=5)
connection.request('GET', '/'); response = connection.getresponse(); status = response.status
response.read(1024); connection.close()
passed = unit_removed and inactive and all(row['released'] for row in ports) and container_removed and count == 0 and status == 200
report = {'result': 'PASS' if passed else 'FAIL', 'unit': UNIT, 'unit_removed': unit_removed,
          'unit_inactive': inactive, 'ports': ports, 'test_container_removed': container_removed,
          'managed_hy2_tables': count, 'original_panel_http_status': status,
          'private_runtime_logs_read': False, 'existing_private_pty_files_read': False,
          'private_test_data_retained': True}
output = Path('/tmp/codex-node-menu-release-final-guard-20261025.json')
output.write_text(json.dumps(report, indent=2) + '\n'); output.chmod(0o644)
print(json.dumps(report))
assert passed
