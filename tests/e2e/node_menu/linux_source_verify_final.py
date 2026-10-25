"""Offline source verification; no panel data or private runtime log reads."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

BASE = Path('/opt/xboard-node-menu-source-20261025-r2')
CACHE = Path('/opt/xboard-rate-route-linux-fix-20261024')
UPLOAD = Path('/tmp/codex-node-menu-upload-20261025')
OWNER = {'tool': 'Codex isolated Node management source verification', 'path': str(BASE)}
def check(ok, reason):
    if not ok: raise RuntimeError(reason)

def main():
    os.umask(0o077)
    check(os.geteuid() == 0 and not BASE.exists(), 'fresh-root-source-fixture-required')
    meta = json.loads((UPLOAD / 'source-metadata.json').read_text())
    archive = UPLOAD / 'node-source.tar.gz'
    check(not archive.is_symlink() and hashlib.sha256(archive.read_bytes()).hexdigest() == meta['source_archive_sha256'], 'source-archive-sha-differs')
    check(json.loads((CACHE / 'owner.json').read_text()) == {'tool': 'Codex offline Node rate route race fixture', 'path': str(CACHE)}, 'offline-cache-owner-differs')
    BASE.mkdir(mode=0o700)
    (BASE / 'owner.json').write_text(json.dumps(OWNER))
    with tarfile.open(archive, 'r:gz') as tar:
        for item in tar:
            name = PurePosixPath(item.name)
            check(not name.is_absolute() and '..' not in name.parts and name.parts[0] == 'source' and item.isfile() and not item.issym() and not item.islnk(), 'unsafe-source-member')
            path = BASE.joinpath(*name.parts)
            check(not path.exists(), 'source-target-exists')
            path.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(item) as reader, path.open('xb') as writer: shutil.copyfileobj(reader, writer)
            path.chmod(0o755 if item.mode & 0o111 else 0o644)
    root = BASE / 'source'
    env = {'PATH': str(CACHE / 'go/bin') + ':/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': str(BASE),
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'GOMODCACHE': str(CACHE / 'gomodcache'),
           'GOCACHE': str(CACHE / 'gocache'), 'GOPATH': str(CACHE / 'gopath'),
           'GOPROXY': (CACHE / 'gomodcache/cache/download').as_uri() + ',off', 'GOSUMDB': 'off',
           'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOENV': 'off', 'CGO_ENABLED': '1',
           'GOMAXPROCS': '2', 'GOMEMLIMIT': '512MiB', 'GOGC': '50'}
    sys.path.insert(0, str(root / 'tools'))
    import kernel_patches
    env['GOFLAGS'] = kernel_patches.prepare(env, root) + ' -p=1'
    packages = ['./cmd/xbctl', './internal/config', './internal/controlplane', './internal/service', './internal/portforward']
    tags = 'with_quic,with_utls,with_wireguard,with_acme,with_clash_api'
    start = time.monotonic()
    result = subprocess.run(['go', 'test', '-race', '-count=1', '-tags', tags, *packages], cwd=root, env=env)
    report = {'result': 'PASS' if result.returncode == 0 else 'FAIL', 'exit_code': result.returncode,
              'packages': packages, 'tags': tags, 'duration_seconds': round(time.monotonic()-start, 2),
              'source_archive_sha256': meta['source_archive_sha256'], 'source_files': meta['source_files'],
              'toolchain': subprocess.check_output(['go','version'], env=env, text=True).strip(),
              'network': 'offline local module proxy only', 'private_runtime_logs_read': False, 'panel_data_accessed': False}
    target = Path('/tmp/codex-node-menu-source-result-20261025.json')
    target.write_text(json.dumps(report, indent=2) + '\n'); target.chmod(0o644)
    print(json.dumps(report), flush=True)
    raise SystemExit(result.returncode)

if __name__ == '__main__': main()
