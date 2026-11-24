import base64,io,json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import stage2


class RepairTests(unittest.TestCase):
 def test_repairable_step_waits_and_retries_only_that_step(self):
  attempts=[];repairs=[]
  def operation():
   attempts.append(1)
   if len(attempts)<3:raise stage2.RecoverableError('offline')
   return 'ready'
  with patch.object(stage2,'request_retry',side_effect=lambda code,message:repairs.append(code)):
   self.assertEqual(stage2.retry_step('ethernet','Connect cable',operation),'ready')
  self.assertEqual(len(attempts),3);self.assertEqual(repairs,['ethernet','ethernet'])

 def test_unsafe_validation_errors_are_never_turned_into_retry_bypasses(self):
  with patch.object(stage2,'request_retry') as prompt:
   for error in (ValueError('Unsupported layout'),stage2.ExistingInstallationError('Existing installation')):
    with self.assertRaises(ValueError):stage2.retry_step('hdd','fix',lambda:(_ for _ in ()).throw(error))
   prompt.assert_not_called()

 def test_retry_decision_is_bound_to_the_current_checkpoint(self):
  request='a'*16
  for value,expected in (({'retry_id':request,'retry':True},None),({'retry_id':'b'*16,'retry':True},ValueError),({'retry_id':request,'retry':False},InterruptedError)):
   line='PHOENIX_RETRY '+base64.b64encode(json.dumps(value).encode()).decode()+'\n'
   with patch.object(stage2.secrets,'token_hex',return_value=request),patch.object(stage2.sys,'stdin',io.StringIO(line)),patch.object(stage2,'event') as event:
    if expected:
     with self.assertRaises(expected):stage2.request_retry('usb-missing','Insert USB')
    else:stage2.request_retry('usb-missing','Insert USB')
    self.assertEqual(event.call_args_list[0].kwargs['retry_id'],request)

 def test_disconnected_ethernet_is_retried_without_disk_operations(self):
  attempts=[];commands=[];carrier=['0','1']
  def network():
   attempts.append(1);return stage2.configure_network()
  def run(*args,**kwargs):commands.append(args);return SimpleNamespace(stdout='2: eth0 inet 192.0.2.2/24\n')
  with patch.object(stage2,'ETHERNET',False),patch.object(Path,'read_text',side_effect=lambda:carrier.pop(0)),patch.object(stage2,'run',side_effect=run),patch.object(stage2,'request_retry') as prompt:
   stage2.retry_step('ethernet','Connect cable',network)
  self.assertEqual(len(attempts),2);prompt.assert_called_once()
  self.assertEqual(sum(c[0]=='udhcpc' for c in commands),1)
  self.assertFalse(any(c[0] in ('mount','umount','dd') for c in commands))

 def test_wrong_usb_payload_is_refused_until_original_is_reinserted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'phoenix').mkdir();manifest=root/'phoenix/SHA256SUMS';manifest.write_bytes(b'original')
   expected=stage2.harvest.sha(manifest);manifest.write_bytes(b'other USB')
   with patch.object(stage2,'USB',root),patch.object(stage2,'locate_usb',return_value='/dev/sdc1'):
    with self.assertRaises(stage2.RecoverableError):stage2.ensure_usb(expected)
    manifest.write_bytes(b'original');self.assertEqual(stage2.ensure_usb(expected),'/dev/sdc1')

 def test_usb_detection_unmounts_an_unrelated_stick_and_rechecks(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'phoenix').mkdir();calls=[]
   def command(args,**kwargs):
    calls.append(args)
    marker=root/'phoenix/usb-id.txt'
    if args[0]=='mount':marker.write_text('PHOENIX_INSTALLER_USB_V2' if Path(args[5]).name=='sdc1' else 'OTHER')
    elif args[0]=='umount':marker.unlink(missing_ok=True)
    return SimpleNamespace(returncode=0)
   with patch.object(stage2,'USB',root),patch.object(stage2,'usb_mount',return_value=None),patch.object(Path,'glob',return_value=[Path('/dev/sdb1'),Path('/dev/sdc1')]),patch.object(stage2.subprocess,'run',side_effect=command):
    self.assertEqual(stage2.locate_usb(),str(Path('/dev/sdc1')))
   self.assertEqual(sum(c[0]=='umount' for c in calls),1)

 def test_existing_installation_can_be_repaired_but_not_overwritten_by_retry(self):
  with tempfile.TemporaryDirectory() as d:
   disk=Path(d);(disk/'alpine-system').mkdir();(disk/'alpine-system/keep').write_bytes(b'old system')
   repairs=[]
   def repair(code,message,**fields):
    self.assertTrue((disk/'alpine-system/keep').exists());repairs.append(code)
    (disk/'alpine-system').rename(disk/'operator-backup')
   with patch.object(stage2,'DISK',disk),patch.object(stage2,'require_readonly_disk'),patch.object(stage2,'maintenance_access',return_value={'ready':False}),patch.object(stage2,'request_retry',side_effect=repair):
    stage2.review_initial_target()
   self.assertEqual(repairs,['existing-installation']);self.assertEqual((disk/'operator-backup/keep').read_bytes(),b'old system')

 def test_space_check_never_uses_ram_or_a_different_mounted_disk(self):
  with patch.object(Path,'read_text',return_value=''),patch.object(stage2.shutil,'disk_usage') as usage:
   with self.assertRaises(stage2.RecoverableError):stage2.check_space()
   usage.assert_not_called()
  with patch.object(Path,'read_text',return_value='/dev/sdb1 '+str(stage2.DISK)+' xfs ro 0 0\n'):
   with self.assertRaisesRegex(ValueError,'Unexpected filesystem'):stage2.check_space()

 def test_post_commit_repairs_finish_usb_without_repeating_cutover(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);usb=root/'usb';work=root/'work';disk=root/'disk';(usb/'phoenix').mkdir(parents=True);work.mkdir();disk.mkdir()
   retries=[];commands=[];original=Path.write_bytes;failed_usb=[False];failed_unmount=[False]
   def write_bytes(path,data):
    if path==usb/'phoenix/boot.txt' and not failed_usb[0]:failed_usb[0]=True;raise OSError('USB removed after first boot file')
    return original(path,data)
   def command(*args,**kwargs):
    commands.append(args)
    if args==('umount',str(disk)) and not failed_unmount[0]:failed_unmount[0]=True;raise subprocess.CalledProcessError(1,args)
   receipt={'event':'complete','message':'Verified'}
   with patch.object(stage2,'USB',usb),patch.object(stage2,'DISK',disk),patch.object(stage2,'ensure_usb',return_value='/dev/sdc1'),patch.object(stage2,'request_retry',side_effect=lambda code,message:retries.append(code)),patch.object(stage2,'run',side_effect=command),patch.object(stage2.os,'sync',create=True),patch.object(stage2,'event') as event,patch.object(stage2,'commit_initial') as commit,patch.object(Path,'write_bytes',write_bytes):
    stage2.finish_committed(work,receipt,b'new verified boot','manifest',0,0)
    commit.assert_not_called();event.assert_called_once_with('complete',message='Verified')
   self.assertEqual(retries,['usb-write','hdd-busy'])
   self.assertEqual((usb/'phoenix/b00.txt').read_bytes(),b'new verified boot');self.assertEqual((usb/'phoenix/boot.txt').read_bytes(),b'new verified boot')
   self.assertEqual(json.loads((work/'installation-receipt.json').read_text()),receipt)
   self.assertEqual(commands.count(('umount',str(disk))),2)

 def test_invalid_timezone_can_be_corrected_without_logging_passwords(self):
  from test_bootstrap import ConfigTests
  config=ConfigTests().config();config.update(timezone='UTC',root_password='unique-root-secret',tvh_password='unique-tv-secret');bad=dict(config,timezone='Invalid/Timezone')
  def encoded(value):return 'PHOENIX_CONFIG '+base64.b64encode(json.dumps(value).encode()).decode()+'\n'
  reply='PHOENIX_RETRY '+base64.b64encode(json.dumps({'retry_id':'a'*16,'retry':True}).encode()).decode()+'\n'
  output=io.StringIO()
  with patch.object(stage2,'ETHERNET',True),patch.object(stage2.sys,'stdin',io.StringIO(encoded(bad)+reply+encoded(config))),patch.object(stage2.sys,'stdout',output),patch.object(stage2.secrets,'token_hex',return_value='a'*16),patch.object(stage2,'ZoneInfo',side_effect=[stage2.ZoneInfoNotFoundError('unknown'),object()]):
   self.assertEqual(stage2.receive_config('disk',30*1024**3)['timezone'],'UTC')
  text=output.getvalue();self.assertEqual(text.count('"event": "need-config"'),2);self.assertIn('"code": "configuration"',text)
  self.assertNotIn(config['root_password'],text);self.assertNotIn(config['tvh_password'],text)
