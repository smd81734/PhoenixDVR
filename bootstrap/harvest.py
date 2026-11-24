#!/usr/bin/env python3
"""Find firmware dependencies and required target configuration without firmware writes."""
import hashlib,json,os,shutil,re,struct
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def manifest(path):
    rows=[]
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith('#'):continue
        digest,name,*_=line.split()
        if digest!='PRESENT' and (len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)):raise ValueError('Unsupported dependency manifest record')
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Unsafe dependency destination')
        rows.append((digest,name))
    return rows

def requirements(package):
    project=Path(package)/'project';catalog=json.loads((project/'dependencies/catalog.json').read_text())
    rows=[(h,'runtime/'+n) for h,n in manifest(project/'dependencies/vendor-files.sha256')]
    rows += [(h,'runtime/opt/chromium/'+n) for h,n in manifest(project/'dependencies/browser-files.sha256')]
    rows += [(v['sha256'],'fonts/'+v['name']) for v in catalog['downloads']['fonts']]
    rows += [(catalog['target_firmware']['module_sha256'],'modules/nexus.ko')]
    return rows

def remote_map(relative):
    return Path(relative).as_posix().removeprefix('runtime/opt/chromium/').startswith('bin/irkeymap/')

def map_files(browser,approved=None,required=False):
    folder=Path(browser).parent/'irkeymap'
    if not folder.is_dir():
        if required:raise ValueError('Selected Chrome bundle has no remote-map directory')
        return []
    files=[]
    for path in sorted(folder.rglob('*')):
        if not path.is_file():continue
        if approved is not None and path not in approved:raise ValueError('Remote-map file escapes the approved source trees')
        if not path.resolve().is_relative_to(folder.resolve()):raise ValueError('Remote-map link escapes its directory')
        files.append((path,'bin/irkeymap/'+path.relative_to(folder).as_posix()))
    if required and not files:raise ValueError('Selected Chrome remote-map directory is empty')
    return files

def index(roots,wanted,progress=lambda message:None):
    found={};seen=set();count=0
    for root in roots:
        root=Path(root)
        if not root.is_dir():continue
        for directory,dirs,files in os.walk(root,followlinks=False):
            for name in files:
                path=Path(directory)/name
                try:
                    resolved=path.resolve()
                    # Ignore links escaping these explicitly approved read-only source trees.
                    if not any(resolved.is_relative_to(Path(r).resolve()) for r in roots):continue
                    st=resolved.stat()
                    if not resolved.is_file() or (st.st_dev,st.st_ino) in seen:continue
                    seen.add((st.st_dev,st.st_ino));digest=sha(resolved);count+=1
                    if digest in wanted:found.setdefault(digest,resolved)
                    if count%100==0:progress('Checked %d firmware files; %d of %d required hashes found'%(count,len(found),len(wanted)))
                    if wanted<=found.keys():return found
                except (OSError,RuntimeError):continue
    return found

def inventory(roots):
    approved=[Path(p).resolve() for p in roots];files=[]
    for root in roots:
        if not Path(root).is_dir():continue
        for directory,dirs,names in os.walk(root,followlinks=False):
            for name in names:
                path=Path(directory)/name
                try:
                    if path.is_file() and any(path.resolve().is_relative_to(r) for r in approved):files.append(path)
                except OSError:continue
    return files

def browser_version(path):
    match=re.fullmatch(r'chrome\.(\d+(?:\.\d+)*)',Path(path).name)
    return tuple(int(v) for v in match.group(1).split('.')) if match else ()

def latest_browser(files):
    candidates=[]
    for path in files:
        if not browser_version(path):continue
        try:
            with path.open('rb') as f:header=f.read(20)
            if len(header)==20 and header[:6]==b'\x7fELF\x01\x01' and struct.unpack_from('<H',header,18)[0]==40:candidates.append(path)
        except OSError:continue
    if not candidates:raise ValueError('No 32-bit ARM Chrome executable was found on the target')
    return max(candidates,key=lambda p:(browser_version(p),p.as_posix()))

def matching_name(path,name):
    if path.name==name:return True
    if '.so' in name:
        stem=name.split('.so',1)[0]
        return path.name.startswith(name+'.') or bool(re.match(re.escape(stem)+r'(?:-|\.so)',path.name))
    return False

