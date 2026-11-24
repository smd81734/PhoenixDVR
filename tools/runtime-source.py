"""Export and assemble runtime source without carrying images in source bundles."""
import argparse,gzip,hashlib,io,json,re,tarfile
from pathlib import Path

def text_data(data):
 try:text=data.decode('utf-8-sig')
 except UnicodeDecodeError:return None
 return None if '\x00' in text else text

def neutral(text):
 text=re.sub(r'(?i)\ba clean box[- ]one replacement\b','a clean replacement for the DVR',text)
 text=re.sub(r'(?i)\ba prepared box[- ]one replacement\b','a prepared replacement for the DVR',text)
 return re.sub(r'(?i)\bbox[- ]one\b','the DVR',text).replace('\r\n','\n').replace('\r','\n')

def app_members(data):
 if not data.startswith(b'\x1f\x8b'):return {},{}
 files={};modes={}
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
  for item in archive:
   name=Path(item.name)
   if name.is_absolute() or '..' in name.parts:raise ValueError('Unsafe application archive path')
   if item.issym() or item.islnk():raise ValueError('Unexpected application archive link')
   if item.isdir():modes[name.as_posix().rstrip('/')+'/']=item.mode
   if item.isfile():files[name.as_posix()]=archive.extractfile(item).read();modes[name.as_posix()]=item.mode
 return files,modes

def pack_apps(data,overrides=None):
 files,modes=app_members(data)
 if not files:return data
 original=dict(files)
 for name,raw in files.items():
  text=text_data(raw)
  if text is not None:files[name]=neutral(text).encode('utf-8')
 for name,raw in (overrides or {}).items():
  if text_data(raw) is None or (name in original and text_data(original[name]) is None):raise ValueError('Application source must not replace compiled binaries or images')
  files[name]=raw
 for name,raw in original.items():
  if text_data(raw) is None and files[name]!=raw:raise ValueError('Compiled application member changed')
 output=io.BytesIO()
 with gzip.GzipFile(fileobj=output,mode='wb',filename='',mtime=0,compresslevel=6) as zipped,tarfile.open(fileobj=zipped,mode='w',format=tarfile.GNU_FORMAT) as archive:
  for name,mode in sorted(modes.items()):
   if name.endswith('/'):
    item=tarfile.TarInfo(name);item.type=tarfile.DIRTYPE;item.mode=mode;item.uid=item.gid=0;item.uname=item.gname='root';item.mtime=0;archive.addfile(item)
  for name,raw in sorted(files.items()):
   item=tarfile.TarInfo(name);item.size=len(raw);item.uid=item.gid=0;item.uname=item.gname='root';item.mtime=0;item.mode=modes.get(name,0o644);archive.addfile(item,io.BytesIO(raw))
 return output.getvalue()

def read_package(archive):
 with tarfile.open(archive,'r:gz') as source:
  members=list(source);roots={Path(m.name).parts[0] for m in members}
  if len(roots)!=1:raise ValueError('Expected one runtime package root')
  root=next(iter(roots));files={};modes={}
  for item in members:
   relative=Path(item.name).relative_to(root)
   if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe runtime member')
   if item.isfile():files[relative.as_posix()]=source.extractfile(item).read();modes[relative.as_posix()]=item.mode
 return root,files,modes

