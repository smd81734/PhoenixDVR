#!/usr/bin/env python3
"""Build a verified offline USB payload around a sealed clean-boot release."""
import argparse,hashlib,json,shutil,tarfile,io,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def installer_template():
 return 'boot flash0.kernel0 \'HYBROAD_7252 flash0.kernel0 root=0 rw runlevel=1 vmalloc=380m bmem=768m@1279m bmem=970m@2099m HYBROAD_SECURE_BOOT loglevel=4 rdinit=/bin/bash -- -c "shopt -s execfail; /bin/busybox sleep 8; /bin/busybox mount -t devtmpfs devtmpfs /dev; /bin/busybox mkdir -p /mnt/phoenix-usb; /bin/busybox mount -t vfat -o ro /dev/sdb1 /mnt/phoenix-usb || /bin/busybox mount -t vfat -o ro /dev/sda1 /mnt/phoenix-usb || exec /bin/bash -i; /bin/busybox cp /mnt/phoenix-usb/phoenix/stage1.sh /phoenix-stage1 && /bin/busybox cmp /mnt/phoenix-usb/phoenix/stage1.sh /phoenix-stage1 && exec /bin/bash /phoenix-stage1; exec /bin/bash -i"\'\n'

def build(package,output,minimal=True):
 package=Path(package);output=Path(output)
 expected=Path(str(package)+'.sha256').read_text().split()[0]
 if sha(package)!=expected:raise ValueError('Clean package archive checksum mismatch')
 if output.exists():raise ValueError('Output already exists')
 with tarfile.open(package,'r:gz') as source:
  members=list(source);roots={Path(m.name).parts[0] for m in members};assert len(roots)==1
  root=next(iter(roots));manifest=json.load(source.extractfile(root+'/MANIFEST.json'))
  for m in members:
   if m.name.startswith('/') or '..' in Path(m.name).parts:raise ValueError('Unsafe package')
  base=source.extractfile(root+'/payload/base-rootfs.tar.gz').read()
  if hashlib.sha256(base).hexdigest()!=manifest['rootfs_sha256']:raise ValueError('Base root checksum mismatch')
  boot=source.extractfile(root+'/project/source/boot/hdd/boot.txt').read()
 output.mkdir(parents=True);p=output/'phoenix';p.mkdir()
 if minimal:
  spec=importlib.util.spec_from_file_location('minimal_package',ROOT/'tools/minimal-package.py');mini=importlib.util.module_from_spec(spec);spec.loader.exec_module(mini)
  mini.create(package,p/'clean-boot-package.tar.gz',p/'base-rootfs.tar.gz')
  base=(p/'base-rootfs.tar.gz').read_bytes()
 # Supply the public Alpine checksum runner: the stock BusyBox has no sha256sum.
 with tarfile.open(fileobj=io.BytesIO(base),mode='r:gz') as archive:
  for source,destination in [('bin/busybox','check-busybox'),('lib/ld-musl-armhf.so.1','check-ld-musl-armhf.so.1')]:
   member=next(m for m in archive if m.name.removeprefix('./')==source)
   if not member.isfile():raise ValueError('Bootstrap verifier must be a regular ELF file')
   data=archive.extractfile(member).read()
   if data[:6]!=b'\x7fELF\x01\x01' or int.from_bytes(data[18:20],'little')!=40:raise ValueError('Bootstrap verifier must be ARM ELF32')
   (p/destination).write_bytes(data)
 if not minimal:
  (p/'base-rootfs.tar.gz').write_bytes(base);shutil.copyfile(package,p/'clean-boot-package.tar.gz')
 for name in ('stage1.sh','stage2.py','harvest.py','network-bridge.py','diagnostics-init.sh','recovery.py','slots.py'):(p/name).write_text((ROOT/'bootstrap'/name).read_text(encoding='utf-8-sig'),encoding='utf-8',newline='\n')
 (p/'usb-id.txt').write_text('PHOENIX_INSTALLER_USB_V2\n',newline='\n');(p/'boot.txt').write_bytes(boot)
 # A BOLT batch file, not a Windows command script. Stock signed kernel only.
 command=installer_template()
 # Keep real double quotes: Linux next_arg groups the Bash -c argument.
 # Backslash-escaped quotes survive as literal characters (the observed \"shopt failure).
 spec=importlib.util.spec_from_file_location('firmware_slots',ROOT/'bootstrap/slots.py');slots=importlib.util.module_from_spec(spec);spec.loader.exec_module(slots)
 slots.write_entries(p,command)
 files=sorted(x for x in output.rglob('*') if x.is_file())
 (p/'SHA256SUMS').write_bytes(''.join(sha(x)+'  '+x.relative_to(output).as_posix()+'\n' for x in files).encode('ascii'))
 files=sorted(x for x in output.rglob('*') if x.is_file())
 payload={'Format':1,'ProtocolVersion':5,'Files':[{'Path':x.relative_to(output).as_posix(),'Sha256':sha(x),'Bytes':x.stat().st_size} for x in files]}
 (output/'payload.json').write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8',newline='\n')
 return payload
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.package,a.output),indent=2))
