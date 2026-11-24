"""Fail release packaging on binary debug data or personal build paths.

Examines PE/ELF binaries recursively inside ZIP and tar archives without extraction.
Runtime configuration paths such as /root and /home/hybroad are legitimate.
"""
import argparse,io,json,re,struct,tarfile,zipfile,hashlib
from pathlib import Path

PERSONAL=(re.compile(rb'[A-Za-z]:[\\/]Users[\\/][^\x00\r\n]{1,160}',re.I),
          re.compile(rb'(?:/home/(?!hybroad/)[^/\x00]+/|/Users/[^/\x00]+/)',re.I))
DEBUG_SUFFIXES=('.pdb','.mdb','.debug','.dwo','.dwp')

def inspect_binary(data,name):
 problems=[]
 if data.startswith(b'MZ'):
  if len(data)<64:raise ValueError('Truncated PE: '+name)
  pe=struct.unpack_from('<I',data,60)[0]
  if data[pe:pe+4]!=b'PE\0\0':raise ValueError('Invalid PE: '+name)
  opt=pe+24;magic=struct.unpack_from('<H',data,opt)[0]
  start=opt+(112 if magic==0x20b else 96 if magic==0x10b else 0)
  if start==opt:raise ValueError('Unknown PE optional header: '+name)
  count=struct.unpack_from('<I',data,start-4)[0]
  if count>6 and any(struct.unpack_from('<II',data,start+6*8)):problems.append('PE debug directory')
  if b'RSDS' in data or b'NB10' in data:problems.append('CodeView/PDB marker')
 elif data.startswith(b'\x7fELF'):
  bits=data[4];endian='<' if data[5]==1 else '>' if data[5]==2 else None
  if endian is None or bits not in (1,2):raise ValueError('Unsupported ELF: '+name)
  if bits==1:
   at=struct.unpack_from(endian+'I',data,32)[0];size,count,names=struct.unpack_from(endian+'HHH',data,46);fmt=endian+'10I'
  else:
   at=struct.unpack_from(endian+'Q',data,40)[0];size,count,names=struct.unpack_from(endian+'HHH',data,58);fmt=endian+'II4QII2Q'
  if count:
   if not size or names>=count or at+size*count>len(data):raise ValueError('Invalid ELF section table: '+name)
   rows=[struct.unpack_from(fmt,data,at+i*size) for i in range(count)]
   table=rows[names];strings=data[table[4]:table[4]+table[5]]
   for row in rows:
    section=strings[row[0]:].split(b'\0',1)[0]
    if section.startswith((b'.debug',b'.zdebug',b'.stab')) or section==b'.gdb_index':problems.append('ELF section '+section.decode('ascii',errors='replace'))
 else:return []
 # Decode wide strings instead of deleting all NULs: deletion can concatenate
 # unrelated C strings into a path that never existed in the binary.
 variants=(data, data.decode('utf-16-le',errors='ignore').encode('utf-8'),data[1:].decode('utf-16-le',errors='ignore').encode('utf-8'))
 for pattern in PERSONAL:
  if any(pattern.search(value) for value in variants):problems.append('Personal/local build path');break
 return sorted(set(problems))

def verify(paths,baseline_exceptions=()):
 errors=[];accepted=[];stats={'files':0,'binaries':0,'archives':0};budget=0
 def visit(data,name,depth=0):
  nonlocal budget
  stats['files']+=1;budget+=len(data)
  if depth>8 or stats['files']>50000 or budget>2*1024**3:raise ValueError('Release inspection limit exceeded')
  if name.lower().endswith(DEBUG_SUFFIXES) or '.dsym/' in name.lower():errors.append({'file':name,'problem':'Debug companion file'})
  if data.startswith((b'MZ',b'\x7fELF')):
   stats['binaries']+=1
   reasons=inspect_binary(data,name)
   allowed=next((e for e in baseline_exceptions if name.endswith(e['artifact_suffix']) and hashlib.sha256(data).hexdigest()==e['sha256']),None)
   for reason in reasons:
    entry={'file':name,'problem':reason}
    if allowed and reason in allowed['problems']:accepted.append(entry)
    else:errors.append(entry)
  elif data.startswith(b'PK\x03\x04'):
   stats['archives']+=1
   with zipfile.ZipFile(io.BytesIO(data)) as archive:
    for member in archive.infolist():
     if not member.is_dir():visit(archive.read(member),name+'!/'+member.filename,depth+1)
  elif name.lower().endswith(('.tar.gz','.tgz','.tar')):
   stats['archives']+=1
   with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
    for member in archive:
     if member.isfile():visit(archive.extractfile(member).read(),name+'!/'+member.name,depth+1)
 for path in map(Path,paths):
  if path.is_dir():
   for file in sorted(path.rglob('*')):
    if file.is_file():visit(file.read_bytes(),str(file))
  else:visit(path.read_bytes(),str(path))
 return {'passed':not errors,'stats':stats,'errors':errors,'unchanged_upstream_baseline_findings':accepted}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('paths',nargs='+');p.add_argument('--report',type=Path);p.add_argument('--baseline-exceptions',type=Path);a=p.parse_args()
 exceptions=json.loads(a.baseline_exceptions.read_text())['artifacts'] if a.baseline_exceptions else []
 result=verify(a.paths,exceptions)
 if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
