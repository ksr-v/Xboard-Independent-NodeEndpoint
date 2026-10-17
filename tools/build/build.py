#!/usr/bin/env python3
"""Deterministic local releases; no network, publishing or credentials."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
VERSION = '1.0.0'
DOCS = ['README.md', 'ARCHITECTURE.md', 'SECURITY.md', 'IMPLEMENTATION_PLAN.md',
        'TEST_REPORT.md', 'VM_TEST_REPORT.md', 'FULL_E2E_TEST_REPORT.md', 'PRODUCTION_DEPLOYMENT.md', 'CHANGELOG.md', 'RELEASE_NOTES.md', 'LICENSE']

def files(directory):
    return sorted(p for p in directory.rglob('*') if p.is_file()
                  and not any(x in ('__pycache__', 'vendor', 'runtime', 'work', 'node_modules', '.pytest_cache') for x in p.parts)
                  and p.suffix not in ('.pyc', '.sqlite', '.log'))

def archive(name, members):
    destination = ROOT / 'dist' / name
    targets = [target for _, target in members]
    if len(set(targets)) != len(targets) or any(target.startswith('/') or '..' in target.split('/') for target in targets):
        raise RuntimeError('Unsafe or duplicate archive path')
    prefix = 'NodeEndpoint/' if name.startswith('NodeEndpoint-') else ''
    manifest = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + target[len(prefix):] + '\n'
                       for path, target in sorted(members, key=lambda entry: entry[1]))
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path, target in sorted(members, key=lambda entry: entry[1]):
            if path.is_symlink():
                raise RuntimeError('Symlinks are not release members')
            info = zipfile.ZipInfo(target, (2026, 10, 9, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix == '.sh' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo(prefix + 'MANIFEST.sha256', (2026, 10, 9, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        bundle.writestr(info, manifest.encode('utf-8'))
    with zipfile.ZipFile(destination) as bundle:
        if bundle.testzip():
            raise RuntimeError('Archive CRC verification failed')
    return {'file': name, 'version': VERSION, 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}

def main():
    (ROOT / 'dist').mkdir(exist_ok=True)
    for name in DOCS:
        if not (ROOT / name).is_file():
            raise RuntimeError('Missing required delivery document: ' + name)
    plugin_root = ROOT / 'plugin' / 'NodeEndpoint'
    config = json.loads((plugin_root / 'config.json').read_text('utf-8'))
    assert config['version'] == VERSION and config['code'] == 'node_endpoint'
    plugin = [(p, 'NodeEndpoint/' + p.relative_to(plugin_root).as_posix()) for p in files(plugin_root)]
    toolset = [(p, p.relative_to(ROOT).as_posix()) for p in files(ROOT / 'tools')]
    documents = [(ROOT / name, name) for name in DOCS] + [(p, p.relative_to(ROOT).as_posix()) for p in files(ROOT / 'docs')]
    # Include Root/Node tools and documents in the native upload package as well.
    plugin += [(p, 'NodeEndpoint/' + name) for p, name in toolset + documents]
    sources = [(p, p.relative_to(ROOT).as_posix()) for p in files(ROOT / 'plugin')] + toolset + documents
    sources += [(p, p.relative_to(ROOT).as_posix()) for p in files(ROOT / 'tests')
                if not any(x in ('fixture', 'tmp', 'preview') for x in p.relative_to(ROOT / 'tests').parts)]
    sources.append((ROOT / '.gitignore', '.gitignore'))
    reports = [archive('NodeEndpoint-' + VERSION + '.zip', plugin),
               archive('node-endpoint-tools-' + VERSION + '.zip', toolset + documents),
               archive('xb-node-endpoint-' + VERSION + '-source.zip', sources)]
    (ROOT / 'dist' / 'SHA256SUMS').write_text(''.join(row['sha256'] + '  ' + row['file'] + '\n' for row in reports), encoding='utf-8')
    (ROOT / 'dist' / 'release.json').write_text(json.dumps({'version': VERSION, 'artifacts': reports}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(reports, indent=2))

if __name__ == '__main__':
    main()
