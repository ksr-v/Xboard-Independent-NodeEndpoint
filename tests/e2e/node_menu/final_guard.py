"""Read-only final checks for the three owned menu fixtures; no log reads."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess


def command(args):
    return subprocess.run(args, capture_output=True, timeout=15)


assert os.geteuid() == 0
units = []
for suffix in ['', '-r2', '-r3']:
    base = Path('/opt/xboard-node-menu-20261025' + suffix)
    assert not base.is_symlink()
    assert json.loads((base / 'owner.json').read_text()) == {
        'tool': 'Codex isolated node management menu fixture',
        'path': str(base), 'date': '2026-10-25'}
    name = 'codex-node-menu-20261025' + suffix + '.service'
    units.append({'unit': name,
                  'file_removed': not (Path('/etc/systemd/system') / name).exists(),
                  'inactive': command(['systemctl', 'is-active', name]).returncode != 0})

ports = []
for port, kind in [(17006, socket.SOCK_STREAM), (19631, socket.SOCK_STREAM),
                   (42443, socket.SOCK_DGRAM)]:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port))
            released = True
        except OSError:
            released = False
    ports.append({'port': port, 'protocol': 'udp' if kind == socket.SOCK_DGRAM else 'tcp',
                  'released': released})

container = command(['docker', 'container', 'inspect',
                     'codex-xboard-hy2-auto-panel-20261025'])
rules = json.loads(command(['nft', '-j', 'list', 'ruleset']).stdout)
owned_tables = [row['table']['name'] for row in rules['nftables']
                if 'table' in row and row['table']['name'].startswith('xbnode_hop_')]
http = http.client.HTTPConnection('127.0.0.1', 17001, timeout=5)
http.request('GET', '/')
response = http.getresponse()
status = response.status
response.read(1024)
http.close()
ok = (all(row['file_removed'] and row['inactive'] for row in units)
      and all(row['released'] for row in ports) and container.returncode == 1
      and not owned_tables and status == 200)
report = {'result': 'PASS' if ok else 'FAIL', 'units': units, 'ports': ports,
          'test_container_removed': container.returncode == 1,
          'host_managed_hy2_tables': len(owned_tables),
          'original_panel_http_status': status,
          'private_runtime_logs_read': False, 'private_test_data_retained': True}
out = Path('/tmp/codex-node-menu-final-guard-20261025.json')
out.write_text(json.dumps(report, indent=2) + '\n')
out.chmod(0o644)
print(json.dumps(report))
assert ok
