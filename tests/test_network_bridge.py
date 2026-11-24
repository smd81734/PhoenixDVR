import hashlib,importlib.util,io,json,os,socket,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).parents[1]
def load(name):
 s=importlib.util.spec_from_file_location('phoenix_'+name.replace('-','_'),ROOT/'bootstrap'/name)
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
class NetworkBridgeTests(unittest.TestCase):
 def config(self):return {'version':1,'device_ip':'169.254.3.1','host_ip':'169.254.3.2','port':18991,'token':'1'*64,'device_cid_sha256':'2'*64}
 def test_only_direct_subnet_and_session_identity_allowed(self):
  m=load('network-bridge.py');m.validate_config(self.config())
  for change in ({'device_ip':'192.168.1.1'},{'host_ip':'169.254.4.2'},{'host_ip':'169.254.3.3'},{'token':'bad'},{'device_cid_sha256':'x'*64},{'port':22}):
   c=self.config();c.update(change)
   with self.assertRaises(ValueError):m.validate_config(c)
 def session(self,root):
  m=load('network-bridge.py');config=self.config()
  code="""import sys,time
from pathlib import Path
print('PHOENIX_EVENT {"event":"progress","kernel_slot":0,"root_slot":0}',flush=True)
print('PHOENIX_EVENT {"event":"need-policy","protocol_version":2}',flush=True)
assert sys.stdin.readline().startswith('PHOENIX_POLICY ')
print('PHOENIX_EVENT {"event":"need-config","echo_disabled":true}',flush=True)
assert sys.stdin.readline().startswith('PHOENIX_CONFIG ')
print('PHOENIX_EVENT {"event":"config-accepted"}',flush=True)
while not Path(__file__).with_name('finish').exists():time.sleep(.02)
print('PHOENIX_EVENT {"event":"complete"}',flush=True)
"""
  (root/'stage2.py').write_text(code)
  session=m.InstallerSession(root,config['device_cid_sha256'])
  def cleanup():
   if session.child:
    if session.child.poll() is None:session.child.terminate()
    session.child.wait(5)
    session.worker.join(5)
  self.addCleanup(cleanup)
  return m,config,session
 def connect(self,m,config,session,valid=True):
  pc,device=socket.socketpair();pc.settimeout(5)
  def run():
   with device:m.serve_connection(device,config,session)
  thread=threading.Thread(target=run,daemon=True);thread.start()
  stream=pc.makefile('rwb',buffering=0)
  stream.write(('PHOENIX_AUTH '+(config['token'] if valid else '0'*64)+'\n').encode())
  if valid:
   self.assertEqual(stream.readline(),b'PHOENIX_NET_READY 2\n')
   state=json.loads(stream.readline()[14:]);self.assertEqual(state['event'],'session-state')
  else:state=None
  def close():
   try:pc.shutdown(socket.SHUT_RDWR)
   except OSError:pass
   stream.close();pc.close();thread.join(5)
   self.assertFalse(thread.is_alive())
  self.addCleanup(close)
  return stream,state,close
 def until(self,stream,kind):
  for _ in range(20):
   line=stream.readline();self.assertTrue(line)
   event=json.loads(line[14:])
   if event['event']==kind:return event
  self.fail('Expected '+kind)
 def test_reconnect_preserves_prompts_staging_and_completed_receipt(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);m,config,session=self.session(root)
   stream,state,close=self.connect(m,config,session);self.until(stream,'need-policy');pid=session.child.pid;close()
   self.assertIsNone(session.child.poll());self.assertFalse(session.child.stdin.closed)
   stream,state,close=self.connect(m,config,session);self.assertFalse(state['policy_received'])
   self.until(stream,'need-policy');stream.write(b'PHOENIX_POLICY e30=\n');self.until(stream,'need-config');close()
   stream,state,close=self.connect(m,config,session);self.assertTrue(state['policy_received']);self.assertFalse(state['configuration_received'])
   self.until(stream,'need-config');stream.write(b'PHOENIX_CONFIG never-retain-this-input\n');self.until(stream,'config-accepted');close()
   stream,state,close=self.connect(m,config,session);self.assertTrue(state['configuration_received']);self.assertEqual(session.child.pid,pid)
   (root/'finish').touch();self.until(stream,'complete');close()
   stream,state,close=self.connect(m,config,session);self.assertTrue(state['configuration_received']);self.until(stream,'complete');close()
   self.assertNotIn('never-retain-this-input',repr(session.events));self.assertEqual(session.child.pid,pid)
 def test_bad_authentication_never_starts_the_installer(self):
  with tempfile.TemporaryDirectory() as folder:
   m,config,session=self.session(Path(folder));stream,state,close=self.connect(m,config,session,False)
   self.assertEqual(stream.readline(),b'');close();self.assertIsNone(session.child)
 def test_unexpected_or_duplicate_inputs_do_not_advance_pending_request(self):
  with tempfile.TemporaryDirectory() as folder:
   m,config,session=self.session(Path(folder));stream,state,close=self.connect(m,config,session)
   self.until(stream,'need-policy');session.submit(b'PHOENIX_CONFIG wrong-order\n')
   self.assertFalse(session.configured);self.assertEqual(session.pending[1]['event'],'need-policy')
   stream.write(b'PHOENIX_POLICY e30=\n');self.until(stream,'need-config')
   session.submit(b'PHOENIX_POLICY duplicate\n');self.assertEqual(session.pending[1]['event'],'need-config');close()
 def test_failed_session_is_replayed_without_restarting(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);m,config,session=self.session(root)
   (root/'stage2.py').write_text("print('PHOENIX_EVENT {\"event\":\"failed\",\"message\":\"stopped safely\"}',flush=True)\n")
   stream,state,close=self.connect(m,config,session);self.until(stream,'failed');pid=session.child.pid;close()
   stream,state,close=self.connect(m,config,session);self.until(stream,'failed');self.assertEqual(session.child.pid,pid);close()
 def test_stage2_pipe_config_does_not_use_terminal_api(self):
  # Test the actual receive_config with a pipe-like input, rather than mirroring it.
  import sys,types
  spec=importlib.util.spec_from_file_location('harvest',ROOT/'bootstrap/harvest.py');harvest=importlib.util.module_from_spec(spec);spec.loader.exec_module(harvest)
  with patch.dict(sys.modules,{'harvest':harvest}),patch.dict(os.environ,{'PHOENIX_ETHERNET_INSTALL':'1'}):m=load('stage2.py')
  import base64
  config={'hostname':'phoenix','timezone':'UTC','band_plan':'au','root_password':'sample-root','tvh_password':'sample-tvh','utc_seconds':1790000000,'authorized_key':'ssh-ed25519 sample'}
  line='PHOENIX_CONFIG '+base64.b64encode(json.dumps(config).encode()).decode()+'\n'
  with patch.object(m.sys,'stdin',io.StringIO(line)),patch.object(m.sys,'stdout',io.StringIO()):self.assertEqual(m.receive_config('disk',30)['hostname'],'phoenix')

 def test_repair_prompt_survives_disconnect_and_reconnect(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);m,config,session=self.session(root)
   (root/'stage2.py').write_text("import sys\nprint('PHOENIX_EVENT {\"event\":\"need-retry\",\"protocol_version\":1,\"retry_id\":\"aaaaaaaaaaaaaaaa\",\"code\":\"usb-missing\"}',flush=True)\nassert sys.stdin.readline().startswith('PHOENIX_RETRY ')\nprint('PHOENIX_EVENT {\"event\":\"failed\",\"message\":\"test finished\"}',flush=True)\n")
   stream,state,close=self.connect(m,config,session);first=self.until(stream,'need-retry');pid=session.child.pid;close()
   stream,state,close=self.connect(m,config,session);second=self.until(stream,'need-retry');self.assertEqual(first['retry_id'],second['retry_id'])
   stream.write(b'PHOENIX_RETRY e30=\n');self.until(stream,'failed');self.assertEqual(session.child.pid,pid);close()

 def test_rejected_configuration_is_not_reported_as_accepted_on_reconnect(self):
  with tempfile.TemporaryDirectory() as folder:
   m,config,session=self.session(Path(folder));session.configured=True
   session.record({'event':'need-retry','code':'configuration','retry_id':'a'*16})
   self.assertFalse(session.state()['configuration_received'])

if __name__=='__main__':unittest.main()
