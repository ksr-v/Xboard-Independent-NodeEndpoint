"""Owned, offline Linux race verification; no panel credentials or log reads."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

CASE=Path('/opt/xboard-rate-route-linux-fix-20261024')
OWNER={'tool':'Codex offline Node rate route race fixture','path':str(CASE)}
META=Path('/tmp/linux-verification-metadata.json')


def require(ok,reason):
    if not ok:raise RuntimeError(reason)


def extract(package,destination,digest):
    source=Path(package)
    require(hashlib.sha256(source.read_bytes()).hexdigest()==digest,'archive-checksum-differs')
    with tarfile.open(source,'r:gz') as tar:
        for item in tar:
            name=PurePosixPath(item.name)
            require(not name.is_absolute() and '..' not in name.parts and not item.issym() and not item.islnk(),'unsafe-archive-entry')
            path=destination.joinpath(*name.parts)
            if item.isdir():path.mkdir(parents=True,exist_ok=True,mode=0o755)
            else:
                require(item.isfile() and not path.exists(),'unexpected-archive-object')
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                with tar.extractfile(item) as reader,path.open('xb') as writer:shutil.copyfileobj(reader,writer)
                path.chmod(0o755 if item.mode&0o111 else 0o644)


def prepare():
    require(os.geteuid()==0 and not CASE.exists(),'root-fresh-dedicated-fixture-required')
    meta=json.loads(META.read_text())
    CASE.mkdir(mode=0o700)
    (CASE/'owner.json').write_text(json.dumps(OWNER))
    (CASE/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    extract('/tmp/node-fixed-source.tar.gz',CASE,meta['source_archive_sha256'])
    extract('/tmp/module-download-cache.tar.gz',CASE,meta['module_cache_sha256'])
    extract('/tmp/go1.26.5.linux-amd64.tar.gz',CASE,meta['go_archive_sha256'])
    print(json.dumps({'result':'PASS','phase':'prepared','source_archive_sha256':meta['source_archive_sha256']}),flush=True)


def owned():
    require(os.geteuid()==0 and CASE.resolve()==CASE and json.loads((CASE/'owner.json').read_text())==OWNER,'ownership-differs')


def run():
    owned()
    root=CASE/'source'
    env={'PATH':str(CASE/'go/bin')+':/usr/bin:/bin','HOME':str(CASE),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
         'GOMODCACHE':str(CASE/'gomodcache'),'GOCACHE':str(CASE/'gocache'),'GOPATH':str(CASE/'gopath'),
         'GOPROXY':(CASE/'gomodcache/cache/download').as_uri()+',off','GOSUMDB':'off','GOTOOLCHAIN':'local',
         'GOWORK':'off','GOENV':'off','CGO_ENABLED':'1','GOMAXPROCS':'2','GOMEMLIMIT':'512MiB','GOGC':'50'}
    sys.path.insert(0,str(root/'tools'))
    import kernel_patches
    env['GOFLAGS']=kernel_patches.prepare(env,root)+' -p=1'
    version=subprocess.check_output(['go','version'],env=env,text=True).strip()
    started=time.monotonic()
    # These are source unit tests with only generated/test identities. Output
    # streams directly; no Root-private runtime or installation log is read.
    command=['go','test','-race','-count=1','./internal/limiter','./internal/kernel/singbox','./internal/kernel/xray','./internal/service']
    result=subprocess.run(command,cwd=root,env=env)
    report={'result':'PASS' if result.returncode==0 else 'FAIL','phase':'linux-race','exit_code':result.returncode,
            'toolchain':version,'source_archive_sha256':json.loads((CASE/'metadata.json').read_text())['source_archive_sha256'],
            'duration_seconds':round(time.monotonic()-started,2),'packages':command[4:],
            'extra_test_sha256':hashlib.sha256((root/'internal/kernel/singbox/low_speed_stream_test.go').read_bytes()).hexdigest(),
            'go_source_cache_network':'off after local module file proxy','panel_data_accessed':False}
    (CASE/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    public=Path('/tmp/codex-rate-route-linux-race-fixed-20261024.json')
    public.write_text(json.dumps(report,indent=2)+'\n');public.chmod(0o644)
    print(json.dumps(report),flush=True)
    raise SystemExit(result.returncode)


if __name__=='__main__':
    try:
        os.umask(0o077)
        require(sys.argv[1:] in [['prepare'],['run']],'unknown-phase')
        globals()[sys.argv[1]]()
    except Exception as error:
        print(json.dumps({'result':'HARNESS_ERROR','type':type(error).__name__,'reason':str(error) if isinstance(error,RuntimeError) else 'offline-fixture-failed'}),flush=True)
        raise SystemExit(1)
