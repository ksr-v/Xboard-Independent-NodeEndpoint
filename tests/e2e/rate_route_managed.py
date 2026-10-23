"""Real panel -> published Node -> controlled target, with synthetic identities."""
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

traffic_path=Path(__file__).with_name('rate_route_traffic.py')
spec=importlib.util.spec_from_file_location('traffic',traffic_path)
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
PANEL=Path('/opt/xboard-rate-route-20261024')
PRIVATE=PANEL/'private'
NAME='codex-xboard-rate-route-20261024'
BASE=Path('/opt/xboard-rate-route-traffic-20261024')
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
            'panel':{'url':'http://127.0.0.1:17004','token':creds['server_token'],
                     'node_id':node['id'],'node_type':'vless'},
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


def speed(conn,limit):
    if limit:
        t.transfer(conn,'D',int(limit*1e6/8)+131072)
    return round(t.transfer(conn,'D',4*1024*1024),3)


def test(kernel,creds):
    php('mutate',{'user':'A','speed_limit':0})
    php('mutate',{'node':'vless','route_ids':[],'custom_routes':[],'custom_outbounds':[]})
    identity=creds['users']['A']['uuid']
    with t.serve(t.DataHandler) as server:
        target=server.server_address[1]
        with managed(kernel,creds) as port:
            with t.socks(port,identity,'127.0.0.1',target) as old_unlimited:
                rate=speed(old_unlimited,0)
                record(kernel,'panel-unlimited-download',rate,'>64 Mbps',rate>64)
                php('mutate',{'user':'A','speed_limit':16});time.sleep(6)
                payload=php('inspect')['nodes']['vless']['users']
                record(kernel,'panel-limit-payload',next(x['speed_limit'] for x in payload if x['alias']=='A'),16,
                       next(x['speed_limit'] for x in payload if x['alias']=='A')==16)
                with t.socks(port,identity,'127.0.0.1',target) as limited:
                    rate=speed(limited,16)
                    record(kernel,'panel-new-connection-16Mbps',rate,'12.8..19.2 Mbps',12.8<=rate<=19.2)
                    rate=speed(old_unlimited,0)
                    record(kernel,'enable-limit-existing-connection',rate,'<=19.2 Mbps',rate<=19.2)
                    php('mutate',{'user':'A','speed_limit':12});time.sleep(6)
                    rate=speed(limited,12)
                    record(kernel,'change-positive-existing-connection',rate,'9.6..14.4 Mbps',9.6<=rate<=14.4)
                    php('mutate',{'user':'A','speed_limit':0});time.sleep(6)
                    with t.socks(port,identity,'127.0.0.1',target) as new_unlimited:
                        rate=speed(new_unlimited,0)
                        record(kernel,'remove-limit-new-connection',rate,'>64 Mbps',rate>64)
                    rate=speed(limited,0)
                    record(kernel,'remove-limit-existing-connection',rate,'>64 Mbps',rate>64)
            # Real panel native route edits: target a controlled SOCKS outbound.
            with t.serve(t.MarkerHandler,'DIRECT') as target_server,t.serve(t.MarkerHandler,'OTHER') as other,t.serve(t.OutboundHandler) as outbound:
                dp=target_server.server_address[1];op=outbound.server_address[1]
                settings={'server':'127.0.0.1','server_port':op,'version':'5'} if kernel=='singbox' else {
                    'servers':[{'address':'127.0.0.1','port':op}]}
                outs=[{'tag':'controlled-out','protocol':'socks','settings':settings}]
                raw=[{'port':[dp],'action':'route','outbound':'controlled-out'}] if kernel=='singbox' else [
                    {'type':'field','port':str(dp),'outboundTag':'controlled-out'}]
                php('mutate',{'node':'vless','custom_routes':raw,'custom_outbounds':outs});time.sleep(6)
                for number,(destination,expected) in enumerate([(dp,'OUTBOUND'),(other.server_address[1],'OTHER')]):
                    observed=t.marker(port,identity,'127.0.0.1',destination)
                    record(kernel,'panel-native-route-'+str(number+1),observed,expected,observed==expected)
                php('mutate',{'node':'vless','custom_routes':[],'custom_outbounds':[]});time.sleep(6)
                observed=t.marker(port,identity,'127.0.0.1',dp)
                record(kernel,'panel-route-removal',observed,'DIRECT',observed=='DIRECT')
    php('mutate',{'user':'A','speed_limit':0})
    php('mutate',{'node':'vless','route_ids':[],'custom_routes':[],'custom_outbounds':[]})


