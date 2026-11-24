import binascii,io,json,os,shlex,struct,subprocess,tempfile,types,unittest
from pathlib import Path
import slots,test_payload,test_diagnostics

NORMAL="boot flash0.kernel0 'HYBROAD_7252 flash0.kernel0 root=0 rw runlevel=1 vmalloc=380m bmem=768m@1279m bmem=970m@2099m'\n"

def gpt():
 table=bytearray(128*128)
 for index,label,start in ((7,'root0',100),(12,'root1',200)):
  offset=(index-1)*128;table[offset:offset+16]=bytes([1])*16
  struct.pack_into('<QQ',table,offset+32,start,start+99)
  encoded=label.encode('utf-16-le');table[offset+56:offset+56+len(encoded)]=encoded
 header=bytearray(512);header[:8]=b'EFI PART';struct.pack_into('<II',header,8,0x10000,92)
 struct.pack_into('<QQQQ',header,24,1,8191,34,8158)
 struct.pack_into('<QIII',header,72,2,128,128,binascii.crc32(table)&0xffffffff)
 struct.pack_into('<I',header,16,binascii.crc32(header[:92])&0xffffffff)
 return bytearray(512)+header+table

class SlotTests(unittest.TestCase):
 def test_all_boot_entries_preserve_selected_pair_and_single_shell_argument(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);package,_=test_payload.PayloadTests().fixture(root);out=root/'out'
   manifest=test_payload.builder.build(package,out,minimal=False);self.assertEqual(manifest['ProtocolVersion'],5)
   for k in (0,1):
    for r in (0,1):
     for prefix in ('i','b','d','u'):
      command=(out/'phoenix'/slots.name(prefix,k,r)).read_text();argv=shlex.split(command)
      self.assertEqual(argv[:2],['boot',f'flash0.kernel{k}']);self.assertEqual(len(argv),3)
      self.assertEqual(slots.running(argv[2]),(k,r))
      kernel=shlex.split(argv[2]);self.assertEqual(len(kernel[kernel.index('--')+2:]),1)
      self.assertIn('/dev/sd[a-z]1',kernel[-1]);self.assertIn('PHOENIX_INSTALLER_USB_V2',kernel[-1])
      self.assertEqual('phoenix.diagnostics=1' in kernel,prefix=='d')
      self.assertEqual('phoenix.transport=uart' in kernel,prefix=='u')
     self.assertEqual((out/'phoenix'/slots.name('i',k,r)).read_bytes(),(out/'phoenix'/slots.name('b',k,r)).read_bytes())
 def test_root_mapping_uses_gpt_label_not_fixed_partition_numbers(self):
  self.assertEqual(slots.gpt_root(io.BytesIO(gpt()),512,8192*512,0),(7,100,100))
  self.assertEqual(slots.gpt_root(io.BytesIO(gpt()),512,8192*512,1),(12,200,100))
 def test_corrupt_partition_data_is_rejected(self):
  for offset in (512+24,1024+32):
   data=gpt();data[offset]^=1
   with self.assertRaisesRegex(ValueError,'CRC'):slots.gpt_root(io.BytesIO(data),512,8192*512,1)
 def test_sysfs_mapping_must_match_gpt(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);block=root/'sys';(block/'mmcblk0/queue').mkdir(parents=True);(block/'mmcblk0p12').mkdir()
   (block/'mmcblk0/queue/logical_block_size').write_text('512');(block/'mmcblk0/size').write_text('8192')
   (block/'mmcblk0p12/start').write_text('200');(block/'mmcblk0p12/size').write_text('100');image=root/'emmc';image.write_bytes(gpt())
   self.assertEqual(slots.root_device(1,block,image),Path('/dev/mmcblk0p12'))
   (block/'mmcblk0p12/start').write_text('201')
   with self.assertRaisesRegex(ValueError,'kernel partition mapping'):slots.root_device(1,block,image)
 def test_boot_pair_and_memory_validation(self):
  for text in (NORMAL.replace('kernel0','kernel2'),NORMAL.replace('root=0','root=2'),NORMAL.replace('vmalloc=380m','vmalloc=100m'),NORMAL.replace('root=0','root=0 root=1')):
   with self.assertRaises(ValueError):slots.running(shlex.split(text)[2])
 def test_renderer_specializes_boot_before_candidate_fingerprinting(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   def render(output):
    output=Path(output);boot=output/'hdd/alpine-system/boot/boot.txt';boot.parent.mkdir(parents=True);boot.write_text(NORMAL)
    (output/'rootfs/etc/phoenix').mkdir(parents=True);return 1
   prepare=types.SimpleNamespace(module=lambda label,path:types.SimpleNamespace(render=render))
   slots.specialize_renderer(prepare,1,1);renderer=prepare.module('phoenix_device_layout','unused');self.assertEqual(renderer.render(root),1)
   self.assertEqual(slots.running(shlex.split((root/'hdd/alpine-system/boot/boot.txt').read_text())[2]),(1,1))
   self.assertEqual(json.loads((root/'rootfs/etc/phoenix/firmware-slots.json').read_text()),{'kernel':1,'root':1})
 def test_module_version_is_checked_even_when_hash_override_is_used(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'nexus.ko';path.write_bytes(b'ELF\x00vermagic=4.1.20-1.9 SMP\x00');slots.validate_module(path,'4.1.20-1.9')
   with self.assertRaisesRegex(ValueError,'running kernel'):slots.validate_module(path,'4.1.20-other')
 @unittest.skipUnless(test_diagnostics.BASH,'Bash required')
 def test_usb_scan_skips_unrelated_stick_and_finds_sdc(self):
  with tempfile.TemporaryDirectory() as d:
   script='''mkdir -p usb/phoenix
testbb() {
 case "$1" in
 mount) echo "$6 $4 $5" >> mounts; case "$6" in /dev/sda1) return 1;; /dev/sdb1) echo OTHER > usb/phoenix/usb-id.txt;; /dev/sdc1) echo PHOENIX_INSTALLER_USB_V2 > usb/phoenix/usb-id.txt;; esac;;
 cat) cat "$2";;
 umount) rm -f usb/phoenix/usb-id.txt;;
 esac
}
'''+slots.USB_SCAN.replace('/bin/busybox','testbb').replace('/dev/sd[a-z]1','/dev/sda1 /dev/sdb1 /dev/sdc1').replace('/mnt/phoenix-usb','usb').replace('exec /bin/bash -i','exit 91')
   subprocess.run([test_diagnostics.BASH,'-c',script],cwd=d,check=True,capture_output=True,timeout=10)
   self.assertEqual((Path(d)/'usb/phoenix/usb-id.txt').read_text().strip(),'PHOENIX_INSTALLER_USB_V2')
   self.assertEqual((Path(d)/'mounts').read_text().splitlines(),['/dev/sda1 -o ro','/dev/sdb1 -o ro','/dev/sdc1 -o ro'])
