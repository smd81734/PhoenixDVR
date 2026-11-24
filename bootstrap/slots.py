"""Validated firmware slot selection; never writes bootloader or eMMC data."""
import binascii,json,re,struct
from pathlib import Path

MEMORY=('vmalloc=380m','bmem=768m@1279m','bmem=970m@2099m')
USB_MOUNT='/bin/busybox mount -t vfat -o ro /dev/sdb1 /mnt/phoenix-usb || /bin/busybox mount -t vfat -o ro /dev/sda1 /mnt/phoenix-usb || exec /bin/bash -i'
USB_SCAN='for dev in /dev/sd[a-z]1; do /bin/busybox mount -t vfat -o ro $dev /mnt/phoenix-usb || continue; if [ x$(/bin/busybox cat /mnt/phoenix-usb/phoenix/usb-id.txt 2>/dev/null) = xPHOENIX_INSTALLER_USB_V2 ]; then break; fi; /bin/busybox umount /mnt/phoenix-usb; done; [ -f /mnt/phoenix-usb/phoenix/usb-id.txt ] || exec /bin/bash -i'

def slot(value):
 if str(value) not in ('0','1'):raise ValueError('Only firmware slots 0 and 1 are supported')
 return int(value)

def name(prefix,kernel,root):
 if prefix not in ('i','b','d','u'):raise ValueError('Invalid boot entry type')
 return f'{prefix}{slot(kernel)}{slot(root)}.txt'

def render(template,kernel,root,installer=False,diagnostics=False):
 kernel,root=slot(kernel),slot(root)
 if template.count('flash0.kernel0')!=2 or template.count('root=0')!=1:raise ValueError('Unexpected signed-kernel boot template')
 text=template.replace('flash0.kernel0',f'flash0.kernel{kernel}').replace('root=0',f'root={root}')
 if installer:
  if text.count(USB_MOUNT)!=1:raise ValueError('Unexpected USB mount template')
  text=text.replace(USB_MOUNT,USB_SCAN)
 if diagnostics:text=text.replace(' rw runlevel=1 ', ' phoenix.diagnostics=1 rw runlevel=1 ')
 return text

def write_entries(folder,template):
 folder=Path(folder)
 for k in (0,1):
  for r in (0,1):
   text=render(template,k,r,installer=True)
   for prefix in ('i','b'):(folder/name(prefix,k,r)).write_bytes(text.encode('ascii'))
   (folder/name('u',k,r)).write_bytes(text.replace(' rw runlevel=1 ',' phoenix.transport=uart rw runlevel=1 ').encode('ascii'))
   (folder/name('d',k,r)).write_bytes(render(template,k,r,installer=True,diagnostics=True).encode('ascii'))
 for alias,prefix in (('install.txt','u'),('boot.txt','b'),('diagnostics.txt','d')):(folder/alias).write_bytes((folder/name(prefix,0,0)).read_bytes())

def running(text):
 prefix=text.split('rdinit=',1)[0]
 kernels=re.findall(r'(?:^|\s)flash0\.kernel(\S+)',prefix)
 roots=re.findall(r'(?:^|\s)root=(\S+)',prefix)
 if len(kernels)!=1 or len(roots)!=1 or not set(MEMORY)<=set(prefix.split()):raise ValueError('Unsupported memory layout or ambiguous firmware slots')
 return slot(kernels[0]),slot(roots[0])

def gpt_root(stream,logical,disk_bytes,root):
 root=slot(root)
 if logical not in (512,4096) or disk_bytes%logical:raise ValueError('Unsupported eMMC sector geometry')
 sectors=disk_bytes//logical;stream.seek(logical);header=bytearray(stream.read(logical))
 if len(header)!=logical or header[:8]!=b'EFI PART':raise ValueError('Missing primary GPT')
 size,crc=struct.unpack_from('<II',header,12)
 if not 92<=size<=logical:raise ValueError('Invalid GPT header size')
 struct.pack_into('<I',header,16,0)
 if binascii.crc32(header[:size])&0xffffffff!=crc:raise ValueError('GPT header CRC mismatch')
 current,backup,first,last=struct.unpack_from('<QQQQ',header,24)
 table_lba,count,entry_size,table_crc=struct.unpack_from('<QIII',header,72)
 length=count*entry_size
 if current!=1 or not 1<backup<sectors or not 1<first<=last<sectors or not 1<table_lba<first or not 0<count<=4096 or not 128<=entry_size<=4096 or entry_size%8 or length>1048576 or table_lba*logical+length>first*logical:raise ValueError('Invalid GPT bounds')
 stream.seek(table_lba*logical);table=stream.read(length)
 if len(table)!=length or binascii.crc32(table)&0xffffffff!=table_crc:raise ValueError('GPT entry CRC mismatch')
 matches=[]
 for index in range(count):
  entry=table[index*entry_size:(index+1)*entry_size]
  if entry[:16]==bytes(16):continue
  label=entry[56:128].decode('utf-16-le').rstrip('\x00')
  if label==f'root{root}':
   start,end=struct.unpack_from('<QQ',entry,32)
   if not first<=start<=end<=last:raise ValueError('Root partition exceeds GPT bounds')
   matches.append((index+1,start*logical//512,(end-start+1)*logical//512))
 if len(matches)!=1:raise ValueError('Missing or duplicate selected GPT root label')
 return matches[0]

def root_device(root,block=Path('/sys/class/block'),device=Path('/dev/mmcblk0')):
 logical=int((block/'mmcblk0/queue/logical_block_size').read_text());disk_bytes=int((block/'mmcblk0/size').read_text())*512
 with Path(device).open('rb') as stream:index,start,size=gpt_root(stream,logical,disk_bytes,root)
 node=f'mmcblk0p{index}'
 if int((block/node/'start').read_text())!=start or int((block/node/'size').read_text())!=size:raise ValueError('GPT root differs from kernel partition mapping')
 return Path('/dev')/node

def validate_module(path,release):
 data=Path(path).read_bytes();versions=re.findall(b'vermagic=([^\x00]+)',data)
 if len(versions)!=1 or versions[0].split()[0].decode('ascii')!=release:raise ValueError('Nexus module does not match the running kernel version')

def specialize_renderer(prepare,kernel,root):
 original_module=prepare.module
 def module(label,path):
  loaded=original_module(label,path)
  if label=='phoenix_device_layout':
   original_render=loaded.render
   def render_layout(output,*args,**kwargs):
    result=original_render(output,*args,**kwargs);output=Path(output)
    boot=output/'hdd/alpine-system/boot/boot.txt';boot.write_bytes(render(boot.read_text(),kernel,root).encode('ascii'))
    data=(json.dumps({'kernel':slot(kernel),'root':slot(root)},sort_keys=True)+'\n').encode('ascii')
    (boot.parent/'firmware-slots.json').write_bytes(data)
    (output/'rootfs/etc/phoenix/firmware-slots.json').write_bytes(data)
    return result
   loaded.render=render_layout
  return loaded
 prepare.module=module