def routes_only(kernel,creds):
    php('mutate',{'user':'A','speed_limit':0})
    php('mutate',{'node':'vless','route_ids':[],'custom_routes':[],'custom_outbounds':[]})
    identity=creds['users']['A']['uuid']
    with t.serve(t.MarkerHandler,'DIRECT') as target,t.serve(t.MarkerHandler,'OTHER') as other,t.serve(t.OutboundHandler) as outbound:
        dp=target.server_address[1];op=outbound.server_address[1]
        settings={'server':'127.0.0.1','server_port':op,'version':'5'} if kernel=='singbox' else {
            'servers':[{'address':'127.0.0.1','port':op}]}
        outs=[{'tag':'controlled-out','protocol':'socks','settings':settings}]
        raw=[{'port':[dp],'action':'route','outbound':'controlled-out'}] if kernel=='singbox' else [
            {'type':'field','port':str(dp),'outboundTag':'controlled-out'}]
        with managed(kernel,creds) as port:
            php('mutate',{'node':'vless','custom_routes':raw,'custom_outbounds':outs});time.sleep(6)
            payload=php('inspect')['nodes']['vless']
            record(kernel,'panel-route-outbound-payload',[payload['custom_route_count'],payload['custom_outbound_count']],[1,1],
                   payload['custom_route_count']==payload['custom_outbound_count']==1)
            for number,(destination,expected) in enumerate([(dp,'OUTBOUND'),(other.server_address[1],'OTHER')]):
                observed=t.marker(port,identity,'127.0.0.1',destination)
                record(kernel,'panel-native-route-'+str(number+1),observed,expected,observed==expected)
        with managed(kernel,creds) as port:
            observed=t.marker(port,identity,'127.0.0.1',dp)
            record(kernel,'panel-native-route-after-cold-start',observed,'OUTBOUND',observed=='OUTBOUND')
            php('mutate',{'node':'vless','custom_routes':[],'custom_outbounds':[]});time.sleep(6)
            observed=t.marker(port,identity,'127.0.0.1',dp)
            record(kernel,'panel-route-removal',observed,'DIRECT',observed=='DIRECT')
    php('mutate',{'node':'vless','route_ids':[],'custom_routes':[],'custom_outbounds':[]})


if __name__=='__main__':
    try:
        os.umask(0o077)
        t.check(os.geteuid()==0,'root-dedicated-fixture-required')
        t.check(json.loads((BASE/'owner.json').read_text())==t.OWNER,'traffic-fixture-owner-differs')
        t.check(json.loads((PANEL/'owner.json').read_text())['tool']=='Codex Xboard rate and route fixture','panel-owner-differs')
        creds=json.loads((PRIVATE/'credentials.private.json').read_text())
        # Preserve earlier sanitized standalone measurements before starting.
        previous=BASE/'report.json'
        if previous.is_file():
            rows=json.loads(previous.read_text())
            if rows and all(x['kernel']=='xray' for x in rows):
                (BASE/'standalone-xray.json').write_text(json.dumps(rows,indent=2)+'\n')
        for kernel in ['singbox','xray']:
            (routes_only if ROUTES_ONLY else test)(kernel,creds)
        print(json.dumps({'result':'PASS' if all(x['pass'] for x in REPORT) else 'FUNCTIONAL_FAILURES',
            'checks':len(REPORT),'failed':[x['kernel']+':'+x['test'] for x in REPORT if not x['pass']],
            'panel_real_database':True,'published_node_binary':True}),flush=True)
    except Exception as error:
        print(json.dumps({'result':'HARNESS_ERROR','type':type(error).__name__,
            'reason':str(error) if isinstance(error,RuntimeError) else 'managed-controlled-harness-exception'}),flush=True)
        raise SystemExit(1)
