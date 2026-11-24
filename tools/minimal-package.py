"""Keep only the files consumed by initial installation; detach the shared base."""
import hashlib,io,json,tarfile,gzip
from pathlib import Path

REQUIRED={
 'prepare.py','activate.py','payload/apps.tar.gz','payload/channel-icons.tar.gz',
 'project/dependencies/catalog.json','project/dependencies/vendor-files.sha256','project/dependencies/browser-files.sha256',
 'project/tools/render-device-config.py','project/source/frontend/browser.py','project/source/provision/persistence.py',
 'project/source/tvheadend/setup.py',
 'project/source/boot/hdd/boot.txt','project/source/boot/hdd/selector.sh.in','project/source/boot/hdd/alpine-init',
}

def digest(data):return hashlib.sha256(data).hexdigest()

def strip_base(base):
 result=io.BytesIO();removed=[]
 with tarfile.open(fileobj=io.BytesIO(base),mode='r:gz') as source,gzip.GzipFile(fileobj=result,mode='wb',mtime=0,filename='',compresslevel=6) as compressed,tarfile.open(fileobj=compressed,mode='w',format=tarfile.GNU_FORMAT) as target:
  for member in source:
   if member.isfile() and member.name.lower().endswith(('.md','.markdown','.rst')):
    removed.append(member.name);continue
   target.addfile(member,source.extractfile(member) if member.isfile() else None)
 return result.getvalue(),removed

def create(package,output,base_output):
 with tarfile.open(package,'r:gz') as source:
  members={m.name:m for m in source};roots={Path(n).parts[0] for n in members};assert len(roots)==1
  root=next(iter(roots))
  def read(name):
   m=members[root+'/'+name]
   if not m.isfile():raise ValueError('Expected a regular package file: '+name)
   return source.extractfile(m).read()
  original=json.loads(read('MANIFEST.json'))
  kept={name:read(name) for name in REQUIRED}
  for name,m in members.items():
   relative=Path(name).relative_to(root).as_posix()
   if m.isfile() and relative.startswith('project/source/boot/rootfs/') and relative!='project/source/boot/rootfs/etc/hostname':kept[relative]=read(relative)
  if not any(n.startswith('project/source/boot/rootfs/') for n in kept):raise ValueError('Boot root overlay missing')
  base=read('payload/base-rootfs.tar.gz')
  if digest(base)!=original['rootfs_sha256']:raise ValueError('Base checksum mismatch')
  base,removed=strip_base(base)
  catalog=json.loads(kept['project/dependencies/catalog.json'])
  catalog={'downloads':{'fonts':[{'name':f['name'],'sha256':f['sha256']} for f in catalog['downloads']['fonts']]},'target_firmware':{'module_sha256':catalog['target_firmware']['module_sha256']}}
  kept['project/dependencies/catalog.json']=(json.dumps(catalog,separators=(',',':'))+'\n').encode()
  manifest={'format':1,'id':original['id'],'rootfs_sha256':digest(base)}
  kept['MANIFEST.json']=(json.dumps(manifest,separators=(',',':'))+'\n').encode()
  # The installer materializes this shared file before normal package verification.
  sums={name:digest(data) for name,data in kept.items()};sums['payload/base-rootfs.tar.gz']=digest(base)
  kept['SHA256SUMS']=''.join(sums[n]+'  '+n+'\n' for n in sorted(sums)).encode()
  with Path(output).open('wb') as file,gzip.GzipFile(fileobj=file,mode='wb',mtime=0,filename='',compresslevel=6) as compressed,tarfile.open(fileobj=compressed,mode='w',format=tarfile.GNU_FORMAT) as target:
   for name,data in sorted(kept.items()):
    m=tarfile.TarInfo(root+'/'+name);m.size=len(data);m.uid=m.gid=0;m.uname=m.gname='root';m.mode=0o755 if name.endswith(('.py','.sh')) else 0o644
    target.addfile(m,io.BytesIO(data))
  Path(base_output).write_bytes(base)
 return {'source_release':original['id'],'package_files':len(kept),'removed_rootfs_documentation':removed,'rootfs_sha256':digest(base)}