def suffix_score(path,destination):
    left=path.parts[::-1];right=Path(destination).parts[::-1];score=0
    for a,b in zip(left,right):
        if a!=b:break
        score+=1
    return score

def find_source(destination,files,browser):
    relative=destination.removeprefix('runtime/opt/chromium/')
    if destination.startswith('runtime/opt/chromium/bin/chrome.'):
        return browser,'runtime/opt/chromium/bin/'+browser.name
    if destination.startswith('runtime/opt/chromium/bin/'):
        candidate=browser.parent/relative.removeprefix('bin/')
        if candidate.is_file() and candidate in files:return candidate,destination
        raise ValueError('Selected Chrome bundle is missing '+relative)
    if destination.startswith('runtime/opt/chromium/share/'):
        for parent in list(browser.parents)[:4]:
            candidate=parent/relative
            if candidate.is_file() and candidate in files:return candidate,destination
        raise ValueError('Selected Chrome bundle is missing '+relative)
    if destination.startswith('runtime/opt/chromium/lib/'):
        for parent in list(browser.parents)[:4]:
            candidates=[p for p in files if p.parent==parent/'lib' and matching_name(p,Path(destination).name)]
            if Path(destination).name=='libstream.so':candidates += [p for p in files if p.parent==parent/'lib' and re.fullmatch(r'lib_stream_.*_browser\.so',p.name)]
            if candidates:return max(candidates,key=lambda p:(p.name==Path(destination).name,tuple(int(n) for n in re.findall(r'\d+',p.name)),p.as_posix())),destination
    name=Path(destination).name;candidates=[p for p in files if matching_name(p,name)]
    if not candidates:raise ValueError('Required target dependency is missing: '+destination)
    return max(candidates,key=lambda p:(p.name==name,suffix_score(p,destination),tuple(int(n) for n in re.findall(r'\d+',p.name)),-files.index(p))),destination

def validate_font(path):
    path=Path(path)
    with path.open('rb') as stream:
        header=stream.read(12)
        if len(header)!=12 or header[:4] not in (b'\x00\x01\x00\x00',b'OTTO',b'true'):
            raise ValueError('Invalid target font: '+path.name)
        count=struct.unpack_from('>H',header,4)[0]
        size=path.stat().st_size
        if not 1<=count<=256 or 12+16*count>size:raise ValueError('Invalid target font table directory: '+path.name)
        for _ in range(count):
            tag,checksum,offset,length=struct.unpack('>4sIII',stream.read(16))
            if offset>size or length>size-offset:raise ValueError('Invalid target font table bounds: '+path.name)


def font_source(destination,files):
    name=Path(destination).name
    choices=[name]+(['Texta-Heavy.ttf'] if name=='Texta-Bold.ttf' else [])
    for choice in choices:
        candidates=[p for p in files if p.name==choice]
        if candidates:
            source=max(candidates,key=lambda p:(suffix_score(p,destination),-files.index(p)))
            validate_font(source)
            return source
    raise ValueError('Required target font is missing: '+name+(' (Texta-Heavy.ttf is also accepted)' if name=='Texta-Bold.ttf' else ''))


