"""Owned PTY + actual systemd/Node/SQLite panel tests. Never read runtime logs."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time

BASE=Path('/opt/xboard-node-menu-release-20261025')
UPLOAD=Path('/tmp/codex-node-menu-release-upload-20261025')
ACTIVE=BASE/'active'; PRIVATE=BASE/'private'; BIN=BASE/'bin'; STUB=BASE/'stubs'
UNIT='codex-node-menu-release-20261025.service'; UNITFILE=Path('/etc/systemd/system')/UNIT
PANEL=Path('/opt/xboard-hy2-auto-panel-20261025'); NAME='codex-xboard-hy2-auto-panel-20261025'
OWNER={'tool':'Codex isolated node management menu fixture','path':str(BASE),'date':'2026-10-25'}
ROWS=[]

def require(ok,reason):
    if not ok: raise RuntimeError(reason)
def run(args, data=None, check=True, timeout=40):
    result=subprocess.run(list(map(str,args)),input=data,capture_output=True,timeout=timeout)
    if check: require(result.returncode==0,'controlled-command-failed-'+Path(str(args[0])).name)
    return result
def write(path,data,mode=0o600):
    path.write_bytes(data.encode() if isinstance(data,str) else data);path.chmod(mode)
def save(path,value): write(path,json.dumps(value,indent=2)+'\n')
def owned():
    require(os.geteuid()==0 and BASE.resolve()==BASE and not BASE.is_symlink(),'owned-root-fixture-required')
    require(json.loads((BASE/'owner.json').read_text())==OWNER,'fixture-owner-differs')
def record(name,ok,**details):
    ROWS.append({'test':name,'pass':bool(ok),**details});save(PRIVATE/'partial-report.json',ROWS)
    print(json.dumps({'test':name,'pass':bool(ok)}),flush=True)
def service(action,*args,check=True): return run(['/usr/bin/systemctl',action,*args,UNIT],check=check)
def pid(): return int(service('show','--property=MainPID','--value').stdout.strip() or b'0')
def healthy():
    try:
        c=http.client.HTTPConnection('127.0.0.1',19631,timeout=.5);c.request('GET','/healthz');r=c.getresponse();v=r.status;r.read();c.close();return v==200
    except OSError: return False
def wait(predicate,label,timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.1)
    raise RuntimeError('fixture-timeout-'+label)

PHP=r'''
chdir('/www');require '/www/vendor/autoload.php';$app=require '/www/bootstrap/app.php';$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
$p=storage_path('app/private/hy2-auto-fixture-owner.json');$s=json_decode(file_get_contents($p),true,16,JSON_THROW_ON_ERROR);
if(($s['tool']??'')!=='Codex HY2 auto 20261025'||config('database.default')!=='sqlite'){exit(80);}
$m=App\Models\ServerMachine::findOrFail($s['machine_id']);if($m->name!=='Codex HY2 auto 20261025'){exit(81);}
$in=json_decode(stream_get_contents(STDIN),true);
if(($in['action']??'')==='rotate'){$m->token=Illuminate\Support\Str::random(32);$m->save();}
echo json_encode(['machine_id'=>$m->id,'token'=>$m->token,'ws_alive'=>(bool)Illuminate\Support\Facades\Cache::get('node_ws_alive:'.$s['node_id'])],JSON_THROW_ON_ERROR);
'''
PHP=PHP.replace(chr(92)*2,chr(92))
def php(action='read'):
    result=run(['/usr/bin/docker','exec','--user','1000:1000','--workdir','/www','-i',NAME,'php','-r',PHP],json.dumps({'action':action}).encode())
    return json.loads(result.stdout)

def adapted(source,version,node_sha,cli_sha):
    s=source
    require(version=='v1.13-orphan.6' and node_sha in s and cli_sha in s,'formal-pins-differ')
    replacements={
        'INSTALL_ROOT="/etc/xboard-node"':f'INSTALL_ROOT="{ACTIVE}"',
        'BINARY_PATH="/usr/local/bin/xboard-node"':f'BINARY_PATH="{BIN}/xboard-node"',
        'SERVICE_NAME="xboard-node.service"':f'SERVICE_NAME="{UNIT}"',
        'CLI_PATH="/usr/local/bin/xbctl"':f'CLI_PATH="{BIN}/xbctl"',
        'CLI_LINK_PATH="/usr/bin/xbctl"':f'CLI_LINK_PATH="{BIN}/xbctl-link"',
        'NODE_COMMAND_PATH="/usr/local/bin/node"':f'NODE_COMMAND_PATH="{BIN}/node"',
        '/etc/xboard-node/install.sh':str(ACTIVE/'install.sh'),
        '/run/lock/xboard-node-installer.lock':str(PRIVATE/'installer.lock'),
            }
    for before,after in replacements.items():
        require(before in s,'source-adaptation-anchor-missing');s=s.replace(before,after)
    return s

def prepare():
    require(os.geteuid()==0 and not BASE.exists() and not UNITFILE.exists(),'fresh-root-unit-fixture-required')
    require(json.loads((PANEL/'owner.json').read_text())=={'tool':'Codex Xboard HY2 auto panel fixture','path':str(PANEL),'date':'2026-10-25'},'panel-owner-differs')
    for port in [19631,42443]:
        typ=socket.SOCK_DGRAM if port==42443 else socket.SOCK_STREAM
        with socket.socket(socket.AF_INET,typ) as s:s.bind(('0.0.0.0',port))
    meta=json.loads((UPLOAD/'manifest.json').read_text())
    for name,digest in meta['files'].items():
        p=UPLOAD/name;require(p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,'uploaded-sha-differs')
    BASE.mkdir(mode=0o700)
    for p in [ACTIVE,PRIVATE,BIN,STUB,ACTIVE/'backups',PRIVATE/'remote']:p.mkdir(mode=0o700)
    save(BASE/'owner.json',OWNER);save(BASE/'manifest.json',meta)
    for name,target in [('xboard-node-linux-amd64','xboard-node'),('xbctl-linux-amd64','xbctl')]:shutil.copyfile(UPLOAD/name,BIN/target);(BIN/target).chmod(0o755)
    original=(UPLOAD/'install.sh').read_text()
    write(ACTIVE/'install.sh',adapted(original,'v1.13-orphan.6',meta['files']['xboard-node-linux-amd64'],meta['files']['xbctl-linux-amd64']),0o700)
    write(BIN/'node',f'#!/usr/bin/env bash\n# XBOARD_NODE_MENU_V1\nexec /bin/bash {ACTIVE}/install.sh manage\n',0o755)
    (BIN/'xbctl-link').symlink_to(BIN/'xbctl')
    credentials=php();key='MENU_FIXTURE_TOKEN'
    write(ACTIVE/'credentials.env',key+'='+credentials['token']+'\n')
    cfg={'id':'menu-fixture','panel':{'url':'http://127.0.0.1:17006'},'machine':{'machine_id':credentials['machine_id'],'token_env':key},
         'kernel':{'type':'singbox','config_dir':str(ACTIVE/'generated'),'log_level':'error'},
         'cert':{'cert_mode':'file','cert_file':'/opt/xboard-hy2-auto-20261025/server.pem','key_file':'/opt/xboard-hy2-auto-20261025/server.private.key'},
         'log':{'level':'warn','output':str(PRIVATE/'node.private.log')},'health_port':19631,'node':{'pull_interval':2,'push_interval':2}}
    save(ACTIVE/'config.yml',cfg);save(ACTIVE/'install-meta.json',{'version':'v1.13-orphan.6','instances':[{'id':'menu-fixture','panel_url':cfg['panel']['url'],'mode':'machine','machine_id':credentials['machine_id']}],'instance_count':1})
    ca=Path('/opt/xboard-hy2-auto-20261025/ca.pem').read_bytes();trust=Path('/etc/ssl/certs/ca-certificates.crt').read_bytes().rstrip()+b'\n'+ca
    write(ACTIVE/'node-endpoint-ca.pem',ca);write(ACTIVE/'node-endpoint-trust.pem',trust)
    fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca.decode())).hexdigest()
    save(ACTIVE/'node-endpoint-trust.json',{'tool':'NodeEndpoint native installer','schema':1,'url':cfg['panel']['url'],'fingerprints':[fingerprint],'ca_sha256':hashlib.sha256(ca).hexdigest(),'trust_sha256':hashlib.sha256(trust).hexdigest()})
    write(UNITFILE,f'[Unit]\nDescription=Codex isolated node-menu fixture 20261025\n[Service]\nType=simple\nEnvironmentFile={ACTIVE}/credentials.env\nExecStart={BIN}/xboard-node -c {ACTIVE}/config.yml\nKillMode=control-group\nTimeoutStopSec=20\nNoNewPrivileges=true\nStandardOutput=null\nStandardError=null\n[Install]\nWantedBy=multi-user.target\n',0o644)
    write(STUB/'journalctl','#!/bin/sh\nprintf "OWNED_LOG_SENTINEL %s\\n" "$*"\n',0o700)
    write(STUB/'apt-get','#!/bin/sh\nexit 0\n',0o700)
    write(STUB/'systemctl',f'#!/bin/bash\nif [[ "$1" == restart && -f "{PRIVATE}/fail-restart.once" ]]; then rm -f "{PRIVATE}/fail-restart.once"; exit 17; fi\nexec /usr/bin/systemctl "$@"\n',0o700)
    write(STUB/'curl',f'''#!/bin/bash
dest=''; url=''; original=("$@")
while [ "$#" -gt 0 ]; do case "$1" in -o|--output) dest="$2"; shift 2 ;; https://*) url="$1"; shift ;; *) shift ;; esac; done
case "$url" in
 https://api.github.com/repos/ksr-v/Xboard-Node--Custom-Source/releases/latest) file=release.json ;;
 https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.6/*) file="${{url##*/}}" ;;
 '') exec /usr/bin/curl "${{original[@]}}" ;;
 *) exit 84 ;;
esac
case "$file" in release.json|install.sh|SHA256SUMS|xboard-node-linux-amd64|xbctl-linux-amd64) cp "{PRIVATE}/remote/$file" "$dest" ;; *) exit 85 ;; esac
''',0o700)
    remote=PRIVATE/'remote';newnode=meta['files']['updated-xboard-node-linux-amd64'];newcli=meta['files']['updated-xbctl-linux-amd64']
    write(remote/'install.sh',adapted(original,'v1.13-orphan.6',newnode,newcli),0o700)
    for before,after in [('updated-xboard-node-linux-amd64','xboard-node-linux-amd64'),('updated-xbctl-linux-amd64','xbctl-linux-amd64')]:shutil.copyfile(UPLOAD/before,remote/after);(remote/after).chmod(0o700)
    sums=''.join(hashlib.sha256((remote/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in ['install.sh','xboard-node-linux-amd64','xbctl-linux-amd64'])
    write(remote/'SHA256SUMS',sums)
    assets=[{'name':name,'state':'uploaded','browser_download_url':'https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.6/'+name,'digest':'sha256:'+hashlib.sha256((remote/name).read_bytes()).hexdigest()} for name in ['install.sh','SHA256SUMS']]
    save(remote/'release.json',{'tag_name':'v1.13-orphan.6','draft':False,'prerelease':False,'assets':assets})
    run(['/usr/bin/systemctl','daemon-reload']);service('start');wait(healthy,'initial-health');wait(lambda:php()['ws_alive'],'initial-machine-ws')
    print(json.dumps({'result':'PASS','phase':'prepared','service':UNIT}),flush=True)

def snapshot():
    result={}
    for n in ['config.yml','credentials.env','install-meta.json','node-endpoint-ca.pem','node-endpoint-trust.pem','node-endpoint-trust.json']:
        data=(ACTIVE/n).read_bytes()
        if n=='node-endpoint-trust.json':data=json.dumps(json.loads(data),sort_keys=True).encode()
        result[n]=hashlib.sha256(data).hexdigest()
    return result
def menu(choice,url=None,token=None):
    env=dict(os.environ,PATH=str(STUB)+':'+str(BIN)+':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',TERM='xterm',LANG='C.UTF-8',LC_ALL='C.UTF-8')
    child,fd=pty.fork()
    if child==0:os.execve(str(BIN/'node'),[str(BIN/'node')],env)
    output=b'';sent_menu=sent_url=sent_token=False;deadline=time.monotonic()+50;status=None
    while time.monotonic()<deadline:
        ready,_,_=select.select([fd],[],[],.1)
        if ready:
            try:chunk=os.read(fd,65536)
            except OSError:chunk=b''
            output+=chunk
        if not sent_menu and '请选择 [0-6]'.encode() in output:os.write(fd,(str(choice)+'\n').encode());sent_menu=True
        if url is not None and not sent_url and '新面板地址'.encode() in output:os.write(fd,(url+'\n').encode());sent_url=True
        if token is not None and not sent_token and '新 Token'.encode() in output:os.write(fd,(token+'\n').encode());sent_token=True
        got,code=os.waitpid(child,os.WNOHANG)
        if got:status=os.waitstatus_to_exitcode(code);break
    if status is None:
        os.kill(child,signal.SIGKILL);os.waitpid(child,0);status=124
    os.close(fd)
    write(PRIVATE/('terminal-'+str(time.time_ns())+'.private'),output)
    return {'exit':status,'token_visible':bool(token) and token.encode() in output,'log_sentinel':b'OWNED_LOG_SENTINEL' in output,'output':output}

def tests():
    owned();before_rules=hashlib.sha256(run(['nft','-s','list','ruleset']).stdout).hexdigest()
    try:
        before=snapshot();oldpid=pid();r=menu(4,'','')
        record('empty-edit-cancels-without-restart',r['exit']==0 and snapshot()==before and pid()==oldpid)
        r=menu(5);record('log-option-is-bounded-synthetic-output',r['exit']==0 and r['log_sentinel'],actual_root_logs_read=False)
        oldpid=pid();r=menu(2);wait(healthy,'restart-health');wait(lambda:php()['ws_alive'],'restart-ws')
        record('menu-restarts-real-owned-node',r['exit']==0 and pid()!=oldpid)
        before=snapshot();oldpid=pid();r=menu(4,'http://localhost:17006','deliberately-invalid-fixture-token')
        record('wrong-token-preflight-preserves-running-install',r['exit']!=0 and pid()==oldpid and snapshot()==before and not r['token_visible'])
        before=snapshot();r=menu(4,'https://127.0.0.1:17007','')
        record('unreachable-panel-preserves-running-install',r['exit']!=0 and pid()==oldpid and snapshot()==before)
        credentials=php('rotate');before=snapshot();oldpid=pid();r=menu(4,'http://localhost:17006',credentials['token']);wait(healthy,'retarget-health');wait(lambda:php()['ws_alive'],'retarget-ws')
        after=snapshot();record('address-and-token-change-authenticates-and-restarts',r['exit']==0 and pid()!=oldpid and after['config.yml']!=before['config.yml'] and after['credentials.env']!=before['credentials.env'] and not r['token_visible'],token_echoed=r['token_visible'])
        inspect=json.loads(run([BIN/'xbctl','config','inspect','--config',ACTIVE/'config.yml']).stdout)
        record('binding-id-machine-and-ca-preserved',inspect['instances'][0]['id']=='menu-fixture' and inspect['instances'][0]['machine_id']==credentials['machine_id'] and all(before[n]==after[n] for n in before if n.startswith('node-endpoint-')))
        before=snapshot();write(PRIVATE/'fail-restart.once','fail once\n');r=menu(4,'http://127.0.0.1:17006','');wait(healthy,'rollback-health');wait(lambda:php()['ws_alive'],'rollback-ws')
        record('failed-restart-restores-all-active-files',r['exit']!=0 and snapshot()==before)
        before=snapshot();oldscript=hashlib.sha256((ACTIVE/'install.sh').read_bytes()).hexdigest();r=menu(1);wait(healthy,'upgrade-health');wait(lambda:php()['ws_alive'],'upgrade-ws')
        new_version=run([BIN/'xboard-node','-v']).stdout.decode().strip();after=snapshot()
        record('online-release-verifies-and-updates-real-binaries',r['exit']==0 and 'v1.13-orphan.6' in new_version and hashlib.sha256((BIN/'xboard-node').read_bytes()).hexdigest()==json.loads((BASE/'manifest.json').read_text())['formal_release_assets']['xboard-node-linux-amd64'],network_source='controlled latest-release transport; exact formal binaries, installer changes only fixture paths')
        record('online-update-retains-binding-health-and-private-ca',before==after and healthy() and (BIN/'node').is_file())
        p=PRIVATE/'remote/install.sh';original=p.read_bytes();p.write_bytes(original+b'\n# tampered\n');before=snapshot();oldpid=pid();r=menu(1)
        record('tampered-online-updater-never-replaces-install',r['exit']!=0 and pid()==oldpid and snapshot()==before)
        p.write_bytes(original)
        service('stop');before=snapshot();r=menu(4,'http://127.0.0.1:17006','')
        record('editing-stopped-service-does-not-start-it',r['exit']==0 and not service('is-active',check=False).returncode==0 and not healthy())
        permissions=all((ACTIVE/n).stat().st_mode&0o777==0o600 for n in ['config.yml','credentials.env','node-endpoint-ca.pem','node-endpoint-trust.pem','node-endpoint-trust.json'])
        record('private-files-and-backups-have-required-permissions',permissions and all(p.stat().st_mode&0o777==0o700 for p in (ACTIVE/'backups').iterdir() if p.is_dir()))
    finally:
        service('stop',check=False)
        after_rules=hashlib.sha256(run(['nft','-s','list','ruleset']).stdout).hexdigest()
        require(before_rules==after_rules,'host-rules-changed')
        report={'result':'PASS' if len(ROWS)==13 and all(r['pass'] for r in ROWS) else 'FAIL','tests':ROWS,'host_nftables_unchanged':before_rules==after_rules,'private_runtime_logs_read':False,'service_stopped':service('is-active',check=False).returncode!=0,'candidate':json.loads((BASE/'manifest.json').read_text()),'formal_binary_assets_unchanged':True,'installer_adaptation':'owned filesystem paths only; version and binary SHA-256 pins unchanged'}
        save(BASE/'report.json',report);out=Path('/tmp/codex-node-menu-release-result-20261025.json');save(out,report);out.chmod(0o644)
        print(json.dumps({'result':report['result'],'checks':len(ROWS),'failed':[r['test'] for r in ROWS if not r['pass']]}),flush=True)

def cleanup():
    owned();require(UNITFILE.is_file() and not UNITFILE.is_symlink() and ('ExecStart='+str(BIN/'xboard-node')) in UNITFILE.read_text(),'unit-owner-differs')
    service('stop',check=False);service('disable',check=False);UNITFILE.unlink();run(['/usr/bin/systemctl','daemon-reload'])
    c=http.client.HTTPConnection('127.0.0.1',17001,timeout=5);c.request('GET','/');r=c.getresponse();status=r.status;r.read(1024);c.close();require(status==200,'original-panel-not-healthy')
    print(json.dumps({'result':'PASS','unit_removed':True,'original_panel_http_status':status,'private_test_data_retained':True}))


def smoke():
    owned();manifest=json.loads((BASE/'manifest.json').read_text());rows=[]
    for name in ['xboard-node-linux-amd64','xbctl-linux-amd64','xboard-node-linux-arm64','xbctl-linux-arm64']:
        path=UPLOAD/name;data=path.read_bytes();arm=name.endswith('arm64')
        require(hashlib.sha256(data).hexdigest()==manifest['formal_release_assets'][name],'formal-smoke-hash-differs')
        require(data[:4]==b'\x7fELF' and int.from_bytes(data[18:20],'little')==(183 if arm else 62),'formal-smoke-architecture-differs')
        argv=([UPLOAD/'qemu-aarch64-static'] if arm else [])+[path,'-v' if name.startswith('xboard-node') else 'version']
        result=run(argv,timeout=45)
        require(b'v1.13-orphan.6' in result.stdout,'formal-smoke-version-differs')
        rows.append({'asset':name,'sha256':hashlib.sha256(data).hexdigest(),'execution':'QEMU-aarch64' if arm else 'native-linux-amd64','version':result.stdout.decode().strip(),'pass':True})
    report={'result':'PASS','version':'v1.13-orphan.6','checks':rows,'native_arm64_functional_test':False}
    save(BASE/'formal-smoke.json',report);out=Path('/tmp/codex-node-menu-release-smoke-20261025.json');save(out,report);out.chmod(0o644)
    print(json.dumps(report),flush=True)

if __name__=='__main__':
    os.umask(0o077)
    try:
        require(sys.argv[1:] in [['prepare'],['run'],['cleanup'],['smoke']],'fixture-action-required')
        {'prepare':prepare,'run':tests,'cleanup':cleanup,'smoke':smoke}[sys.argv[1]]()
    except Exception as e:
        import traceback
        print(json.dumps({'result':'HARNESS_ERROR','type':type(e).__name__,'reason':str(e) if isinstance(e,RuntimeError) else 'controlled-fixture-error','frames':[{'function':f.name,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__) if f.filename==__file__]}),flush=True)
        raise SystemExit(1)
