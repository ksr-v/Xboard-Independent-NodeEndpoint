"""Real panel -> fixed candidate Node -> controlled target; synthetic identities."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.dont_write_bytecode=True
traffic_path=Path(__file__).with_name('rate_route_fixed_traffic.py')
spec=importlib.util.spec_from_file_location('traffic',traffic_path)
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
PANEL=Path('/opt/xboard-rate-route-fixed-20261024')
PRIVATE=PANEL/'private'
NAME='codex-xboard-rate-route-fixed-20261024'
BASE=Path('/opt/xboard-rate-route-fix-traffic-20261024')
REPORT=[]
ROUTES_ONLY=sys.argv[1:]==['routes']


def php(action, data=None):
    result=subprocess.run(['docker','exec','--user','1000:1000','--workdir','/www','--env',
        'XBOARD_RATE_ROUTE_FIXTURE=20261024','-i',NAME,'php','/tmp/codex-rate-route-seed-20261024.php',action],
        input=json.dumps(data or {}).encode(),capture_output=True,timeout=30)
    t.check(result.returncode==0,'fixture-php-'+action+'-failed')
    return json.loads(result.stdout)


def record(kernel,name,observed,expected,ok):
    row={'kernel':kernel,'test':name,'observed':observed,'expected':expected,'pass':bool(ok)}
    REPORT.append(row)
    (BASE/('managed-routes-report.json' if ROUTES_ONLY else 'managed-report.json')).write_text(json.dumps(REPORT,indent=2)+'\n')
    print(json.dumps(row),flush=True)


@contextlib.contextmanager
def managed(kernel,creds):
    directory=BASE/('managed-'+kernel)
    directory.mkdir(mode=0o700,exist_ok=True)
    node=creds['nodes']['vless']
    port=node['port']
    if kernel=='singbox':
        rules=[{'ip_cidr':['127.0.0.0/8'],'action':'route','outbound':'direct'}]
    else:
        rules=[{'type':'field','ip':['127.0.0.0/8'],'outboundTag':'direct'}]
    config={'kernel':{'type':kernel,'config_dir':str(directory/'generated'),'custom_route':rules},
            'log':{'level':'info','output':str(directory/'node.private.log')},
            'cert':{'cert_mode':'none'},
            'panel':{'url':'http://127.0.0.1:17005'},
            'machine':{'machine_id':creds['machine_id'],'token':creds['machine_token']},
            'node':{'pull_interval':2,'push_interval':5,'track_interval':1}}
    path=directory/'config.json';path.write_text(json.dumps(config))
    with (directory/'stdout.private').open('wb') as log:
        proc=subprocess.Popen([str(BASE/'xboard-node'),'-c',str(path)],cwd=directory,
                              stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    t.VLESS_PORTS.add(port)
    try:
        for _ in range(150):
            t.check(proc.poll() is None,'managed-node-startup-failed')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('managed-node-not-listening')
        yield port
    finally:
        t.VLESS_PORTS.discard(port)
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=3)


def speed(conn,limit,direction='D'):
    if limit:
        t.transfer(conn,direction,int(limit*1e6/8)+131072)
    return round(t.transfer(conn,direction,4*1024*1024),3)


def change_and_observe(kernel,conn,direction,limit,label):
    """Reuse the exact pre-existing TCP stream; no reconnect or fixed sleep."""
    save_start=time.monotonic()
    php('mutate',{'user':'A','speed_limit':limit})
    saved=time.monotonic()
    deadline=saved+5
    samples=[]
    first=None
    consecutive=0
    error_type=None
    conn.settimeout(1.5)
    # Unlimited probes must exceed the largest preceding one-second burst;
    # otherwise two short transfers could appear unlimited before the update.
    sample_size=4*1024*1024 if limit==0 else 1024*1024
    while time.monotonic()<deadline:
        try:
            rate=t.transfer(conn,direction,sample_size)
        except (OSError,EOFError,RuntimeError) as error:
            error_type=type(error).__name__
            break
        ended=time.monotonic()
        ok=rate>64 if limit==0 else .8*limit<=rate<=1.2*limit
        samples.append({'mbps':round(rate,3),'elapsed_ms':round((ended-saved)*1000,1)})
        if ok:
            if first is None:
                first=ended
            consecutive+=1
            if consecutive==2:
                break
        else:
            first=None
            consecutive=0
    observed={'samples':samples,'first_observed_ms':round((first-saved)*1000,1) if first else None,
              'save_call_ms':round((saved-save_start)*1000,1),'same_tcp_stream':True,
              'transfer_error':error_type}
    success=consecutive>=2 and first is not None and first<=deadline and error_type is None
    record(kernel,label,observed,'same TCP stream; two '+('>64 Mbps' if limit==0 else str(.8*limit)+'..'+str(1.2*limit)+' Mbps')+' samples; first <=5000 ms',success)
    conn.settimeout(9)
    return success


def observe_closed(kernel,conn,saved,label):
    remaining=max(.001,1-(time.monotonic()-saved))
    conn.settimeout(remaining)
    closed=False
    try:
        closed=conn.recv(1)==b''
    except socket.timeout:
        pass
    except (OSError,EOFError):
        closed=True
    elapsed=round((time.monotonic()-saved)*1000,1)
    record(kernel,label,{'closed':closed,'elapsed_ms':elapsed},'existing TCP closed <=1000 ms',closed and elapsed<=1000)


def poll_markers(kernel,port,identity,probes,saved,label):
    deadline=saved+5
    attempt=0
    latest=[]
    ok=False
    while time.monotonic()<deadline:
        attempt+=1
        # VLESS may flush a short reply after more than 200 ms. Bound each
        # probe without treating its protocol buffering as a failed route.
        latest=[t.marker(port,identity,host,destination,timeout=.75) for host,destination,expected in probes]
        ok=all(value in ('CLOSED','BLOCKED') if expected=='BLOCKED' else value==expected
               for value,(_,_,expected) in zip(latest,probes))
        if ok:
            break
        time.sleep(.025)
    elapsed=round((time.monotonic()-saved)*1000,1)
    record(kernel,label,{'markers':latest,'elapsed_ms':elapsed,'attempts':attempt},
           {'markers':[x[2] for x in probes],'within_ms':5000},ok and elapsed<=5000)


def route_change(kernel,port,identity,data_port,changes,probes,label):
    with t.socks(port,identity,'127.0.0.1',data_port) as live:
        t.transfer(live,'D',1024)
        php('mutate',changes)
        saved=time.monotonic()
        observe_closed(kernel,live,saved,label+'-existing-flow-closed')
        poll_markers(kernel,port,identity,probes,saved,label+'-new-policy')


def routing_tests(kernel,creds,port,identity,data_port):
    with t.serve(t.MarkerHandler,'DIRECT') as target,t.serve(t.MarkerHandler,'OTHER') as other,t.serve(t.OutboundHandler) as outbound:
        dp=target.server_address[1]
        other_port=other.server_address[1]
        op=outbound.server_address[1]
        settings={'server':'127.0.0.1','server_port':op,'version':'5'} if kernel=='singbox' else {
            'servers':[{'address':'127.0.0.1','port':op}]}
        outs=[{'tag':'controlled-out','protocol':'socks','settings':settings}]
        blocked=[{'port':[dp],'action':'route','outbound':'block'}] if kernel=='singbox' else [
            {'type':'field','port':str(dp),'outboundTag':'block'}]
        routed=[{'port':[dp],'action':'route','outbound':'controlled-out'}] if kernel=='singbox' else [
            {'type':'field','port':str(dp),'outboundTag':'controlled-out'}]
        baseline=t.marker(port,identity,'127.0.0.1',dp)
        record(kernel,'panel-route-baseline',baseline,'DIRECT',baseline=='DIRECT')
        route_change(kernel,port,identity,data_port,
            {'node':'vless','custom_routes':blocked,'custom_outbounds':[]},
            [('127.0.0.1',dp,'BLOCKED'),('127.0.0.1',other_port,'OTHER')],'panel-native-block')
        route_change(kernel,port,identity,data_port,
            {'node':'vless','custom_routes':routed,'custom_outbounds':outs},
            [('127.0.0.1',dp,'OUTBOUND'),('127.0.0.1',other_port,'OTHER')],'panel-native-new-outbound')
        payload=php('inspect')['nodes']['vless']
        record(kernel,'panel-route-outbound-payload',[payload['custom_route_count'],payload['custom_outbound_count']],[1,1],
               payload['custom_route_count']==payload['custom_outbound_count']==1)
        route_change(kernel,port,identity,data_port,
            {'node':'vless','custom_routes':[],'custom_outbounds':[]},
            [('127.0.0.1',dp,'DIRECT'),('127.0.0.1',other_port,'OTHER')],'panel-native-route-removal')
        record(kernel,'panel-controlled-outbound-actually-used',outbound.hits,'>0 CONNECT requests',outbound.hits>0)


def test(kernel,creds):
    php('mutate',{'user':'A','speed_limit':0})
    php('mutate',{'node':'socks','enabled':False})
    php('mutate',{'node':'vless','enabled':True,'route_ids':[],'custom_routes':[],'custom_outbounds':[]})
    identity=creds['users']['A']['uuid']
    with t.serve(t.DataHandler) as server:
        target=server.server_address[1]
        with managed(kernel,creds) as port:
            if not ROUTES_ONLY:
                for direction,word in [('D','download'),('U','upload')]:
                    php('mutate',{'user':'A','speed_limit':0})
                    with t.socks(port,identity,'127.0.0.1',target) as live:
                        # Confirm a real stream is usable before changing settings.
                        initial=speed(live,0,direction)
                        record(kernel,'panel-unlimited-'+word,initial,'>64 Mbps',initial>64)
                        if not change_and_observe(kernel,live,direction,16,'enable-limit-existing-'+word):
                            continue
                        payload=php('inspect')['nodes']['vless']['users']
                        actual=next(x['speed_limit'] for x in payload if x['alias']=='A')
                        record(kernel,'panel-limit-payload-'+word,actual,16,actual==16)
                        if not change_and_observe(kernel,live,direction,12,'change-positive-existing-'+word):
                            continue
                        change_and_observe(kernel,live,direction,0,'disable-limit-existing-'+word)
            # Reset speed through the same real model notifications, waiting on
            # traffic rather than assuming a fixed sleep covers propagation.
            with t.socks(port,identity,'127.0.0.1',target) as reset:
                change_and_observe(kernel,reset,'D',0,'unlimited-before-routing')
            routing_tests(kernel,creds,port,identity,target)
    php('mutate',{'user':'A','speed_limit':0})
    php('mutate',{'node':'vless','route_ids':[],'custom_routes':[],'custom_outbounds':[]})


if __name__=='__main__':
    try:
        os.umask(0o077)
        t.check(os.geteuid()==0,'root-dedicated-fixture-required')
        t.prepare_fixture()
        t.check(json.loads((BASE/'owner.json').read_text())==t.OWNER,'traffic-fixture-owner-differs')
        panel_owner=json.loads((PANEL/'owner.json').read_text())
        t.check(not PANEL.is_symlink() and PANEL.resolve()==PANEL and
                panel_owner.get('tool')=='Codex Xboard fixed rate and route fixture' and
                panel_owner.get('path')==str(PANEL),'panel-owner-differs')
        creds=json.loads((PRIVATE/'credentials.private.json').read_text())
        for kernel in ['singbox','xray']:
            test(kernel,creds)
        print(json.dumps({'result':'PASS' if all(x['pass'] for x in REPORT) else 'FUNCTIONAL_FAILURES',
            'checks':len(REPORT),'failed':[x['kernel']+':'+x['test'] for x in REPORT if not x['pass']],
            'panel_real_database':True,'candidate_node_binary':True,'machine_mode':True,'binary_sha256':t.SHA}),flush=True)
    except Exception as error:
        print(json.dumps({'result':'HARNESS_ERROR','type':type(error).__name__,
            'reason':str(error) if isinstance(error,RuntimeError) else 'managed-controlled-harness-exception'}),flush=True)
        raise SystemExit(1)