def check(package,roots,progress=lambda message:None,ignore_mismatches=False,selection_report=None):
    rows=requirements(package);font_rows=[(h,n) for h,n in rows if n.startswith('fonts/')]
    config_rows=[(h,n) for h,n in rows if h=='PRESENT']
    requires_maps=any(remote_map(n) for h,n in rows)
    catalog=Path(package)/'project/dependencies/catalog.json'
    if catalog.is_file():requires_maps=requires_maps or json.loads(catalog.read_text()).get('target_firmware',{}).get('remote_maps_directory_required',False)
    rows=[(h,n) for h,n in rows if h!='PRESENT' and not n.startswith('fonts/') and not remote_map(n)]
    files=inventory(roots);browser=latest_browser(files)
    progress('Selected target browser '+browser.name)
    if not ignore_mismatches:
        found=index(roots,{h for h,_ in rows},progress)
        missing=[name for h,name in rows if h not in found]
        if missing:raise ValueError('Firmware does not match supported dependencies: '+', '.join(missing))
        expected=next(h for h,n in rows if n.startswith('runtime/opt/chromium/bin/chrome.'))
        if sha(browser)!=expected:raise ValueError('Newest target Chrome differs from the known SHA; use the explicit firmware mismatch option to proceed')
        selected=[(h,'runtime/opt/chromium/bin/'+browser.name if n.startswith('runtime/opt/chromium/bin/chrome.') else n) for h,n in rows]
    else:
        # Reference SHA override applies to native firmware; actual-copy hashes remain mandatory.
        selected=[];found={}
        for expected,destination in rows:
            source,destination=find_source(destination,files,browser);actual=sha(source)
            selected.append((actual,destination));found[actual]=source
            if selection_report is not None:selection_report.append({'destination':destination,'source':str(source),'expected_sha256':expected,'installed_sha256':actual,'known_sha_matches':actual==expected})
        progress('Firmware SHA mismatches accepted; '+str(len(selected))+' native firmware files will be copy-verified')
    # Target configuration is required, but its contents may vary between devices.
    for _,destination in config_rows:
        source,destination=find_source(destination,files,browser)
        relative=destination.removeprefix('runtime/')
        if suffix_score(source,relative)!=len(Path(relative).parts):raise ValueError('Required target configuration is missing: '+relative)
        actual=sha(source);selected.append((actual,destination));found[actual]=source
        if selection_report is not None:selection_report.append({'destination':destination,'source':str(source),'expected_sha256':None,'installed_sha256':actual,'known_sha_matches':None,'policy':'required target configuration; actual copy hash verified'})
    # Fonts are visual assets, not firmware ABI dependencies. Validate format and
    # record actual hashes for copy integrity without requiring the historical SHA.
    for expected,destination in font_rows:
        source=font_source(destination,files);actual=sha(source)
        selected.append((actual,destination));found[actual]=source
        progress('Selected font '+source.name+' for '+Path(destination).name+' (copy integrity enforced)')
        if selection_report is not None:selection_report.append({'destination':destination,'source':str(source),'expected_sha256':expected,'installed_sha256':actual,'known_sha_matches':actual==expected,'policy':'font variant accepted; format and copy verified'})
    for source,relative in map_files(browser,set(files),requires_maps):
        actual=sha(source);destination='runtime/opt/chromium/'+relative
        selected.append((actual,destination));found[actual]=source
        if selection_report is not None:selection_report.append({'destination':destination,'source':str(source),'expected_sha256':None,'installed_sha256':actual,'known_sha_matches':None,'policy':'whole remote-map directory; actual copy hash verified'})
    return selected,found


def preparation_dependencies(prepare,package,vendor,fonts,ko,ignore_mismatch=False):
    """R12 preparation adapter: retain native checks, allow validated font variants.

    The sealed package stays unchanged; only its dependencies callback is replaced.
    Its package verification, font copy checks and candidate checks remain intact.
    """
    package=Path(package);vendor=Path(vendor)
    catalog=json.loads((package/'project/dependencies/catalog.json').read_text())
    files=prepare.fingerprint_files(package/'project/dependencies/vendor-files.sha256',vendor,ignore_mismatch)
    browser_module=prepare.module('package_browser',package/'project/source/frontend/browser.py')
    executable=browser_module.newest(vendor.glob('opt/*/bin/chrome.*'))
    browser_files=[];requires_maps=False
    for expected,relative in manifest(package/'project/dependencies/browser-files.sha256'):
        if remote_map(relative):requires_maps=True;continue
        if relative.startswith('bin/chrome.'):relative='bin/'+executable.name
        source=executable.parent.parent/relative
        if not source.is_file():raise ValueError('Required browser dependency is missing: '+relative)
        if sha(source)!=expected and not ignore_mismatch:raise ValueError('Target browser dependency differs: '+relative)
        browser_files.append((source,relative))
    browser_files.extend(map_files(executable,required=requires_maps or catalog['target_firmware'].get('remote_maps_directory_required',False)))
    for font in catalog['downloads']['fonts']:validate_font(Path(fonts)/font['name'])
    if not Path(ko).is_file():raise ValueError('Target kernel module is missing')
    if prepare.sha(ko)!=catalog['target_firmware']['module_sha256'] and not ignore_mismatch:raise ValueError('Target kernel module differs')
    return files,browser_files,catalog


def copy(rows,found,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('Harvest destination already exists')
    for digest,name in rows:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(found[digest],target)
        if sha(target)!=digest:raise ValueError('Harvest copy verification failed: '+name)
    return destination
