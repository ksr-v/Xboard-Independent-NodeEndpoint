import hashlib,json,os,pathlib,sys,time
b=pathlib.Path('/opt/node-endpoint-e2e-20261010'); assert os.geteuid()==0 and (b/'.node-endpoint-e2e-owner').is_file()
sys.path.insert(0,str(b/'tests/e2e')); import panel_control as c; import panel_endpoint as e
d=c.read_json(b/'panel-control.json'); meta=e.private_meta()
assert c.read_json(b/'e2e-20261010-state.json')['status']=='cleaned'
assert c.read_json(b/'e2e-20261010-auth.json')['status']=='restored'
assert c.read_json(b/'e2e-20261010-plugin-state.json')['status']=='cleaned'
resume_file_meta={}; metadata_changes=[]
for i in d['snapshots']:
 if 'storage/logs/xboard-ws-server.' in i['path']: continue
 p=pathlib.Path(i['path']); x=p.stat(); observed=(x.st_uid,x.st_gid,x.st_mode&0o7777)
 assert p.is_file() and not p.is_symlink() and c.digest(p.read_bytes())==i['sha256']
 assert not x.st_mode&0o022
 resume_file_meta[str(p)]=observed
 if observed!=(i['uid'],i['gid'],i['mode']): metadata_changes.append({'path':str(p),'before':[i['uid'],i['gid'],i['mode']],'resumed':list(observed)})
def static_guard():
 for item in d['snapshots']:
  if 'storage/logs/xboard-ws-server.' in item['path']: continue
  p=pathlib.Path(item['path']); x=p.stat()
  assert p.is_file() and not p.is_symlink() and c.digest(p.read_bytes())==item['sha256']
  assert (x.st_uid,x.st_gid,x.st_mode&0o7777)==resume_file_meta[str(p)]
 policy=c.read_json(b/'original-queue-continuity-policy.json')
 q=c.properties('xboard-queue.service'); assert q['ActiveState']=='active'
 assert {k:v for k,v in q.items() if k!='MainPID'}=={k:v for k,v in d['original_queue_properties'].items() if k!='MainPID'}
 assert c.digest(pathlib.Path(q['FragmentPath']).read_bytes())==policy['unit_sha256']
 for item in c.read_json(b/'redis-external-upgrade-policy.json')['post_upgrade_files']:
  p=pathlib.Path(item['path']); assert p.is_file() and not p.is_symlink() and c.digest(p.read_bytes())==item['sha256']
static_guard()
queue_old=d['original_queue_properties']['MainPID']; skip={queue_old,'124722'}
current={}
for oldpid,expected in d['protected_processes'].items():
 if oldpid in skip: continue
 candidates=[]
 for p in pathlib.Path('/proc').iterdir():
  if not p.name.isdigit(): continue
  identity=c.process_identity(int(p.name))
  if identity and all(identity[k]==expected[k] for k in ('uid','executable','exe_dev','exe_ino')): candidates.append(identity)
 assert len(candidates)==1, 'protected_executable_identity_not_unique'
 current[oldpid]=candidates[0]
for service in ('xboard-queue.service','redis-server.service'):
 q=c.properties(service); assert q['ActiveState']=='active' and int(q['MainPID'])>0
 current[service]=c.process_identity(int(q['MainPID'])); assert current[service]
original_state=(b/'panel-control.json').read_bytes()
backup=b/'resume-before-panel-control.private.json'; assert not backup.exists(); c.atomic_write(backup,original_state)
def resume_guard(ignored):
 static_guard()
 for identity in current.values(): assert c.process_identity(identity['pid'])==identity, 'current_protected_process_changed'
c.protected_unchanged=resume_guard
resume_guard(d)
results={}
value=e.cli('uninstall')['status']; assert not value['installed']; results['root_uninstall']=e.public_summary(value)
meta['phases'].append({'phase':'uninstall','at_ns':time.time_ns(),'result':results['root_uninstall']})
results['firewall_close']=e.firewall_close(meta)
meta['phases'].append({'phase':'firewall-close','at_ns':time.time_ns(),'result':results['firewall_close']}); c.write_json(b/'panel-endpoint.json',meta)
results['bridge']=c.remove_bridge(d)
results['ws']=c.stop_unit('ws',d)
results['worker_files']=c.restore_worker_files(d)
results['queue']=c.stop_unit('queue',d)
resume_guard(d)
assert not (e.STATE/'installation.json').exists() and not c.BRIDGE.exists()
assert all(not (pathlib.Path('/etc/systemd/system')/unit).exists() and c.properties(unit).get('MainPID','0')=='0' for unit in (c.WS_UNIT,c.QUEUE_UNIT))
result={'schema_version':1,'passed':True,'scope':'Owned panel cleanup after externally restored/rebooted VMs; original config bytes verified, resumed file metadata and protected processes pinned before/after cleanup. Initial PIDs and initial metadata are not claimed preserved across VM restoration.','results':results,'original_nginx_file_bytes_preserved':True,'resumed_nginx_file_metadata_preserved':True,'external_restore_metadata_changes':metadata_changes,'original_worker_files_restored':True,'current_protected_processes_preserved':True,'initial_process_pids_preserved_across_external_vm_restore':False,'external_vm_restore_user_confirmed':True,'current_process_identities':list(current.values()),'original_private_control_snapshot_retained':True}
c.write_json(b/'resume-panel-owned-cleanup-result.json',result); print(json.dumps({'passed':True,'own_panel_cleanup_completed':True,'initial_process_pids_preserved_across_external_vm_restore':False}))
