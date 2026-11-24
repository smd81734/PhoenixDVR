"""Assemble the USB payload from runtime inputs and project source."""
import argparse,hashlib,json,shutil,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bootstrap'))
import slots
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def build(output):
    output=Path(output);baseline=ROOT/'inputs/runtime';receipt=json.loads((ROOT/'BASELINE.json').read_text())
    if output.exists():raise ValueError('Output already exists')
    assets=('base-rootfs.tar.gz','clean-boot-package.tar.gz','check-busybox','check-ld-musl-armhf.so.1')
    for name in assets:
        relative='usb-payload/phoenix/'+name
        if sha(baseline/relative)!=receipt['baseline_files_sha256'][relative]:raise ValueError('Runtime asset hash mismatch: '+name)
    (output/'phoenix').mkdir(parents=True)
    for name in assets:shutil.copyfile(baseline/'usb-payload/phoenix'/name,output/'phoenix'/name)
    (output/'phoenix/usb-id.txt').write_bytes(b'PHOENIX_INSTALLER_USB_V2\n')
    spec=importlib.util.spec_from_file_location('runtime_source',ROOT/'tools/runtime-source.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    runtime.repack(baseline/'usb-payload/phoenix/clean-boot-package.tar.gz',output/'phoenix/clean-boot-package.tar.gz',ROOT/'runtime')
    for name in ('stage1.sh','stage2.py','harvest.py','network-bridge.py','diagnostics-init.sh','recovery.py','slots.py'):
        (output/'phoenix'/name).write_text((ROOT/'bootstrap'/name).read_text(encoding='utf-8-sig'),encoding='utf-8',newline='\n')
    # Ethernet selects the final boot hook once. It initially enters the existing
    # signed-kernel installer; successful stage2 replaces this with normal boot.
    spec=importlib.util.spec_from_file_location('payload_builder',ROOT/'tools/build-payload.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    command=builder.installer_template()
    slots.write_entries(output/'phoenix',command)
    sums=output/'phoenix/SHA256SUMS';manifest=output/'payload.json'
    files=sorted(p for p in output.rglob('*') if p.is_file() and p not in (sums,manifest))
    sums.write_bytes(''.join(sha(p)+'  '+p.relative_to(output).as_posix()+'\n' for p in files).encode('ascii'))
    files=sorted(files+[sums]);data={'Format':1,'ProtocolVersion':5,'Release':'preview27','HardwareValidated':False,'Files':[{'Path':p.relative_to(output).as_posix(),'Sha256':sha(p),'Bytes':p.stat().st_size} for p in files]}
    manifest.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
    for name in ('base-rootfs.tar.gz','check-busybox','check-ld-musl-armhf.so.1'):
        if sha(output/'phoenix'/name)!=receipt['baseline_files_sha256']['usb-payload/phoenix/'+name]:raise ValueError('Runtime asset verification failed: '+name)
    return {'payload':str(output),'files':len(files),'base_and_verifier_bytes_preserved':True,'compiled_runtime_members_preserved':True,'runtime_sources_prepared':True,'hardware_install_test':'pending'}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.output),indent=2))
