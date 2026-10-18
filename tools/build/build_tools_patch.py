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
REPLACEMENTS = ('tools/panel/nginx.py', 'tools/panel/manager.py')
ADDITIONS = {'1.0.1': (), '1.0.2': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php'),
             '1.0.3': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php', 'tools/panel/firewall.py'),
             '1.0.4': ('tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php', 'tools/panel/firewall.py',
                       'tools/panel/panel_passphrase.py', 'tools/panel/panel_passphrase.php')}
UPDATED_DOCS = ('README.md', 'PRODUCTION_DEPLOYMENT.md', 'docs/PANEL_TOOL.md', 'CHANGELOG.md', 'RELEASE_NOTES.md')
EXTRA_DOCS = ('ROOT_FIREWALL_TEST_REPORT.md', 'NATIVE_MACHINE_TEST_REPORT.md',
              'NATIVE_INSTALL_CHOICES_TEST_REPORT.md', 'NATIVE_DOMAIN_TEST_REPORT.md')


def build(base, output, version='1.0.4'):
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
    replacements = REPLACEMENTS + (UPDATED_DOCS if version in ('1.0.3', '1.0.4') else ())
    if version == '1.0.4':
        replacements += ('SECURITY.md',)
    for name in replacements:
        if name not in members:
            raise ValueError('Missing base module: ' + name)
        members[name] = (ROOT / name).read_bytes()
    # The standalone wizard pins this ZIP; including it would make the pin self-referential.
    if 'tools/panel/quick_install.py' in members or 'install.sh' in members:
        raise ValueError('Standalone pinned bootstrap must not be bundled inside its own tools ZIP')
    added = ADDITIONS[version] + (EXTRA_DOCS if version in ('1.0.3', '1.0.4') else ())
    if version == '1.0.4':
        added += ('CA_PASSPHRASE_TEST_REPORT.md',)
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
            'added_documents': (list(EXTRA_DOCS) + (['CA_PASSPHRASE_TEST_REPORT.md'] if version == '1.0.4' else [])) if version in ('1.0.3', '1.0.4') else []}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'dist/node-endpoint-tools-1.0.0.zip')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', choices=sorted(ADDITIONS), default='1.0.4')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output or ROOT / ('.runtime/tools-' + args.version), args.version)))
