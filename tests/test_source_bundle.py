import getpass,hashlib,importlib.util,io,json,platform,tarfile,tempfile,unittest,zipfile
from pathlib import Path,PureWindowsPath
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/name);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
export=load('package-source.py');runtime=load('runtime-source.py');inputs=load('import-runtime.py')

class SourceBundleTests(unittest.TestCase):
 def test_current_export_excludes_standalone_utility(self):
  files=export.entries(ROOT)
  forbidden=('AccessBoot','AccessProgram','AccessTests','access-stage1','access-restore',
             'access-service','build-access','import-access','package-access','test_access','ACCESS.md')
  for name in files:
   self.assertFalse(any(marker in name for marker in forbidden),name)
  for name in ('src/Core.cs','src/EthernetDiscovery.cs','src/EthernetTransport.cs','src/Program.cs','src/Updates.cs'):
   self.assertIn(name,files)
  self.assertNotIn(b'partial class EthernetInstallLink',files['src/EthernetTransport.cs'])
 def test_images_reports_and_local_metadata_are_excluded(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'source';(root/'src').mkdir(parents=True);(root/'src/app.cs').write_text('class App {}')
   (root/'build').mkdir();(root/'build/private.json').write_text('local report')
   (root/'baseline').mkdir();(root/'baseline/image.img').write_bytes(b'image')
   (root/'inputs').mkdir();(root/'inputs/runtime.zip').write_bytes(b'image')
   (root/'tools/result-local').mkdir(parents=True);(root/'tools/result-local/session.json').write_text('{"local_report":true}')
   out=Path(d)/'source.zip';export.create(root,out)
   with zipfile.ZipFile(out) as archive:
    self.assertEqual(set(archive.namelist()),{'src/app.cs','SHA256SUMS'})
    self.assertTrue(all(m.date_time==(1980,1,1,0,0,0) and not m.extra and not m.comment for m in archive.infolist()))
 def test_private_host_paths_are_rejected(self):
  path=str(PureWindowsPath(chr(88)+':/')/'Users'/'fixture'/'source')
  with self.assertRaisesRegex(ValueError,'privacy audit'):export.audit({'src/app.cs':path.encode()})
 def test_runtime_import_verifies_every_asset_before_creating_output(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);archive=root/'input.zip';receipt={'baseline_files_sha256':{}}
   with zipfile.ZipFile(archive,'w') as z:
    for name in inputs.ASSETS:
     relative='usb-payload/phoenix/'+name;data=name.encode();z.writestr(relative,data);receipt['baseline_files_sha256'][relative]=hashlib.sha256(data).hexdigest()
   inputs.import_assets(archive,root/'baseline',receipt)
   self.assertEqual(len(list((root/'baseline/usb-payload/phoenix').iterdir())),4)
   receipt['baseline_files_sha256']['usb-payload/phoenix/base-rootfs.tar.gz']='0'*64
   with self.assertRaisesRegex(ValueError,'hash mismatch'):inputs.import_assets(archive,root/'rejected',receipt)
   self.assertFalse((root/'rejected').exists())
 def test_runtime_source_is_anonymous_and_binary_payload_is_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);original=root/'runtime.tar.gz';output=root/'clean.tar.gz'
   label='box'+chr(45)+'one'
   files={'MANIFEST.json':json.dumps({'rootfs_sha256':'0'*64,'target':label}).encode(),'project/dependencies/catalog.json':b'{"target_firmware":{}}','project/dependencies/browser-files.sha256':('0'*64+'  bin/irkeymap/legacy.xml\n'+'1'*64+'  bin/resources.pak\n').encode(),'source.py':('"""For '+label+'."""\n').encode(),'payload/apps.tar.gz':b'opaque compiled payload'}
   with tarfile.open(original,'w:gz') as archive:
    for name,data in files.items():
     item=tarfile.TarInfo('runtime/'+name);item.size=len(data);item.uname=getpass.getuser();item.gname=platform.node();item.mtime=123456;archive.addfile(item,io.BytesIO(data))
   runtime.repack(original,output);_,cleaned,_=runtime.read_package(output)
   self.assertEqual(cleaned['payload/apps.tar.gz'],files['payload/apps.tar.gz']);self.assertNotIn(label.encode(),cleaned['source.py']);self.assertNotIn(b'legacy.xml',cleaned['project/dependencies/browser-files.sha256'])
   self.assertTrue(json.loads(cleaned['project/dependencies/catalog.json'])['target_firmware']['remote_maps_directory_required'])
   with tarfile.open(output) as archive:self.assertTrue(all(m.uid==0 and m.gid==0 and m.uname=='root' and m.gname=='root' and m.mtime==0 for m in archive))
   runtime.export(original,root/'source');self.assertFalse((root/'source/payload').exists());export.audit(export.entries(root/'source'))
