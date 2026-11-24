import base64,contextlib,importlib.util,io,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recovery',ROOT/'bootstrap/recovery.py')
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)

class RecoveryTests(unittest.TestCase):
 def key_input(self):return 'PHOENIX_RECOVERY_KEY '+base64.b64encode(b'ssh-ed25519 QUJD test').decode()+'\n'
 def test_invalid_key_is_rejected_before_any_system_command(self):
  with patch.object(recovery.sys,'stdin',io.StringIO('PHOENIX_RECOVERY_KEY YmFk\n')),patch.object(recovery.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaises(ValueError):recovery.start(True)
   run.assert_not_called()
 def run_access(self,ethernet,repeat=False):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);calls=[]
   def path(value):
    self.assertIn(value,('/run/phoenix-recovery','/root/.ssh','/proc'))
    return root/value.lstrip('/')
   def command(args,**kwargs):
    calls.append((args,kwargs))
    if args[:2]==['ip','-4']:return subprocess.CompletedProcess(args,0,'2: eth0 inet 169.254.3.1/30 scope global eth0\n','')
    if args[:2]==['dropbearkey','-y']:return subprocess.CompletedProcess(args,0,'Fingerprint: SHA256:QUJD\n','')
    if args[:2]==['dropbearkey','-t']:Path(args[args.index('-f')+1]).write_bytes(b'host-key-fixture')
    if args[0]=='dropbear':
     Path(args[args.index('-P')+1]).write_text('123')
     cmd=root/'proc/123/cmdline';cmd.parent.mkdir(parents=True,exist_ok=True);cmd.write_bytes(b'dropbear\0-r\0'+args[args.index('-r')+1].encode()+b'\0')
    return subprocess.CompletedProcess(args,0,'','')
   (root/'root').mkdir()
   output=io.StringIO()
   with patch.object(recovery,'Path',side_effect=path),patch.object(recovery.sys,'stdin',io.StringIO(self.key_input()*(2 if repeat else 1))),patch.object(recovery.subprocess,'run',side_effect=command),patch.object(recovery.socket,'create_connection',return_value=MagicMock()),contextlib.redirect_stdout(output):
    access=recovery.start(ethernet)
    if repeat:self.assertEqual(access,recovery.start(ethernet))
   self.assertTrue(access['ready']);self.assertEqual(access['address'],'169.254.3.1');self.assertEqual(access['host_fingerprint'],'SHA256:QUJD')
   ssh=next(args for args,k in calls if args[0]=='dropbear');self.assertIn('-s',ssh);self.assertIn('169.254.3.1:22',ssh)
   self.assertEqual((root/'root/.ssh/authorized_keys').read_bytes(),b'ssh-ed25519 QUJD test\n')
   self.assertFalse(any(args[0] in ('mount','umount') for args,k in calls))
   password=next(k['input'] for args,k in calls if args[0]=='chpasswd');self.assertNotIn(password,output.getvalue())
   self.assertEqual(any(args[0]=='udhcpc' for args,k in calls),not ethernet)
   if repeat:
    self.assertEqual(sum(args[:2]==['dropbearkey','-t'] for args,k in calls),1)
    self.assertEqual(sum(args[0]=='dropbear' for args,k in calls),1)
 def test_direct_access_uses_existing_bridge_address_and_key_only_auth(self):self.run_access(True)
 def test_uart_access_obtains_lan_dhcp_address(self):self.run_access(False)
 def test_repeated_repair_preserves_ssh_host_identity_and_existing_server(self):self.run_access(True,repeat=True)
