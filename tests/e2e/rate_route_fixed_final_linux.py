"""Offline final source verification in the existing owned Linux fixture."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

CASE = Path('/opt/xboard-rate-route-linux-fix-20261024')
OWNER = {'tool': 'Codex offline Node rate route race fixture', 'path': str(CASE)}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def main():
    require(os.geteuid() == 0 and CASE.resolve() == CASE, 'owned-root-fixture-required')
    require(json.loads((CASE / 'owner.json').read_text()) == OWNER, 'ownership-differs')
    archive = Path('/tmp/codex-node-final-source-20261024.tar.gz')
    meta = json.loads(Path('/tmp/codex-node-final-metadata-20261024.json').read_text())
    require(not archive.is_symlink() and hashlib.sha256(archive.read_bytes()).hexdigest() == meta['source_archive_sha256'], 'source-checksum-differs')
    root = CASE / 'source-final-r2'
    require(not root.exists(), 'final-source-directory-already-exists')
    with tarfile.open(archive, 'r:gz') as tar:
        for item in tar:
            name = PurePosixPath(item.name)
            require(not name.is_absolute() and '..' not in name.parts and name.parts[0] == 'source-final-r2', 'unsafe-archive-path')
            require(item.isfile() and not item.issym() and not item.islnk(), 'unsafe-archive-object')
            destination = CASE.joinpath(*name.parts)
            require(not destination.exists(), 'existing-archive-target')
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with tar.extractfile(item) as reader, destination.open('xb') as writer:
                shutil.copyfileobj(reader, writer)
            destination.chmod(0o755 if item.mode & 0o111 else 0o644)
    env = {'PATH': str(CASE / 'go/bin') + ':/usr/bin:/bin', 'HOME': str(CASE),
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'GOMODCACHE': str(CASE / 'gomodcache'),
           'GOCACHE': str(CASE / 'gocache'), 'GOPATH': str(CASE / 'gopath'),
           'GOPROXY': (CASE / 'gomodcache/cache/download').as_uri() + ',off', 'GOSUMDB': 'off',
           'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOENV': 'off', 'CGO_ENABLED': '1',
           'GOMAXPROCS': '2', 'GOMEMLIMIT': '512MiB', 'GOGC': '50'}
    sys.path.insert(0, str(root / 'tools'))
    import kernel_patches
    env['GOFLAGS'] = kernel_patches.prepare(env, root) + ' -p=1'
    started = time.monotonic()
    packages = ['./internal/limiter', './internal/kernel/singbox', './internal/kernel/xray', './internal/service']
    result = subprocess.run(['go', 'test', '-race', '-count=1', *packages], cwd=root, env=env)
    report = {'result': 'PASS' if result.returncode == 0 else 'FAIL', 'phase': 'final-source-linux-race',
              'exit_code': result.returncode, 'toolchain': subprocess.check_output(['go', 'version'], env=env, text=True).strip(),
              'source_archive_sha256': meta['source_archive_sha256'], 'source_files': meta['source_files'],
              'duration_seconds': round(time.monotonic() - started, 2), 'packages': packages,
              'network': 'offline local module proxy only', 'panel_data_accessed': False,
              'private_runtime_logs_read': False}
    public = Path('/tmp/codex-rate-route-final-source-linux-r2-20261024.json')
    public.write_text(json.dumps(report, indent=2) + '\n')
    public.chmod(0o644)
    print(json.dumps(report), flush=True)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    os.umask(0o077)
    main()
