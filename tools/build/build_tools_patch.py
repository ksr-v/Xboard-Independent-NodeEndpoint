#!/usr/bin/env python3
"""Build supplementary tools from the pinned original ZIP without replacing it."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE_SHA256 = '42f2893938c294646c44e351a5c251cfea9f6a30156d67c345bc476307ea89e2'
REPLACEMENTS = ('tools/panel/nginx.py', 'tools/panel/manager.py', 'tools/panel/node-endpoint-panel.sh')
ADDITIONS = {'1.0.1': (), '1.0.2': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php'),
             '1.0.3': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php', 'tools/panel/firewall.py'),
             '1.0.4': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php', 'tools/panel/firewall.py',
                       'tools/panel/panel_passphrase.py', 'tools/panel/panel_passphrase.php')}
ADDITIONS['1.0.5'] = ADDITIONS['1.0.4']
ADDITIONS['1.0.6'] = ADDITIONS['1.0.5'] + ('tools/panel/docker_context.py', 'tools/panel/endpoint_settings.php')
ADDITIONS['1.0.7'] = ADDITIONS['1.0.6']
ADDITIONS['1.0.8'] = ADDITIONS['1.0.7']
ADDITIONS['1.0.9'] = ADDITIONS['1.0.8']
ADDITIONS['1.0.10'] = ADDITIONS['1.0.9']
ADDITIONS['1.0.11'] = ADDITIONS['1.0.10']
ADDITIONS['1.0.12'] = ADDITIONS['1.0.11']
ADDITIONS['1.0.13'] = ADDITIONS['1.0.12']
UPDATED_DOCS = ('README.md', 'PRODUCTION_DEPLOYMENT.md', 'docs/PANEL_TOOL.md', 'CHANGELOG.md', 'RELEASE_NOTES.md')
EXTRA_DOCS = ('ROOT_FIREWALL_TEST_REPORT.md', 'NATIVE_MACHINE_TEST_REPORT.md',
              'NATIVE_INSTALL_CHOICES_TEST_REPORT.md', 'NATIVE_DOMAIN_TEST_REPORT.md')


def build(base, output, version='1.0.11'):
    data = Path(base).read_bytes()
    if hashlib.sha256(data).hexdigest() != BASE_SHA256:
        raise ValueError('Base tools ZIP differs from the published v1.0.0 SHA-256')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
        manifest = dict(line.split('  ', 1)[::-1] for line in members['MANIFEST.sha256'].decode().splitlines())
        if set(manifest) != set(members) - {'MANIFEST.sha256'}:
            raise ValueError('Incomplete base manifest')
        if any(hashlib.sha256(members[name]).hexdigest() != digest for name, digest in manifest.items()):
            raise ValueError('Base manifest mismatch')
    replacements = REPLACEMENTS + (UPDATED_DOCS if version in ('1.0.3', '1.0.4', '1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13') else ())
    if version in ('1.0.4', '1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        replacements += ('SECURITY.md',)
    if version in ('1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        replacements += ('tools/node/release-lock.json', 'tools/node/audited-SHA256SUMS', 'docs/NODE_TOOL.md')
    for name in replacements:
        if name not in members:
            raise ValueError('Missing base module: ' + name)
        members[name] = (ROOT / name).read_bytes()
    # The standalone wizard pins this ZIP; including it would make the pin self-referential.
    if 'tools/panel/quick_install.py' in members or 'install.sh' in members:
        raise ValueError('Standalone pinned bootstrap must not be bundled inside its own tools ZIP')
    added = ADDITIONS[version] + (EXTRA_DOCS if version in ('1.0.3', '1.0.4', '1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13') else ())
    if version in ('1.0.4', '1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('CA_PASSPHRASE_TEST_REPORT.md',)
    if version in ('1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/FLOWSCOPE_PRIVATE_ENDPOINT_TEST_REPORT.md',)
    if version in ('1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/DOCKER_INSTALL.md',)
    if version in ('1.0.7', '1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/UPGRADE.zh-CN.md', 'docs/RATE_ROUTE_FIX_REPORT.md', 'docs/RATE_ROUTE_TEST_REPORT.md',
                  'docs/RELEASE_VALIDATION_20261025.md', 'docs/verification/node-v5-release-20261025.json',
                  'docs/verification/rate-route-fixed-20261024.json',
                  'docs/verification/rate-route-fixed-final-host-20261024.json',
                  'docs/verification/rate-route-20261024.json',
                  'docs/verification/rate-route-final-host-20261024.json')
    if version in ('1.0.8', '1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/NODE_MANAGEMENT_TEST_REPORT.md', 'docs/HYSTERIA2_AUTO_FORWARDING_REPORT.md',
                  'docs/RELEASE_VALIDATION_NODE_MENU_20261025.md', 'docs/verification/node-management-20261025.json',
                  'docs/verification/hysteria2-auto-forwarding-20261025.json',
                  'docs/verification/node-v6-release-20261025.json')
    if version in ('1.0.9', '1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/DEVICE_LIMIT_RELEASE_20261028.md', 'docs/RELEASE_VALIDATION_DEVICE_LIMIT_20261028.md')
    if version in ('1.0.10', '1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/HY2_INPUT_FIREWALL.zh-CN.md', 'docs/RELEASE_VALIDATION_HY2_INPUT_20261030.md',
                  'docs/WEB_MANAGER.zh-CN.md', 'docs/verification/node-v8-release-20261030.json')
    if version in ('1.0.11', '1.0.12', '1.0.13'):
        added += ('docs/RELEASE_VALIDATION_NODE_V9_20261030.md', 'docs/verification/node-v9-release-20261030.json')
    if version in ('1.0.12', '1.0.13'):
        added += ('docs/DEVICE_ADMISSION_ENTRY_FIX_20261030.md',)
    if version == '1.0.13':
        added += ('docs/ENTRY_REFRESH_CA_FIX_20261030.md',)
    for name in added:
        if name in members:
            raise ValueError('Expected a new tool module: ' + name)
        members[name] = (ROOT / name).read_bytes()
    members['MANIFEST.sha256'] = ''.join(
        hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
        for name, data in sorted(members.items()) if name != 'MANIFEST.sha256'
    ).encode()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ('node-endpoint-tools-' + version + '.zip')
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(members.items()):
            entry = zipfile.ZipInfo(name, (2026, 10, 17, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    return {'file': destination.name, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'bytes': destination.stat().st_size, 'base_sha256': BASE_SHA256, 'replaced_modules': list(replacements),
            'tools_version': version, 'added_modules': list(ADDITIONS[version]),
            'added_documents': list(added[len(ADDITIONS[version]):])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'dist/node-endpoint-tools-1.0.0.zip')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', choices=sorted(ADDITIONS), default='1.0.11')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output or ROOT / ('.runtime/tools-' + args.version), args.version)))
