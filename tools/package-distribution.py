"""Publish a bare installer ZIP and keep development material in a separate ZIP."""
import argparse,hashlib,json,shutil,zipfile,subprocess,sys,importlib.util
from pathlib import Path

def sha(path):
 value=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1048576),b''):value.update(block)
 return value.hexdigest()

def zip_files(path,entries):
 with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
  for source,name in sorted(entries,key=lambda e:e[1]):
   item=zipfile.ZipInfo(name,(1980,1,1,0,0,0));item.create_system=3;item.external_attr=0o100644<<16;item.compress_type=zipfile.ZIP_DEFLATED
   archive.writestr(item,Path(source).read_bytes())
 with zipfile.ZipFile(path) as archive:
  if archive.testzip() is not None:raise ValueError('ZIP validation failed')
 Path(str(path)+'.sha256').write_bytes((sha(path)+'  '+path.name+'\n').encode('ascii'))

def validate_payload(payload):
 payload=Path(payload)
 for path in payload.rglob('SHA256SUMS'):
  data=path.read_bytes()
  if not data or b'\r' in data or not data.endswith(b'\n') or data.startswith(b'\xef\xbb\xbf'):raise ValueError('SHA256SUMS must use LF endings without BOM: '+str(path))
 manifest=json.loads((payload/'payload.json').read_text())
 for entry in manifest['Files']:
  path=payload/entry['Path']
  if path.stat().st_size!=entry['Bytes'] or sha(path)!=entry['Sha256']:raise ValueError('Payload manifest mismatch: '+entry['Path'])

def publish(exe,payload,source,upstream,destination,name,update=None):
 exe,payload,source,upstream,destination=map(Path,(exe,payload,source,upstream,destination))
 validate_payload(payload)
 if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):raise ValueError('Invalid release name')
 destination.mkdir(parents=True,exist_ok=True)
 folder=destination/name;installer=destination/(name+'.zip');development=destination/(name+'-development.zip')
 if any(p.exists() for p in (folder,installer,development)):raise ValueError('Release output already exists')
 scripts=exe.parent/'scripts'
 for script in ('format-usb.ps1','update.py'):
  if not (scripts/script).is_file():raise ValueError('Missing helper script beside the EXE: scripts/'+script)
 if update:
  update=Path(update)
  if not update.name.startswith('phoenix-update-') or not update.name.endswith('.tar.gz'):raise ValueError('Invalid bundled update filename')
  spec=importlib.util.spec_from_file_location('release_updater',source/'src/update.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
  worker.package(update,key=source/'src/update-public-key.json')
 folder.mkdir();shutil.copy2(exe,folder/'PhoenixSetup.exe');shutil.copytree(payload,folder/'usb-payload')
 (folder/'scripts').mkdir()
 for script in ('format-usb.ps1','update.py'):shutil.copy2(scripts/script,folder/'scripts'/script)
 if update:shutil.copy2(update,folder/update.name)
 entries=[(p,p.relative_to(folder).as_posix()) for p in folder.rglob('*') if p.is_file()]
 zip_files(installer,entries)
 subprocess.run([sys.executable,str(Path(__file__).with_name('verify-release.py')),str(installer),'--baseline-exceptions',str(Path(__file__).with_name('baseline-upstream-exceptions.json')),'--report',str(destination/(name+'-release-hygiene.json'))],check=True)
 # Source is published separately without bundled images, provenance or local
 # validation logs. Runtime assets are imported from a separately supplied ZIP.
 spec=importlib.util.spec_from_file_location('source_export',Path(__file__).with_name('package-source.py'));export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)
 export.create(source,development)
 return {'installer_zip':str(installer),'installer_bytes':installer.stat().st_size,'installer_sha256':sha(installer),'installer_files':len(entries),'development_zip':str(development),'development_bytes':development.stat().st_size,'development_sha256':sha(development)}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('exe','payload','source','upstream','destination','name'):p.add_argument('--'+name,required=True)
 p.add_argument('--update',help='Optional signed DVR update bundled beside the EXE')
 a=p.parse_args();print(json.dumps(publish(a.exe,a.payload,a.source,a.upstream,a.destination,a.name,a.update),indent=2))
