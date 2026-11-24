import importlib.util,json,sys,tempfile,unittest,base64,io,contextlib,struct
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bootstrap'))
import harvest,stage2

def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
class HarvestTests(unittest.TestCase):
 def test_hash_match_is_independent_of_vendor_path(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);write(root/'stock/odd/location','vendor');digest=harvest.sha(root/'stock/odd/location');found=harvest.index([root/'stock'],{digest});self.assertEqual(found[digest],root/'stock/odd/location')
 def test_duplicate_contents_can_supply_multiple_destinations(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);write(root/'stock/file','vendor');digest=harvest.sha(root/'stock/file');out=harvest.copy([(digest,'runtime/a'),(digest,'fonts/b')],{digest:root/'stock/file'},root/'out');self.assertEqual((out/'runtime/a').read_text(),(out/'fonts/b').read_text())
 def test_missing_hash_is_not_silently_replaced(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);write(root/'stock/file','wrong');self.assertEqual(harvest.index([root/'stock'],{'a'*64}),{})
 def test_manifest_traversal_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'manifest';p.write_text('a'*64+'  ../escape\n')
   with self.assertRaisesRegex(ValueError,'Unsafe'):harvest.manifest(p)
 def test_copy_refuses_existing_destination(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'already exists'):harvest.copy([],{},Path(d))
 def test_modified_source_is_detected_after_copy(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);write(root/'source','before');digest=harvest.sha(root/'source');(root/'source').write_text('after')
   with self.assertRaisesRegex(ValueError,'verification'):harvest.copy([(digest,'file')],{digest:root/'source'},root/'out')
class ConfigTests(unittest.TestCase):
 def config(self):return dict(hostname='phoenix',band_plan='au',timezone='Australia/Sydney',root_password='password',tvh_password='another',utc_seconds=1795040000,authorized_key='ssh-ed25519 AAAA test')
 def test_valid_config(self):self.assertEqual(stage2.validate_config(self.config())['hostname'],'phoenix')
 def test_control_character_password_refused(self):
  c=self.config();c['root_password']='bad\nline'
  with self.assertRaisesRegex(ValueError,'password'):stage2.validate_config(c)
 def test_hostname_shell_text_refused(self):
  c=self.config();c['hostname']='box;reboot'
  with self.assertRaisesRegex(ValueError,'hostname'):stage2.validate_config(c)
 def test_invalid_clock_refused(self):
  c=self.config();c['utc_seconds']=0
  with self.assertRaisesRegex(ValueError,'time'):stage2.validate_config(c)
 def test_usb_is_never_accepted_as_hdd(self):
  with self.assertRaisesRegex(ValueError,'USB occupies'):stage2.check_disk('/dev/sda1')
 def test_oversized_key_refused(self):
  c=self.config();c['authorized_key']='x'*3000
  with self.assertRaisesRegex(ValueError,'public key'):stage2.validate_config(c)
