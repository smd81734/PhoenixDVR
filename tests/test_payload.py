import hashlib,importlib.util,io,json,shlex,struct,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('payload_builder',ROOT/'tools/build-payload.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class PayloadTests(unittest.TestCase):
 def test_checksum_file_is_lf_bytes_and_manifest_matches_it(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,_=self.fixture(root);out=root/'out';manifest=builder.build(package,out,minimal=False)
   data=(out/'phoenix/SHA256SUMS').read_bytes();self.assertNotIn(b'\r',data);self.assertTrue(data.endswith(b'\n'));self.assertFalse(data.startswith(b'\xef\xbb\xbf'))
   entry=next(e for e in manifest['Files'] if e['Path']=='phoenix/SHA256SUMS');self.assertEqual(entry['Sha256'],hashlib.sha256(data).hexdigest())
 def test_diagnostic_boot_uses_explicit_kernel_mode_and_is_checksum_covered(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,_=self.fixture(root);out=root/'out';manifest=builder.build(package,out,minimal=False)
   command=(out/'phoenix/diagnostics.txt').read_text();bolt=shlex.split(command)
   self.assertEqual(bolt[:2],['boot','flash0.kernel0']);self.assertEqual(len(bolt),3)
   self.assertIn('phoenix.diagnostics=1',shlex.split(bolt[2]));self.assertNotIn('phoenix.diagnostics=1',(out/'phoenix/install.txt').read_text())
   for name in ('diagnostics.txt','diagnostics-init.sh'):
    entry=next(e for e in manifest['Files'] if e['Path']=='phoenix/'+name);self.assertEqual(entry['Sha256'],builder.sha(out/'phoenix'/name))
 def fixture(self,root,arm=True):
  elf=bytearray(20);elf[:6]=b'\x7fELF\x01\x01';struct.pack_into('<H',elf,18,40 if arm else 62)
  raw=io.BytesIO()
  with tarfile.open(fileobj=raw,mode='w:gz') as archive:
   for name in ('bin/busybox','lib/ld-musl-armhf.so.1'):
    m=tarfile.TarInfo('./'+name);m.size=len(elf);archive.addfile(m,io.BytesIO(elf))
  base=raw.getvalue();package=root/'package.tar.gz'
  with tarfile.open(package,'w:gz') as archive:
   for name,data in [('MANIFEST.json',json.dumps(dict(id='synthetic',rootfs_sha256=hashlib.sha256(base).hexdigest())).encode()),('payload/base-rootfs.tar.gz',base),('project/source/boot/hdd/boot.txt',b'boot-placeholder')]:
    m=tarfile.TarInfo('release/'+name);m.size=len(data);archive.addfile(m,io.BytesIO(data))
  Path(str(package)+'.sha256').write_text(builder.sha(package)+'  '+package.name)
  return package,bytes(elf)
 def test_boot_command_preserves_one_bash_script_argument_without_literal_quotes(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,_=self.fixture(root);builder.build(package,root/'out',minimal=False)
   command=(root/'out/phoenix/install.txt').read_text();bolt=shlex.split(command)
   self.assertEqual(bolt[:2],['boot','flash0.kernel0']);self.assertEqual(len(bolt),3)
   kernel=shlex.split(bolt[2]);start=kernel.index('--');self.assertEqual(kernel[start+1],'-c');self.assertEqual(len(kernel[start+2:]),1)
   script=kernel[-1];self.assertTrue(script.startswith('shopt -s execfail;'));self.assertTrue(script.endswith('exec /bin/bash -i'));self.assertNotIn('\\"',command)
 def test_public_verifiers_are_copied_exactly_and_checksum_covered(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,elf=self.fixture(root);out=root/'out';manifest=builder.build(package,out,minimal=False)
   for name in ('check-busybox','check-ld-musl-armhf.so.1'):
    p=out/'phoenix'/name;self.assertEqual(p.read_bytes(),elf)
    entry=next(e for e in manifest['Files'] if e['Path']=='phoenix/'+name);self.assertEqual(entry['Sha256'],builder.sha(p))
    self.assertIn(builder.sha(p)+'  phoenix/'+name,(out/'phoenix/SHA256SUMS').read_text())
 def test_non_arm_verifier_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,_=self.fixture(root,arm=False)
   with self.assertRaisesRegex(ValueError,'ARM ELF32'):builder.build(package,root/'out',minimal=False)
