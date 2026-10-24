"""Final Laravel unit scenarios using an existing offline test image."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

case = Path('/opt/xboard-rate-route-linux-fix-20261024')
assert os.geteuid() == 0 and case.resolve() == case
assert json.loads((case / 'owner.json').read_text()) == {'tool': 'Codex offline Node rate route race fixture', 'path': str(case)}
archive = Path('/tmp/codex-backend-final-source-20261024.tar.gz')
meta = json.loads(Path('/tmp/codex-backend-final-metadata-20261024.json').read_text())
assert not archive.is_symlink() and hashlib.sha256(archive.read_bytes()).hexdigest() == meta['source_archive_sha256']
root = case / 'backend-final'
assert not root.is_symlink()
os.umask(0o077)
with tarfile.open(archive, 'r:gz') as tar:
    for item in tar:
        name = PurePosixPath(item.name)
        assert not name.is_absolute() and '..' not in name.parts and name.parts[0] == 'backend-final'
        assert item.isfile() and not item.issym() and not item.islnk()
        target = case.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if target.exists():
            assert not target.is_symlink()
            with tar.extractfile(item) as reader:
                assert target.read_bytes() == reader.read(), 'existing-source-content-differs'
        else:
            with tar.extractfile(item) as reader, target.open('xb') as writer:
                shutil.copyfileobj(reader, writer)
        target.chmod(0o644)
image = 'sha256:fd4991a3653e3c57dc71e9e2d6e79f3bbd7cf4cfd7cde048cfdbada4736a456f'
subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], check=True, stdout=subprocess.DEVNULL)
command = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--user', '0:0',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m',
           '-e', 'XBOARD_TEST_VENDOR=/www/vendor/autoload.php',
           '--mount', 'type=bind,src=' + str(root) + ',dst=/verification,readonly',
           '--mount', 'type=bind,src=' + str(root / 'app') + ',dst=/www/app,readonly',
           '--entrypoint', 'php', image, '/verification/tests/RateRoute/synchronization.php']
result = subprocess.run(command, capture_output=True, text=True, timeout=90)
passes = [line for line in result.stdout.splitlines() if line.startswith('PASS ')]
if result.returncode != 0:
    print(json.dumps({'result': 'UNIT_TEST_FAILURE', 'stdout': result.stdout[-3000:], 'stderr': result.stderr[-3000:]}))
assert result.returncode == 0 and len(passes) == 14, 'backend-final-scenarios-failed'
report = {'result': 'PASS', 'phase': 'final-backend-linux-docker', 'scenarios': 13,
          'source_archive_sha256': meta['source_archive_sha256'], 'source_files': meta['source_files'],
          'image': image, 'network': 'none', 'database': 'fresh in-memory SQLite',
          'production_data_accessed': False, 'private_runtime_logs_read': False,
          'checks': passes[:-1]}
public = Path('/tmp/codex-rate-route-final-backend-20261024.json')
public.write_text(json.dumps(report, indent=2) + '\n')
public.chmod(0o644)
print(json.dumps(report))
