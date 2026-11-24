import importlib.util,io,struct,tarfile,tempfile,unittest,zipfile,hashlib
from pathlib import Path
spec=importlib.util.spec_from_file_location('release_hygiene',Path(__file__).parents[1]/'tools/verify-release.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

def pe(debug=False):
 b=bytearray(512);b[:2]=b'MZ';struct.pack_into('<I',b,60,64);b[64:68]=b'PE\0\0'
 opt=88;struct.pack_into('<H',b,opt,0x10b);struct.pack_into('<I',b,opt+92,16)
 if debug:struct.pack_into('<II',b,opt+96+6*8,0x2000,28)
 return bytes(b)

def elf(debug=False):
 names=b'\0.shstrtab\0'+(b'.debug_info\0' if debug else b'.text\0')
 b=bytearray(172+len(names));b[:6]=b'\x7fELF\x01\x01'
 struct.pack_into('<I',b,32,52);struct.pack_into('<HHH',b,46,40,3,1)
 struct.pack_into('<10I',b,92,1,3,0,0,172,len(names),0,0,1,0)
 struct.pack_into('<10I',b,132,11,1,0,0,172,0,0,0,1,0);b[172:]=names
 return bytes(b)

class HygieneTests(unittest.TestCase):
 def test_pe_release_and_debug_directory(self):
  self.assertEqual(audit.inspect_binary(pe(),'release.exe'),[])
  self.assertIn('PE debug directory',audit.inspect_binary(pe(True),'debug.exe'))
 def test_elf_debug_section(self):
  self.assertEqual(audit.inspect_binary(elf(),'release'),[])
  self.assertIn('ELF section .debug_info',audit.inspect_binary(elf(True),'debug'))
 def test_personal_path_ascii_and_utf16(self):
  for encoding in ('ascii','utf-16-le'):
   synthetic=str(__import__('pathlib').PureWindowsPath(chr(88)+':/')/'Users'/'fixture'/'work'/'app.cs')
   self.assertIn('Personal/local build path',audit.inspect_binary(pe()+synthetic.encode(encoding),'release.exe'))
 def test_nested_archive_is_checked(self):
  tar=io.BytesIO()
  with tarfile.open(fileobj=tar,mode='w:gz') as t:
   data=elf(True);m=tarfile.TarInfo('bin/app');m.size=len(data);t.addfile(m,io.BytesIO(data))
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'installer.zip'
   with zipfile.ZipFile(path,'w') as z:z.writestr('usb/payload.tar.gz',tar.getvalue())
   result=audit.verify([path]);self.assertFalse(result['passed']);self.assertEqual(result['stats']['binaries'],1)
 def test_separate_c_strings_are_not_joined_into_fake_paths(self):
  self.assertNotIn('Personal/local build path',audit.inspect_binary(pe()+b'/home/\x00fixture\x00/file','release.exe'))
 def test_debug_companion_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'app.pdb';path.write_bytes(b'symbols');self.assertFalse(audit.verify([path])['passed'])
 def test_upstream_exception_pins_exact_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'upstream.exe';data=pe(True);path.write_bytes(data)
   policy=[{'artifact_suffix':'upstream.exe','sha256':hashlib.sha256(data).hexdigest(),'problems':['PE debug directory']}]
   self.assertTrue(audit.verify([path],policy)['passed'])
   path.write_bytes(data+b'changed');self.assertFalse(audit.verify([path],policy)['passed'])

if __name__=='__main__':unittest.main()
