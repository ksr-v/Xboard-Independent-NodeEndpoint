#!/usr/bin/env python3
"""Verify local release hashes, archive safety and every member manifest."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {'NodeEndpoint-1.0.0.zip', 'node-endpoint-tools-1.0.0.zip', 'xb-node-endpoint-1.0.0-source.zip'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-only', action='store_true', help='Verify current versioned native-plugin release')
    parser.add_argument('--with-tools', action='store_true', help='Also verify the current supplementary tools beside the native plugin')
    args = parser.parse_args()
    if args.with_tools and not args.plugin_only:
        parser.error('--with-tools requires --plugin-only')
    version = json.loads((ROOT / 'plugin/NodeEndpoint/config.json').read_text('utf-8'))['version'] if args.plugin_only else '1.0.0'
    directory = ROOT / 'dist' / ('v' + version) if args.plugin_only else ROOT / 'dist'
    expected = {'NodeEndpoint-' + version + '.zip'} if args.plugin_only else EXPECTED
    if args.with_tools:
        expected.add('node-endpoint-tools-1.0.11.zip')
    release = json.loads((directory / 'release.json').read_text('utf-8'))
    if release.get('version') != version or {row['file'] for row in release['artifacts']} != expected:
        raise RuntimeError('Release identity differs')
    reports = []
    sums = []
    for row in release['artifacts']:
        artifact = directory / row['file']
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual != row['sha256'] or artifact.stat().st_size != row['bytes'] or row['version'] != release['version']:
            raise RuntimeError('Artifact metadata/hash differs: ' + row['file'])
        sums.append(actual + '  ' + row['file'] + '\n')
        prefix = 'NodeEndpoint/' if row['file'].startswith('NodeEndpoint-') else ''
        with zipfile.ZipFile(artifact) as bundle:
            names = bundle.namelist()
            if bundle.testzip() or len(set(names)) != len(names):
                raise RuntimeError('Corrupt or duplicate ZIP member')
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or not name.startswith(prefix):
                    raise RuntimeError('Unsafe ZIP path')
                if any(part in ('.audit', '.runtime', '.git', 'vendor', 'work', 'node_modules', '__pycache__') for part in path.parts):
                    raise RuntimeError('Development/private directory in release')
                if path.name in ('.env', 'credentials.env', 'fixture-auth.json') or path.suffix in ('.key', '.sqlite', '.pyc', '.log'):
                    raise RuntimeError('Private/runtime file in release')
                if re.search(rb'^-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----', bundle.read(name), re.MULTILINE):
                    raise RuntimeError('Private key in release')
            manifest_name = prefix + 'MANIFEST.sha256'
            members = {}
            for line in bundle.read(manifest_name).decode('utf-8').splitlines():
                digest, target = line.split('  ', 1)
                if not re.fullmatch(r'[0-9a-f]{64}', digest) or prefix + target in members:
                    raise RuntimeError('Invalid member manifest')
                members[prefix + target] = digest
            if set(members) != set(names) - {manifest_name}:
                raise RuntimeError('Incomplete member manifest')
            for name, digest in members.items():
                if hashlib.sha256(bundle.read(name)).hexdigest() != digest:
                    raise RuntimeError('Member SHA-256 differs')
            if prefix:
                config = json.loads(bundle.read(prefix + 'config.json'))
                if config['code'] != 'node_endpoint' or config['version'] != version:
                    raise RuntimeError('Native plugin metadata differs')
            reports.append({'file': row['file'], 'members': len(names), 'sha256': actual, 'result': 'PASS'})
    if (directory / 'SHA256SUMS').read_text('utf-8') != ''.join(sums):
        raise RuntimeError('SHA256SUMS differs from release metadata')
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