def clean(files):
 result=dict(files)
 for name,data in files.items():
  if name.startswith('payload/') or name=='SHA256SUMS':continue
  text=data.decode('utf-8-sig')
  text=neutral(text)
  if name.endswith('browser-files.sha256'):
   text=''.join(line+'\n' for line in text.splitlines() if line.strip() and not line.split()[-1].startswith('bin/irkeymap/'))
  if name.endswith('dependencies/catalog.json'):
   value=json.loads(text);value['target_firmware']['remote_maps_directory_required']=True;text=json.dumps(value,indent=2)+'\n'
  if name=='prepare.py' and '# Copy the complete remote-map directory.' not in text:
   start=text.index('def dependencies(');end=text.index('\ndef ',start+1);part=text[start:end]
   marker="    for font in catalog['downloads']['fonts']:"
   if part.count(marker)!=1:raise ValueError('Unrecognized preparation dependency function')
   extra="""    # Copy the complete remote-map directory. File names are vendor-owned.
    maps = browser/'bin/irkeymap'
    if not maps.is_dir(): raise ValueError('Missing remote-map directory')
    map_files = sorted(p for p in maps.rglob('*') if p.is_file())
    if not map_files: raise ValueError('Empty remote-map directory')
    for source in map_files:
        if not source.resolve().is_relative_to(maps.resolve()): raise ValueError('Remote-map link escapes its directory')
        browser_files.append((source, source.relative_to(browser).as_posix()))
"""
   text=text[:start]+part.replace(marker,extra+marker,1)+text[end:]
  result[name]=text.replace('\r\n','\n').replace('\r','\n').encode('utf-8')
 if 'payload/apps.tar.gz' in result:result['payload/apps.tar.gz']=pack_apps(result['payload/apps.tar.gz'])
 return result

def sums(files,include_base):
 entries={name:hashlib.sha256(data).hexdigest() for name,data in files.items() if name!='SHA256SUMS'}
 if include_base:entries['payload/base-rootfs.tar.gz']=json.loads(files['MANIFEST.json'])['rootfs_sha256']
 return ''.join(entries[name]+'  '+name+'\n' for name in sorted(entries)).encode('ascii')

def export(archive,output):
 _,files,_=read_package(archive);files=clean(files);output=Path(output)
 files={name:data for name,data in files.items() if not name.startswith('payload/') and name!='SHA256SUMS'}
 _,original,_=read_package(archive)
 for name,data in app_members(original.get('payload/apps.tar.gz',b''))[0].items():
  text=text_data(data)
  if text is not None:files['apps/'+name]=neutral(text).encode('utf-8')
 files['SHA256SUMS']=sums(files,False)
 if output.exists():
  for path in output.rglob('*'):
   if path.relative_to(output).as_posix()=='SHA256SUMS':continue
   if path.is_file() and (path.relative_to(output).as_posix() not in files or path.read_bytes()!=files[path.relative_to(output).as_posix()]):raise ValueError('Existing runtime source was edited; refusing to replace it')
 for name,data in files.items():
  target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 return {'source_files':len(files),'images_included':False}

def repack(archive,output,source=None):
 if Path(archive).resolve()==Path(output).resolve():raise ValueError('Do not overwrite the original runtime input')
 root,original,modes=read_package(archive);files=clean(original);app_overrides={}
 if source and Path(source).is_dir():
  for path in Path(source).rglob('*'):
   if not path.is_file() or '__pycache__' in path.parts:continue
   name=path.relative_to(source).as_posix()
   if name=='SHA256SUMS':continue
   if name.startswith('payload/') or path.is_symlink():raise ValueError('Runtime source must not contain payload assets or links')
   if name.startswith('apps/'):app_overrides[name.removeprefix('apps/')]=path.read_bytes()
   else:files[name]=path.read_bytes()
 if 'payload/apps.tar.gz' in files:files['payload/apps.tar.gz']=pack_apps(original['payload/apps.tar.gz'],app_overrides)
 for name,data in original.items():
  if name.startswith('payload/') and name!='payload/apps.tar.gz' and files[name]!=data:raise ValueError('Binary runtime asset changed')
 files['SHA256SUMS']=sums(files,True)
 with Path(output).open('wb') as stream,gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0,compresslevel=6) as zipped,tarfile.open(fileobj=zipped,mode='w',format=tarfile.GNU_FORMAT) as target:
  for name,data in sorted(files.items()):
   item=tarfile.TarInfo(root+'/'+name);item.size=len(data);item.uid=item.gid=0;item.uname=item.gname='root';item.mtime=0;item.mode=modes.get(name,0o644)
   target.addfile(item,io.BytesIO(data))
 return {'files':len(files),'compiled_binaries_unchanged':True,'application_sources_prepared':True,'archive_metadata_normalized':True}

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--repack',action='store_true');parser.add_argument('--source',type=Path)
 args=parser.parse_args();print(json.dumps(repack(args.archive,args.output,args.source) if args.repack else export(args.archive,args.output),indent=2))
