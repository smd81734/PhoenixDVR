import hashlib,importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('minimal_package',ROOT/'tools/minimal-package.py');mini=importlib.util.module_from_spec(spec);spec.loader.exec_module(mini)

class MinimalPackageTests(unittest.TestCase):
 def fixture(self,root):
  buffer=io.BytesIO()
  with tarfile.open(fileobj=buffer,mode='w:gz') as a:
   for name,data in [('usr/bin/needed',b'needed'),('usr/share/README.md',b'documentation')]:
    m=tarfile.TarInfo(name);m.size=len(data);a.addfile(m,io.BytesIO(data))
  base=buffer.getvalue();files={name:b'required' for name in mini.REQUIRED}
  files['project/dependencies/catalog.json']=json.dumps({'downloads':{'fonts':[{'name':'font.ttf','sha256':'0'*64}],'unneeded_inventory':['unused']},'target_firmware':{'module_sha256':'1'*64}}).encode()
  files.update({'MANIFEST.json':json.dumps({'id':'fixture','rootfs_sha256':hashlib.sha256(base).hexdigest()}).encode(),'payload/base-rootfs.tar.gz':base,'project/source/boot/rootfs/etc/inittab':b'init','reports/report.json':b'report','README.md':b'documentation','project/tests/test.py':b'test','project/source/native.c':b'C source'})
  package=root/'source.tar.gz'
  with tarfile.open(package,'w:gz') as a:
   for name,data in files.items():
    m=tarfile.TarInfo('release/'+name);m.size=len(data);a.addfile(m,io.BytesIO(data))
  return package
 def test_only_required_members_and_virtual_base_manifest_are_kept(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package=self.fixture(root);mini.create(package,root/'minimal.tar.gz',root/'base.tar.gz')
   with tarfile.open(root/'minimal.tar.gz') as a:
    names={m.name.removeprefix('release/') for m in a if m.isfile()}
    self.assertEqual(names,mini.REQUIRED|{'project/source/boot/rootfs/etc/inittab','MANIFEST.json','SHA256SUMS'})
    self.assertNotIn('payload/base-rootfs.tar.gz',names)
    sums=a.extractfile('release/SHA256SUMS').read().decode();self.assertIn(hashlib.sha256((root/'base.tar.gz').read_bytes()).hexdigest()+'  payload/base-rootfs.tar.gz',sums)
 def test_base_documentation_is_removed_without_changing_runtime_content(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package=self.fixture(root);mini.create(package,root/'minimal.tar.gz',root/'base.tar.gz')
   with tarfile.open(root/'base.tar.gz') as a:
    self.assertEqual([m.name for m in a if m.isfile()],['usr/bin/needed']);self.assertEqual(a.extractfile('usr/bin/needed').read(),b'needed')
