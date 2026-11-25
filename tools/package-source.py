"""Create a reproducible source-only ZIP with no runtime images or local reports."""
import argparse,hashlib,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES=('README.md','BUILDING.md','BASELINE.json','PROJECT.json','LICENSE','.gitignore','.gitattributes')
DIRECTORIES=('src','bootstrap','tools','tests','runtime')
SUFFIXES={'.cs','.py','.sh','.ps1','.ld','.c','.h','.in','.json','.md'}
PRIVATE_PATHS=(re.compile(r'(?i)\b[A-Z]:[\\/]'),re.compile(r'(?i)(?:/Users/[A-Za-z0-9_.-]+/|/home/(?!hybroad/)[A-Za-z0-9_.-]+/)'),re.compile(r'(?i)\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+'))
PRIVATE_MATERIAL=(re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),re.compile(r'ssh-ed25519 [A-Za-z0-9+/=]{40,}'),re.compile(r'(?i)\bbox[- ](?:one|1)\b'))

def entries(source):
 source=Path(source);files={}
 for name in ROOT_FILES:
  path=source/name
  if path.is_file():files[name]=path.read_bytes()
 for directory in DIRECTORIES:
  for path in sorted((source/directory).rglob('*')):
   relative=path.relative_to(source)
   if any(part in ('__pycache__','results','logs','validation','diagnostics') or part.startswith('result-') for part in relative.parts):continue
   if path.is_file() and not path.is_symlink() and (directory=='runtime' or path.suffix.lower() in SUFFIXES) and '__pycache__' not in relative.parts:files[relative.as_posix()]=path.read_bytes()
 for name in ('DESIGN.md',):
  design=source/'docs'/name
  if design.is_file():files['docs/'+name]=design.read_bytes()
 if (source/'runtime').exists():
  if 'runtime/SHA256SUMS' not in files:raise ValueError('Runtime source export is incomplete')
  for line in files['runtime/SHA256SUMS'].decode('ascii').splitlines():
   if 'runtime/'+line.split(None,1)[1].strip() not in files:raise ValueError('Runtime source export is incomplete')
  files['runtime/SHA256SUMS']=''.join(hashlib.sha256(data).hexdigest()+'  '+name.removeprefix('runtime/')+'\n' for name,data in sorted(files.items()) if name.startswith('runtime/') and name!='runtime/SHA256SUMS').encode('ascii')
 return files

def audit(files,forbidden=()):
 errors=[]
 for name,data in files.items():
  if data.startswith((b'MZ',b'\x7fELF',b'PK\x03\x04',b'\x1f\x8b',b'\x89PNG')) or name.lower().endswith(('.zip','.gz','.img','.iso','.exe','.so','.ko')):errors.append(name+': binary or image not permitted');continue
  try:text=data.decode('utf-8-sig')
  except UnicodeDecodeError:errors.append(name+': non-text file');continue
  if any(pattern.search(text) for pattern in PRIVATE_PATHS+PRIVATE_MATERIAL):errors.append(name+': absolute host path, private material or device-specific wording')
  if any(word.casefold() in text.casefold() for word in forbidden):errors.append(name+': forbidden identifying text')
 if errors:raise ValueError('Source privacy audit failed: '+'; '.join(errors))

def create(source,output,forbidden=()):
 files=entries(source);audit(files,forbidden)
 files['SHA256SUMS']=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(files.items())).encode('ascii')
 output=Path(output)
 if output.exists():raise ValueError('Source output already exists')
 output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
  for name,data in sorted(files.items()):
   item=zipfile.ZipInfo(name,(1980,1,1,0,0,0));item.create_system=3;item.external_attr=(0o100755 if name.endswith(('.py','.sh')) else 0o100644)<<16;item.compress_type=zipfile.ZIP_DEFLATED
   archive.writestr(item,data)
 with zipfile.ZipFile(output) as archive:
  if archive.testzip() is not None:raise ValueError('Source ZIP failed integrity check')
 Path(str(output)+'.sha256').write_bytes((hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n').encode('ascii'))
 return {'files':len(files),'bytes':output.stat().st_size,'runtime_images_included':False,'privacy_audit_passed':True}

if __name__=='__main__':
 import json
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--forbid',action='append',default=[])
 args=parser.parse_args();print(json.dumps(create(args.source,args.output,args.forbid),indent=2))
