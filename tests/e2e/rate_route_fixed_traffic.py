"""Controlled loopback traffic against the candidate independent Node binary.

Only sanitized measurements leave the Root-owned fixture. No standard Node
configuration, service, firewall, Nginx or existing panel is changed.
"""
import concurrent.futures as futures
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid

BASE = Path('/opt/xboard-rate-route-fix-traffic-20261024')
OWNER = {'tool': 'Codex fixed Node traffic fixture', 'path': str(BASE)}
SHA = None
METADATA = Path(__file__).with_name('candidate-metadata.json')
BINARY_SOURCE = Path('/tmp/codex-rate-route-fixed-node-20261024')
CHUNK = b'Xboard controlled rate and route acceptance\n' * 1024
REPORT = []
VLESS_PORTS = set()


def check(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def exact(conn, count):
    data = bytearray()
    while len(data) < count:
        part = conn.recv(count-len(data))
        if not part:
            raise EOFError('controlled-peer-closed')
        data.extend(part)
    return bytes(data)


class ThreadServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class DataHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        try:
            while True:
                command = exact(self.request, 9)
                size = struct.unpack('!Q', command[1:])[0]
                check(size <= 128*1024*1024, 'unsafe-traffic-size')
                if command[:1] == b'D':
                    left = size
                    while left:
                        part = CHUNK[:min(left,len(CHUNK))]
                        self.request.sendall(part)
                        left -= len(part)
                elif command[:1] == b'U':
                    left = size
                    while left:
                        part = self.request.recv(min(65536,left))
                        if not part:
                            return
                        left -= len(part)
                    self.request.sendall(b'K')
                else:
                    return
        except (EOFError,OSError):
            return


class MarkerHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(4)
            self.request.recv(8192)
            self.server.hits += 1
            body = self.server.marker.encode()
            self.request.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        except OSError:
            pass


class OutboundHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(5)
            head = exact(self.request,2)
            exact(self.request,head[1])
            self.request.sendall(b'\x05\x00')
            target = exact(self.request,4)
            if target[3] == 1:
                exact(self.request,4)
            elif target[3] == 3:
                exact(self.request,exact(self.request,1)[0])
            elif target[3] == 4:
                exact(self.request,16)
            else:
                return
            exact(self.request,2)
            self.server.hits += 1
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01')
            self.request.recv(8192)
            self.request.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 8\r\n\r\nOUTBOUND')
        except (OSError,EOFError):
            pass


@contextlib.contextmanager
def serve(handler, marker=None):
    server = ThreadServer(('127.0.0.1',0),handler)
    server.hits = 0
    server.marker = marker
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def socks(port, identity, target, target_port, timeout=None):
    conn = socket.create_connection(('127.0.0.1',port),timeout=5 if timeout is None else timeout)
    conn.settimeout(9 if timeout is None else timeout)
    if port in VLESS_PORTS:
        try:
            address = b'\x01'+socket.inet_aton(target)
        except OSError:
            address = b'\x02'+bytes([len(target)])+target.encode()
        conn.sendall(b'\x00'+uuid.UUID(identity).bytes+b'\x00\x01'+struct.pack('!H',target_port)+address)
        return VlessConnection(conn)
    conn.sendall(b'\x05\x01\x02')
    check(exact(conn,2)==b'\x05\x02','socks-auth-method')
    value = identity.encode()
    conn.sendall(b'\x01'+bytes([len(value)])+value+bytes([len(value)])+value)
    check(exact(conn,2)==b'\x01\x00','socks-auth-result')
    try:
        address = b'\x01'+socket.inet_aton(target)
    except OSError:
        address = b'\x03'+bytes([len(target)])+target.encode()
    conn.sendall(b'\x05\x01\x00'+address+struct.pack('!H',target_port))
    reply=exact(conn,4)
    check(reply[:2]==b'\x05\x00','socks-connect-result')
    if reply[3]==1:
        exact(conn,6)
    elif reply[3]==4:
        exact(conn,18)
    elif reply[3]==3:
        exact(conn,exact(conn,1)[0]+2)
    return conn


class VlessConnection:
    def __init__(self,conn):
        self.conn=conn
        self.first=True
    def __getattr__(self,name):
        return getattr(self.conn,name)
    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.conn.close()
    def recv(self,count):
        if self.first:
            head=exact(self.conn,2)
            check(head[0]==0,'vless-version')
            exact(self.conn,head[1])
            self.first=False
        return self.conn.recv(count)


def transfer(conn, direction, size):
    start=time.monotonic()
    conn.sendall(direction.encode()+struct.pack('!Q',size))
    left=size
    if direction=='D':
        while left:
            part=conn.recv(min(left,65536))
            check(bool(part),'download-truncated')
            left-=len(part)
    else:
        while left:
            part=CHUNK[:min(left,len(CHUNK))]
            conn.sendall(part)
            left-=len(part)
        check(exact(conn,1)==b'K','upload-ack')
    return size*8/(time.monotonic()-start)/1e6


def measure(port, identity, target_port, direction, limit):
    with socks(port,identity,'127.0.0.1',target_port) as conn:
        if limit:
            transfer(conn,direction,int(limit*1e6/8)+131072)
        size=4*1024*1024 if limit else 32*1024*1024
        return round(transfer(conn,direction,size),3)


def marker(port, identity, target, target_port, timeout=3):
    try:
        with socks(port,identity,target,target_port,timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(b'GET /controlled HTTP/1.1\r\nHost: controlled.fixture.test\r\nConnection: close\r\n\r\n')
            data=bytearray()
            while len(data)<8192:
                part=conn.recv(8192)
                if not part:
                    break
                data.extend(part)
                if b'\r\n\r\n' in data:
                    header,body=data.split(b'\r\n\r\n',1)
                    length=re.search(rb'(?im)^Content-Length:\s*(\d+)\s*$',header)
                    if length and len(body)>=int(length[1]):
                        return body[:int(length[1])].decode()
            return data.split(b'\r\n\r\n',1)[1].decode() if b'\r\n\r\n' in data else 'CLOSED'
    except (EOFError,OSError,RuntimeError):
        return 'BLOCKED'


def record(kernel, name, observed, expected, ok):
    item={'kernel':kernel,'test':name,'observed':observed,'expected':expected,'pass':bool(ok)}
    REPORT.append(item)
    print(json.dumps(item),flush=True)
    (BASE/'report.json').write_text(json.dumps(REPORT,indent=2)+'\n')
    (BASE/('standalone-'+kernel+'.json')).write_text(json.dumps([x for x in REPORT if x['kernel']==kernel],indent=2)+'\n')


@contextlib.contextmanager
def standalone(kernel, users, rules, outbounds=None, legacy=None):
    directory=BASE/(kernel+'-'+secrets.token_hex(4))
    directory.mkdir(mode=0o700)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    config={'kernel':{'type':kernel,'config_dir':str(directory/'generated'),'custom_outbound':outbounds or []},
            'log':{'level':'warn','output':str(directory/'node.log')},'cert':{'cert_mode':'none'},
            'standalone':{'enabled':True,'node':{'protocol':'vless' if kernel=='xray' else 'socks','listen_ip':'127.0.0.1','network':'tcp','tls':0,
                'server_port':port,'custom_route_rules':rules,'routes':legacy or []},'users':users}}
    if kernel=='xray':
        VLESS_PORTS.add(port)
    path=directory/'config.json'
    path.write_text(json.dumps(config))
    with (directory/'stdout.private').open('wb') as stdout:
        process=subprocess.Popen([str(BASE/'xboard-node'),'-c',str(path)],cwd=directory,stdout=stdout,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        ready=False
        for _ in range(100):
            check(process.poll() is None,'node-startup-failed-'+kernel)
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):
                    ready=True
                    break
            except OSError:
                time.sleep(.1)
        check(ready,'node-not-listening')
        yield port
    finally:
        VLESS_PORTS.discard(port)
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=3)


def run(kernel):
    identities=[str(uuid.uuid4()) for _ in range(3)]
    users=[{'id':i+1,'uuid':value,'speed_limit':0 if i==0 else 8} for i,value in enumerate(identities)]
    direct={'name':'controlled-loopback','match':{'ip_cidrs':['127.0.0.0/8']},'action':{'type':'direct'}}
    with serve(DataHandler) as data:
        with standalone(kernel,users,[direct]) as port:
            for index,label in [(0,'unlimited'),(1,'8Mbps')]:
                for direction,word in [('D','download'),('U','upload')]:
                    try:
                        rate=measure(port,identities[index],data.server_address[1],direction,0 if index==0 else 8)
                    except (OSError,EOFError,RuntimeError) as error:
                        rate='TRANSFER_'+type(error).__name__
                        record(kernel,label+'-'+word,rate,'complete within expected bandwidth window',False)
                        continue
                    record(kernel,label+'-'+word,rate,'>32 Mbps' if index==0 else '6.4..9.6 Mbps',rate>32 if index==0 else 6.4<=rate<=9.6)
            def parallel(same):
                connections=[socks(port,identities[1 if same else i+1],'127.0.0.1',data.server_address[1]) for i in range(2)]
                try:
                    transfer(connections[0],'D',1200000)
                    start=time.monotonic()
                    with futures.ThreadPoolExecutor(2) as pool:
                        jobs=[pool.submit(transfer,c,'D',4*1024*1024) for c in connections]
                        [job.result() for job in jobs]
                    return round(2*4*1024*1024*8/(time.monotonic()-start)/1e6,3)
                finally:
                    [c.close() for c in connections]
            for same,label,lo,hi in [(True,'same-user-two-connections',6.4,9.6),(False,'different-users-two-connections',12.8,19.2)]:
                try:
                    rate=parallel(same)
                    record(kernel,label,rate,str(lo)+'..'+str(hi)+' Mbps total',lo<=rate<=hi)
                except (OSError,EOFError,RuntimeError) as error:
                    record(kernel,label,'TRANSFER_'+type(error).__name__,'complete within expected bandwidth window',False)
    # Exercise arbitrary callback sizes below the historical 8 Mbps threshold.
    slow_identity=str(uuid.uuid4())
    with serve(DataHandler) as data:
        with standalone(kernel,[{'id':1,'uuid':slow_identity,'speed_limit':1}],[direct]) as port:
            for direction,word in [('D','download'),('U','upload')]:
                try:
                    with socks(port,slow_identity,'127.0.0.1',data.server_address[1]) as conn:
                        conn.settimeout(20)
                        transfer(conn,direction,125000+131072)
                        observed=round(transfer(conn,direction,1024*1024),3)
                    record(kernel,'1Mbps-large-transfer-'+word,observed,'0.8..1.2 Mbps; complete',.8<=observed<=1.2)
                except (OSError,EOFError,RuntimeError) as error:
                    record(kernel,'1Mbps-large-transfer-'+word,'TRANSFER_'+type(error).__name__,'complete at 1 Mbps',False)
    with serve(MarkerHandler,'DIRECT') as target, serve(MarkerHandler,'OTHER') as other, serve(OutboundHandler) as outbound:
        target_port=target.server_address[1]
        other_port=other.server_address[1]
        outbound_port=outbound.server_address[1]
        if kernel=='singbox':
            outs=[{'type':'socks','tag':'controlled-out','server':'127.0.0.1','server_port':outbound_port,'version':'5'}]
        else:
            outs=[{'protocol':'socks','tag':'controlled-out','settings':{'servers':[{'address':'127.0.0.1','port':outbound_port}]}}]
        def rule(name,match,action,target=None):
            return {'name':name,'match':match,'action':dict(type=action,**({'target':target} if target else {}))}
        cases=[
            ('custom-outbound-port',[rule('out-port',{'ports':[str(target_port)]},'route','controlled-out'),direct], [('127.0.0.1',target_port,'OUTBOUND'),('127.0.0.1',other_port,'OTHER')]),
            ('block-port',[rule('block-port',{'ports':[str(target_port)]},'block'),direct], [('127.0.0.1',target_port,'BLOCKED'),('127.0.0.1',other_port,'OTHER')]),
            ('exact-domain',[rule('exact-domain',{'domains':['exact.fixture.test']},'route','controlled-out'),direct], [('exact.fixture.test',target_port,'OUTBOUND'),('sub.exact.fixture.test',target_port,'BLOCKED')]),
            ('suffix-domain',[rule('suffix-domain',{'domain_suffixes':['suffix.fixture.test']},'route','controlled-out'),direct], [('suffix.fixture.test',target_port,'OUTBOUND'),('sub.suffix.fixture.test',target_port,'OUTBOUND')]),
            ('combined-port-network-OR-observation',[rule('combined',{'ports':[str(target_port)],'networks':['tcp']},'route','controlled-out'),direct], [('127.0.0.1',target_port,'OUTBOUND'),('127.0.0.1',other_port,'OUTBOUND')]),
            ('first-match',[rule('block-first',{'ports':[str(target_port)]},'block'),rule('allow-second',{'ports':[str(target_port)]},'direct'),direct], [('127.0.0.1',target_port,'BLOCKED')]),
        ]
        for label,rules,probes in cases:
            with standalone(kernel,[users[0]],rules,outs) as port:
                for index,(host,destination,expected) in enumerate(probes):
                    observed=marker(port,identities[0],host,destination)
                    # Both an explicit block and graceful close prove no HTTP response.
                    ok=observed in ('CLOSED','BLOCKED') if expected=='BLOCKED' else observed==expected
                    record(kernel,label+'-'+str(index+1),observed,expected,ok)
        record(kernel,'controlled-outbound-actually-used',outbound.hits,'>0 CONNECT requests',outbound.hits>0)


def prepare_fixture():
    global SHA
    os.umask(0o077)
    check(os.geteuid()==0,'root-dedicated-fixture-required')
    check(METADATA.is_file() and not METADATA.is_symlink(),'candidate-metadata-missing')
    check(METADATA.stat().st_uid==0 and METADATA.stat().st_mode & 0o022==0,'candidate-metadata-permissions')
    metadata=json.loads(METADATA.read_text())
    SHA=metadata.get('binary_sha256')
    check(isinstance(SHA,str) and len(SHA)==64 and all(c in '0123456789abcdef' for c in SHA),'candidate-sha-malformed')
    check(metadata.get('binary_path')==str(BINARY_SOURCE),'candidate-source-path-differs')
    if not BASE.exists():
        BASE.mkdir(mode=0o700)
        (BASE/'owner.json').write_text(json.dumps(OWNER))
    check(BASE.resolve()==BASE and not BASE.is_symlink() and json.loads((BASE/'owner.json').read_text())==OWNER,'fixture-ownership-differs')
    check(BASE.stat().st_uid==0 and BASE.stat().st_mode & 0o077==0,'fixture-permissions-differ')
    destination=BASE/'xboard-node'
    if not destination.exists():
        check(BINARY_SOURCE.is_file() and not BINARY_SOURCE.is_symlink(),'candidate-binary-source-missing')
        check(BINARY_SOURCE.stat().st_uid==0 and BINARY_SOURCE.stat().st_mode & 0o022==0,'candidate-binary-permissions')
        check(hashlib.sha256(BINARY_SOURCE.read_bytes()).hexdigest()==SHA,'candidate-binary-hash-differs')
        shutil.copyfile(BINARY_SOURCE,destination)
        destination.chmod(0o700)
    check(not destination.is_symlink() and hashlib.sha256(destination.read_bytes()).hexdigest()==SHA,'binary-changed')


def main():
    prepare_fixture()
    if sys.argv[1:]==['threshold']:
        identity=str(uuid.uuid4())
        rules=[{'name':'controlled-loopback','match':{'ip_cidrs':['127.0.0.0/8']},'action':{'type':'direct'}}]
        with serve(DataHandler) as server:
            with standalone('singbox',[{'id':1,'uuid':identity,'speed_limit':9}],rules) as port:
                for direction,label in [('D','download'),('U','upload')]:
                    try:
                        with socks(port,identity,'127.0.0.1',server.server_address[1]) as conn:
                            transfer(conn,direction,9*1000000//8+131072)
                            rate=round(transfer(conn,direction,16*1024*1024),3)
                        record('singbox','9Mbps-threshold-'+label,rate,'7.2..10.8 Mbps',7.2<=rate<=10.8)
                    except (OSError,EOFError,RuntimeError) as error:
                        record('singbox','9Mbps-threshold-'+label,'TRANSFER_'+type(error).__name__,'complete at 9 Mbps',False)
        (BASE/'threshold-report.json').write_text(json.dumps(REPORT,indent=2)+'\n')
        return
    for kernel in (sys.argv[1:] or ['singbox','xray']):
        check(kernel in ['singbox','xray'],'unknown-kernel')
        run(kernel)
    print(json.dumps({'result':'PASS' if all(x['pass'] for x in REPORT) else 'FUNCTIONAL_FAILURES',
                      'checks':len(REPORT),'failed':[x['kernel']+':'+x['test'] for x in REPORT if not x['pass']],
                      'binary_sha256':SHA,'only_controlled_loopback_traffic':True}),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'result':'HARNESS_ERROR','type':type(error).__name__,'reason':str(error) if isinstance(error,RuntimeError) else 'controlled-harness-exception'}),flush=True)
        raise SystemExit(1)
