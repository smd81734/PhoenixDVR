#!/usr/bin/env python3
"""Authenticated reconnectable transport for one RAM installer process.

Disconnecting never closes the installer's input. Credentials are passed straight
to its pipe; only status events and input-accepted flags are retained in RAM.
"""
import hashlib,hmac,ipaddress,json,os,socket,subprocess,sys,threading,time
from pathlib import Path


def validate_config(value):
    if value.get('version')!=1:raise ValueError('Unsupported network setup version')
    device=ipaddress.IPv4Interface(value['device_ip']+'/30')
    host=ipaddress.IPv4Interface(value['host_ip']+'/30')
    allowed=ipaddress.IPv4Network('169.254.0.0/16')
    if device.ip not in allowed or host.ip not in allowed or device.network!=host.network or device.ip==host.ip:raise ValueError('Expected a direct-link /30 subnet')
    if device.ip not in device.network.hosts() or host.ip not in host.network.hosts():raise ValueError('Invalid host address')
    for name,length in (('token',64),('device_cid_sha256',64)):
        text=value[name]
        if not isinstance(text,str) or len(text)!=length or any(c not in '0123456789abcdef' for c in text):raise ValueError('Invalid setup identity')
    if value.get('port')!=18991:raise ValueError('Unexpected setup port')
    return value


class InstallerSession:
    requests={'need-policy':b'PHOENIX_POLICY ', 'need-config':b'PHOENIX_CONFIG ', 'need-recovery-key':b'PHOENIX_RECOVERY_KEY ', 'need-retry':b'PHOENIX_RETRY '}

    def __init__(self,root,cid):
        self.root=root;self.cid=cid;self.lock=threading.RLock()
        self.child=None;self.events=[];self.sequence=0;self.pending=None
        self.policy=False;self.configured=False;self.finished=False;self.terminal=False;self.slots={}

    def start(self):
        with self.lock:
            if self.child is not None:return
            env=dict(os.environ,PHOENIX_ETHERNET_INSTALL='1')
            self.child=subprocess.Popen([sys.executable,'-I','-u',str(self.root/'stage2.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
            self.worker=threading.Thread(target=self.drain,daemon=True);self.worker.start()

    def record(self,event):
        with self.lock:
            if 'kernel_slot' in event and 'root_slot' in event:self.slots={k:event[k] for k in ('kernel_slot','root_slot')}
            if event.get('event') in self.requests:
                if event.get('event')=='need-retry' and event.get('code')=='configuration':self.configured=False
                self.sequence+=1;self.pending=(self.sequence,event)
            else:
                self.sequence+=1;self.events.append((self.sequence,event))
                self.events=self.events[-1024:]
                if event.get('event') in ('complete','failed'):self.terminal=True

    def drain(self):
        try:
            for line in self.child.stdout:
                if not line.startswith(b'PHOENIX_EVENT '):continue
                try:event=json.loads(line[14:])
                except (ValueError,UnicodeError):continue
                if isinstance(event,dict):self.record(event)
            self.child.wait()
        finally:
            with self.lock:
                if not self.terminal:self.record({'event':'failed','message':'Installer process stopped. Reconnect to review status; do not restart installation over a partial system.'})
                self.finished=True;self.pending=None
                self.child.stdin.close();self.child.stdout.close()

    def state(self):
        with self.lock:
            return dict(event='session-state',protocol_version=2,device_cid_sha256=self.cid,
                        policy_received=self.policy,configuration_received=self.configured,**self.slots)

    def submit(self,line):
        with self.lock:
            if self.finished or self.pending is None:return
            kind=self.pending[1]['event']
            if not line.startswith(self.requests[kind]) or not line.endswith(b'\n') or len(line)>32768:return
            self.child.stdin.write(line);self.child.stdin.flush()
            if kind=='need-policy':self.policy=True
            elif kind=='need-config':self.configured=True
            self.pending=None


def event_bytes(event):
    return ('PHOENIX_EVENT '+json.dumps(event)+'\n').encode()


def serve_connection(connection,config,session):
    connection.settimeout(10)
    stream=connection.makefile('rwb',buffering=0)
    stopped=threading.Event()
    try:
        authentication=stream.readline(160)
        if not hmac.compare_digest(authentication,('PHOENIX_AUTH '+config['token']+'\n').encode()):return
        connection.settimeout(None)
        stream.write(b'PHOENIX_NET_READY 2\n')
        # Snapshot and replay are ordered with input acceptance and process output.
        with session.lock:
            stream.write(event_bytes(session.state()))
            cursor=0
        session.start()

        def receive():
            try:
                while not stopped.is_set():
                    line=stream.readline(32769)
                    if not line or len(line)>32768:break
                    session.submit(line)
                    line=None
            except (OSError,ValueError):pass
            finally:stopped.set()

        reader=threading.Thread(target=receive,daemon=True);reader.start()
        pending_sent=None;heartbeat=time.monotonic()
        while not stopped.is_set():
            with session.lock:
                updates=[(seq,event) for seq,event in session.events if seq>cursor]
                pending=session.pending;finished=session.finished
            for seq,event in updates:
                stream.write(event_bytes(event));cursor=seq
            if pending and pending[0]!=pending_sent:
                stream.write(event_bytes(pending[1]));pending_sent=pending[0]
            if finished:break
            if time.monotonic()-heartbeat>=10:
                stream.write(event_bytes({'event':'connection-alive'}));heartbeat=time.monotonic()
            stopped.wait(.05)
    except (OSError,ValueError):pass
    finally:
        stopped.set()
        try:connection.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        stream.close()
        if 'reader' in locals():reader.join(1)


def main():
    os.umask(0o077)
    root=Path('/var/lib/phoenix')
    config=validate_config(json.loads((root/'setup-network.json').read_text()))
    actual=Path('/sys/class/block/mmcblk0/device/cid').read_text().strip()
    if hashlib.sha256(actual.encode()).hexdigest()!=config['device_cid_sha256']:raise ValueError('Setup device identity differs')
    for command in (['ip','link','set','lo','up'],['ip','link','set','eth0','up'],['ip','addr','replace',config['device_ip']+'/30','dev','eth0']):subprocess.run(command,check=True)
    session=InstallerSession(root,config['device_cid_sha256'])
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind((config['device_ip'],config['port']));server.listen(1)
        print('Phoenix direct-link installer is available for its paired PC; reconnect after closing the app.',flush=True)
        while True:
            connection,peer=server.accept()
            with connection:
                if peer[0]==config['host_ip']:serve_connection(connection,config,session)


if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('PHOENIX_NETWORK_STOPPED: '+type(error).__name__,flush=True)
        raise SystemExit(1)
