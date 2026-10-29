#!/usr/bin/env python3
"""Check that the current native and supplementary packages carry one exact Node lock."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    lock_bytes = (ROOT / 'tools/node/release-lock.json').read_bytes()
    sums_bytes = (ROOT / 'tools/node/audited-SHA256SUMS').read_bytes()
    lock = json.loads(lock_bytes)
    binary_assets = {'xboard-node-linux-amd64', 'xboard-node-linux-arm64',
                     'xbctl-linux-amd64', 'xbctl-linux-arm64', 'install.sh'}
    if lock['version'] != 'v1.13-orphan.8' or lock['repository'] != 'https://github.com/ksr-v/Xboard-Node--Custom-Source':
        raise RuntimeError('Unexpected Node release identity')
    for key in ('source_commit', 'binary_source_commit'):
        if not re.fullmatch('[0-9a-f]{40}', lock[key]):
            raise RuntimeError('Missing exact source identity: ' + key)
    if set(lock['assets']) != binary_assets | {'SHA256SUMS'}:
        raise RuntimeError('Incomplete release asset lock')
    if any(not re.fullmatch('[0-9a-f]{64}', value) for value in lock['assets'].values()):
        raise RuntimeError('Invalid asset SHA-256')
    sums = {}
    for line in sums_bytes.decode('ascii').splitlines():
        if not line:
            continue
        digest, name = line.split('  ', 1)
        if name in sums:
            raise RuntimeError('Duplicate audited asset')
        sums[name] = digest
    if set(sums) != binary_assets or any(lock['assets'][name] != digest for name, digest in sums.items()):
        raise RuntimeError('Audited asset list differs from release lock')
    if hashlib.sha256(sums_bytes).hexdigest() != lock['assets']['SHA256SUMS']:
        raise RuntimeError('Audited SHA256SUMS bytes differ from locked release asset')

    version = json.loads((ROOT / 'plugin/NodeEndpoint/config.json').read_text('utf-8'))['version']
    directory = ROOT / 'dist' / ('v' + version)
    rows = []
    for name, prefix in [('NodeEndpoint-' + version + '.zip', 'NodeEndpoint/'),
                         ('node-endpoint-tools-1.0.10.zip', '')]:
        with zipfile.ZipFile(directory / name) as archive:
            if archive.read(prefix + 'tools/node/release-lock.json') != lock_bytes:
                raise RuntimeError('Packaged release lock differs: ' + name)
            if archive.read(prefix + 'tools/node/audited-SHA256SUMS') != sums_bytes:
                raise RuntimeError('Packaged audited sums differ: ' + name)
            if prefix:
                plugin = ROOT / 'plugin/NodeEndpoint'
                for path in sorted(plugin.rglob('*')):
                    if path.is_file() and path.suffix != '.pyc':
                        if archive.read(prefix + path.relative_to(plugin).as_posix()) != path.read_bytes():
                            raise RuntimeError('Packaged plugin source differs: ' + path.name)
            rows.append({'file': name, 'node_version': lock['version'], 'result': 'PASS'})
    print(json.dumps({'result': 'PASS', 'checks': rows}, indent=2))


if __name__ == '__main__':
    main()