class InitialCommitTests(unittest.TestCase):
 def fixture(self,root):
  system=root/'candidate/alpine-system';(system/'data').mkdir(parents=True);(system/'data/new').write_text('new');disk=root/'disk';disk.mkdir();(disk/'existing-stock-recording').write_bytes(b'keep');return system,disk
 def test_success_retains_existing_disk_files(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   system,disk=self.fixture(Path(d));stage2.commit_initial(system,disk,SimpleNamespace(initialize=lambda data,disk:(disk/'phoenix-pvr').mkdir()))
   self.assertEqual((disk/'existing-stock-recording').read_bytes(),b'keep');self.assertTrue((disk/'alpine-system/data/new').exists())
 def test_failed_settings_setup_rolls_back_core_and_keeps_evidence(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   system,disk=self.fixture(Path(d))
   def fail(data,disk):(disk/'phoenix-pvr').mkdir();raise OSError('disk full')
   with self.assertRaisesRegex(OSError,'disk full'):stage2.commit_initial(system,disk,SimpleNamespace(initialize=fail))
   self.assertTrue(system.is_dir());self.assertFalse((disk/'alpine-system').exists());self.assertFalse((disk/'phoenix-pvr').exists());self.assertTrue((system.parent/'failed-persistent-store').exists());self.assertEqual((disk/'existing-stock-recording').read_bytes(),b'keep')
 def test_existing_installation_is_never_overwritten(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   system,disk=self.fixture(Path(d));(disk/'alpine-system').mkdir()
   with self.assertRaisesRegex(ValueError,'Existing installation'):stage2.commit_initial(system,disk,SimpleNamespace(initialize=lambda *a:None))
   self.assertTrue(system.is_dir())

class RelaxedFirmwareTests(unittest.TestCase):
 def fixture(self,root):
  old=root/'stock/chrome/bin/webkitGl39';new=root/'stock/chrome/bin/webkitGl40';old.mkdir(parents=True);new.mkdir()
  header=bytearray(20);header[:6]=b'\x7fELF\x01\x01';struct.pack_into('<H',header,18,40)
  (old/'chrome.202504071556').write_bytes(header+b'old');(new/'chrome.202612010000').write_bytes(header+b'new')
  (new/'resources.pak').write_bytes(b'new resources');(old/'resources.pak').write_bytes(b'old resources');(root/'stock/lib').mkdir();(root/'stock/lib/libtest.so').write_bytes(b'updated library')
  rows=[(harvest.sha(old/'chrome.202504071556'),'runtime/opt/chromium/bin/chrome.202504071556'),(harvest.sha(old/'resources.pak'),'runtime/opt/chromium/bin/resources.pak'),('a'*64,'runtime/lib/libtest.so')]
  return old,new,rows
 def test_override_selects_new_browser_and_its_own_resources(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old,new,rows=self.fixture(root);report=[]
   with patch.object(harvest,'requirements',return_value=rows):chosen,found=harvest.check(root,[root/'stock'],ignore_mismatches=True,selection_report=report)
   names={name:digest for digest,name in chosen};self.assertIn('runtime/opt/chromium/bin/chrome.202612010000',names);self.assertEqual(found[names['runtime/opt/chromium/bin/resources.pak']],new/'resources.pak');self.assertTrue(any(not v['known_sha_matches'] for v in report))
 def test_override_still_refuses_missing_browser_resources(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old,new,rows=self.fixture(root);(new/'resources.pak').unlink()
   with patch.object(harvest,'requirements',return_value=rows):
    with self.assertRaisesRegex(ValueError,'Selected Chrome bundle is missing'):harvest.check(root,[root/'stock'],ignore_mismatches=True)
 def test_strict_mode_refuses_newer_browser_even_when_old_hash_is_available(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old,new,rows=self.fixture(root);rows[-1]=(harvest.sha(root/'stock/lib/libtest.so'),rows[-1][1])
   with patch.object(harvest,'requirements',return_value=rows):
    with self.assertRaisesRegex(ValueError,'Newest target Chrome'):harvest.check(root,[root/'stock'])
 def test_override_hashes_only_selected_files_not_all_target_files(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old,new,rows=self.fixture(root);(root/'stock/unrelated-private-file').write_bytes(b'not a dependency');actual=harvest.sha;hashed=[]
   def record(path):hashed.append(Path(path));return actual(path)
   with patch.object(harvest,'requirements',return_value=rows),patch.object(harvest,'sha',side_effect=record):harvest.check(root,[root/'stock'],ignore_mismatches=True)
   self.assertNotIn(root/'stock/unrelated-private-file',hashed)
 def test_policy_defaults_are_explicit_boolean(self):
  for value in (False,True):
   text='PHOENIX_POLICY '+base64.b64encode(json.dumps({'ignore_sha_mismatches':value}).encode()).decode()+'\n'
   with patch.object(stage2.sys,'stdin',io.StringIO(text)),contextlib.redirect_stdout(io.StringIO()):self.assertIs(stage2.receive_policy(),value)
 def test_invalid_policy_does_not_enable_override(self):
  text='PHOENIX_POLICY '+base64.b64encode(json.dumps({'ignore_sha_mismatches':'true'}).encode()).decode()+'\n'
  with patch.object(stage2.sys,'stdin',io.StringIO(text)),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaisesRegex(ValueError,'policy'):stage2.receive_policy()
 def test_non_arm_chrome_is_not_used_as_the_newest_browser(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old,new,rows=self.fixture(root);(new/'chrome.209901010000').write_bytes(b'not an ARM ELF');self.assertEqual(harvest.latest_browser(harvest.inventory([root/'stock'])).name,'chrome.202612010000')

if __name__=='__main__':unittest.main()

class ExistingTargetTests(unittest.TestCase):
 def test_clean_target_with_unrelated_recordings_is_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'recording.ts').write_bytes(b'keep');stage2.check_initial_target(root);self.assertEqual((root/'recording.ts').read_bytes(),b'keep')
 def test_existing_install_reports_paths_without_modifying_them(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);write(root/'alpine-system/data/config','preserve');write(root/'phoenix-pvr/recordings/show.ts','recording')
   with self.assertRaisesRegex(ValueError,'alpine-system, phoenix-pvr'):stage2.check_initial_target(root)
   self.assertEqual((root/'alpine-system/data/config').read_text(),'preserve');self.assertEqual((root/'phoenix-pvr/recordings/show.ts').read_text(),'recording')

class FontPolicyTests(unittest.TestCase):
 def font(self,path,content=b'font'):
  path.parent.mkdir(parents=True,exist_ok=True)
  path.write_bytes(struct.pack('>4sHHHH',b'\x00\x01\x00\x00',1,16,0,0)+struct.pack('>4sIII',b'head',0,28,len(content))+content)
 def fixture(self,root,bold='Texta-Heavy.ttf'):
  browser=root/'stock/chrome/bin/chrome.202504071556';browser.parent.mkdir(parents=True)
  elf=bytearray(20);elf[:6]=b'\x7fELF\x01\x01';struct.pack_into('<H',elf,18,40);browser.write_bytes(elf)
  native=root/'stock/lib/libnative.so';write(native,'native')
  rows=[(harvest.sha(browser),'runtime/opt/chromium/bin/'+browser.name),(harvest.sha(native),'runtime/lib/libnative.so')]
  for name in ('Texta-Light.ttf','Texta-Medium.ttf',bold):self.font(root/'stock/fonts'/name,name.encode())
  rows += [('0'*64,'fonts/'+n) for n in ('Texta-Light.ttf','Texta-Medium.ttf','Texta-Bold.ttf')]
  return rows
 def check(self,root,rows,**kwargs):
  with patch.object(harvest,'requirements',return_value=rows):return harvest.check(root,[root/'stock'],**kwargs)
 def test_different_font_hashes_are_accepted_in_strict_mode(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);report=[];selected,found=self.check(root,rows,selection_report=report)
   self.assertEqual(len(selected),5);self.assertTrue(all(not e['known_sha_matches'] for e in report));self.assertTrue(all(e['installed_sha256']!='0'*64 for e in report))
 def test_heavy_is_mapped_to_bold_and_copy_verified(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);selected,found=self.check(root,rows);out=harvest.copy(selected,found,root/'out')
   self.assertEqual((out/'fonts/Texta-Bold.ttf').read_bytes(),(root/'stock/fonts/Texta-Heavy.ttf').read_bytes())
 def test_exact_bold_is_preferred_when_present(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);self.font(root/'stock/fonts/Texta-Bold.ttf',b'bold');selected,found=self.check(root,rows)
   digest=next(h for h,n in selected if n=='fonts/Texta-Bold.ttf');self.assertEqual(found[digest].name,'Texta-Bold.ttf')
 def test_absent_font_is_still_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);(root/'stock/fonts/Texta-Light.ttf').unlink()
   with self.assertRaisesRegex(ValueError,'font is missing: Texta-Light'):self.check(root,rows)
 def test_non_font_is_still_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);(root/'stock/fonts/Texta-Light.ttf').write_bytes(b'not a font')
   with self.assertRaisesRegex(ValueError,'Invalid target font'):self.check(root,rows)
 def test_native_mismatch_still_fails_without_override(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);(root/'stock/lib/libnative.so').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'runtime/lib/libnative.so'):self.check(root,rows)
 def test_font_mutation_after_selection_fails_copy_integrity(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.fixture(root);selected,found=self.check(root,rows);self.font(root/'stock/fonts/Texta-Light.ttf',b'changed')
   with self.assertRaisesRegex(ValueError,'copy verification failed'):harvest.copy(selected,found,root/'out')
 def test_invalid_table_bounds_are_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'font.ttf';self.font(path);data=bytearray(path.read_bytes());struct.pack_into('>I',data,20,99999);path.write_bytes(data)
   with self.assertRaisesRegex(ValueError,'bounds'):harvest.validate_font(path)

class SharedBaseTests(unittest.TestCase):
 def fixture(self,root):
  package=root/'package';package.mkdir();source=root/'base.tar.gz';source.write_bytes(b'compressed base fixture')
  (package/'MANIFEST.json').write_text(json.dumps({'rootfs_sha256':harvest.sha(source)}));return package,source
 def test_shared_base_is_materialized_with_exact_copy_integrity(self):
  with tempfile.TemporaryDirectory() as d:
   package,source=self.fixture(Path(d));target=stage2.hydrate_base_rootfs(package,source);self.assertEqual(target.read_bytes(),source.read_bytes())
 def test_corrupt_shared_base_is_rejected_before_copy(self):
  with tempfile.TemporaryDirectory() as d:
   package,source=self.fixture(Path(d));source.write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'Shared Alpine image checksum'):stage2.hydrate_base_rootfs(package,source)
   self.assertFalse((package/'payload/base-rootfs.tar.gz').exists())
 def test_corrupt_materialized_base_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   package,source=self.fixture(Path(d));target=stage2.hydrate_base_rootfs(package,source);target.write_bytes(b'wrong')
   with self.assertRaisesRegex(ValueError,'Materialized Alpine image checksum'):stage2.hydrate_base_rootfs(package,source)
   self.assertEqual(target.read_bytes(),b'wrong')
