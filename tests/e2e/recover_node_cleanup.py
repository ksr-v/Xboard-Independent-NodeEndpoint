import hashlib,json,os,pathlib,shutil,subprocess,sys,time
b=pathlib.Path('/opt/node-endpoint-lab-20261010'); assert os.geteuid()==0 and (b/'.node-endpoint-vm-lab').is_file()
sys.path.insert(0,str(b/'tests/e2e')); import node_runner as n
r=n.Runner(b/'e2e-manifest-007.json'); s=r.state
assert s['failed'] and s['phase']=='rolled_back' and s['cleanup_failures']==['own_standard_layout_cleanup_failed']
assert len(s['checks'])==14 and all(x['passed'] for x in s['checks'])
assert n.private_json(n.STANDARD_ROOT/'.e2e-owner.json')=={'run_id':r.run_id,'marker':s['marker']}
r.assert_preserved()
assert (n.STANDARD_ROOT/'config.yml').read_bytes()==(r.base/'original-config.private.yml').read_bytes()
assert n.private_json(n.STANDARD_ROOT/'install-meta.json')==n.private_json(r.base/'original-meta.private.json')
assert set(p.name for p in n.STANDARD_ROOT.iterdir())<={'config.yml','credentials.env','install-meta.json','.e2e-owner.json','e2e-node-data','e2e-machine-data','.node-endpoint-backups','.node-endpoint.lock','node-endpoint-trust.pem'}
for p in n.STANDARD_ROOT.rglob('*'):
 assert not p.is_symlink() and p.stat().st_uid==0
 assert p.is_dir() or (p.is_file() and p.stat().st_nlink==1)
for service in s['units']:
 assert not (pathlib.Path('/etc/systemd/system')/service).exists()
 assert r.unit_state(service).get('MainPID')=='0'
 assert r.unit_state(service).get('ActiveState')=='inactive'
for p in pathlib.Path('/proc').iterdir():
 if p.name.isdigit():
  try:
   args=(p/'cmdline').read_bytes().split(b'\0')
   assert not (str(b/'tests/e2e/node_runner.py').encode() in args and str(r.manifest_path).encode() in args)
   assert not ((p/'exe').exists() and (p/'exe').resolve()==n.STANDARD_BINARY)
  except (FileNotFoundError,ProcessLookupError,PermissionError): pass
protected=r.protected_services(); assert all('ActiveState=active' in v for v in protected.values())
foreign=r.nft_foreign_hash(s.get('nft_table'))
tables=json.loads(subprocess.run(['nft','-j','list','tables'],capture_output=True,text=True,check=True).stdout)['nftables']
own_exists=any(x.get('table',{}).get('family')=='inet' and x.get('table',{}).get('name')==s['nft_table'] for x in tables)
if own_exists:
 assert n.ne.digest(json.dumps(n.stable_nft(r.nft_json(s['nft_table'])),sort_keys=True).encode())==s['own_nft_sha256']
backup=r.base/'cleanup-recovery-before-state.private.json'; assert not backup.exists(); n.write(backup,(r.base/'state.json').read_bytes())
if n.STANDARD_DROPIN.exists():
 assert not list(n.STANDARD_DROPIN.iterdir())
 n.remove_owned_standard_dropin(n.STANDARD_DROPIN,b'')
shutil.rmtree(n.STANDARD_ROOT); n.STANDARD_BINARY.unlink()
subprocess.run(['systemctl','daemon-reload'],check=True)
if own_exists: subprocess.run(['nft','delete','table','inet',s['nft_table']],check=True)
assert r.nft_foreign_hash()==foreign and r.protected_services()==protected
assert not n.STANDARD_ROOT.exists() and not n.STANDARD_BINARY.exists() and not n.STANDARD_DROPIN.exists()
result={'schema':1,'result':'PASS','passed':True,'scope':'Separate recovery of owned run007 fixture cleanup after user-restored VMs; original failed run and state remain unchanged.','original_run_result':'FAIL','original_business_checks':14,'original_failed_history_preserved':True,'owned_standard_layout_removed':True,'owned_services_absent_and_no_own_processes':True,'owned_nft_table_already_absent_after_vm_restore':not own_exists,'current_foreign_nft_preserved':True,'current_protected_services_preserved':True,'original_protected_service_pids_changed_during_external_vm_restore':protected!=s['protected_services'],'original_foreign_nft_snapshot_matches_after_external_vm_restore':foreign==s['foreign_nft_sha256'],'recovery_runner_sha256':hashlib.sha256((b/'tests/e2e/node_runner.py').read_bytes()).hexdigest(),'historical_state_unmodified':(r.base/'state.json').read_bytes()==backup.read_bytes()}
n.write_json(b/'node-cleanup-recovery-result.json',result); print(json.dumps(result))
