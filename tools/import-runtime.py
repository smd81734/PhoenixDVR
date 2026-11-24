"""Import hash-pinned runtime assets from a separately supplied installer ZIP."""
import argparse,hashlib,json,shutil,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ASSETS=('base-rootfs.tar.gz','clean-boot-package.tar.gz','check-busybox','check-ld-musl-armhf.so.1')

def resolve(path):
 path=Path(path)
 return path if path.is_absolute() else ROOT/path

def import_assets(archive,output,receipt):
 output=Path(output);data={}
 with zipfile.ZipFile(archive) as source:
  for name in ASSETS:
   relative='usb-payload/phoenix/'+name
   matches=[item for item in source.infolist() if item.filename==relative or item.filename.endswith('/'+relative)]
   if len(matches)!=1:raise ValueError('Missing or ambiguous runtime asset: '+name)
   raw=source.read(matches[0])
   if hashlib.sha256(raw).hexdigest()!=receipt['baseline_files_sha256'][relative]:raise ValueError('Runtime asset hash mismatch: '+name)
   data[name]=raw
 if output.exists():raise ValueError('Input directory already exists; use a new output directory')
 output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='runtime-import-',dir=output.parent) as folder:
  stage=Path(folder)/'baseline';destination=stage/'usb-payload/phoenix';destination.mkdir(parents=True)
  for name,raw in data.items():(destination/name).write_bytes(raw)
  stage.rename(output)
 return {'assets_imported':len(data),'hashes_verified':True}

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',required=True,help='Installer ZIP, relative to the source archive root');parser.add_argument('--output',default='inputs/runtime')
 args=parser.parse_args();receipt=json.loads((ROOT/'BASELINE.json').read_text())
 print(json.dumps(import_assets(resolve(args.archive),resolve(args.output),receipt),indent=2))
