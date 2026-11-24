"""Integration checks against the actual sealed preparation module; no target writes."""
import importlib.util,json,sys,tempfile,shutil,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'bootstrap'))
import harvest

def load(path):
 spec=importlib.util.spec_from_file_location('sealed_prepare',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def main(package):
 package=Path(package);prepare=load(package/'prepare.py');prepare.verify_package(package)
 passed=[]
 for case in ('font variants accepted','native mismatch refused','browser mismatch refused','module mismatch refused','invalid font refused','explicit native override accepted'):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'package';vendor=root/'vendor';fonts=root/'fonts';dep=p/'project/dependencies';dep.mkdir(parents=True);fonts.mkdir()
   browser=p/'project/source/frontend/browser.py';browser.parent.mkdir(parents=True);shutil.copy2(package/'project/source/frontend/browser.py',browser)
   for name in ('lib/libnative.so','opt/chromium/bin/chrome.202504071556','opt/chromium/bin/resources.pak'):
    f=vendor/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(name.encode())
   ko=root/'nexus.ko';ko.write_bytes(b'module')
   for name in ('Texta-Bold.ttf','Texta-Light.ttf','Texta-Medium.ttf'):
    (fonts/name).write_bytes(struct.pack('>4sHHHH',b'\x00\x01\x00\x00',1,16,0,0)+struct.pack('>4sIII',b'head',0,28,4)+b'font')
   (dep/'vendor-files.sha256').write_text(prepare.sha(vendor/'lib/libnative.so')+'  lib/libnative.so\n')
   (dep/'browser-files.sha256').write_text(''.join(prepare.sha(vendor/'opt/chromium'/n)+'  '+n+'\n' for n in ('bin/chrome.202504071556','bin/resources.pak')))
   (dep/'catalog.json').write_text(json.dumps({'downloads':{'fonts':[{'name':n.name,'sha256':'0'*64} for n in sorted(fonts.iterdir())]},'target_firmware':{'module_sha256':prepare.sha(ko)}}))
   if case=='native mismatch refused' or case=='explicit native override accepted':(vendor/'lib/libnative.so').write_bytes(b'changed')
   if case=='browser mismatch refused':(vendor/'opt/chromium/bin/resources.pak').write_bytes(b'changed')
   if case=='module mismatch refused':ko.write_bytes(b'changed')
   if case=='invalid font refused':(fonts/'Texta-Light.ttf').write_bytes(b'invalid')
   reject=case.endswith('refused')
   try:result=harvest.preparation_dependencies(prepare,p,vendor,fonts,ko,case=='explicit native override accepted')
   except ValueError:
    if not reject:raise
   else:
    if reject:raise AssertionError('Unsafe input accepted: '+case)
    assert len(result[0])==1 and len(result[1])==2
   passed.append(case)
 report={'passed':len(passed),'checks':passed,'sealed_package_verified':True,'target_writes':False}
 (ROOT/'build/preparation-font-adapter-tests.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main(sys.argv[1])
