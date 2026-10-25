import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

work = Path('D:/xb-node-endpoint')
root = work / '.runtime/native-node-source'
out = work / '.runtime/node-menu-20261025'
names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
files = sorted({name for name in names if name and (root / name).is_file()})
archive = out / 'node-source.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    for name in files:
        path = root / name
        assert not path.is_symlink()
        tar.add(path, arcname='source/' + name, recursive=False)
meta = {'source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'source_files': len(files), 'base_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
        'changed_sources': [name for name in files if name.startswith('internal/portforward/')],
        'candidate': 'Node management menu; uncommitted working-tree candidate'}
(out / 'source-metadata.json').write_bytes((json.dumps(meta, indent=2) + '\n').encode())
print(json.dumps(meta))
