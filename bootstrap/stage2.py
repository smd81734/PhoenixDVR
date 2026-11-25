#!/usr/bin/env python3
"""Initial installation only. Runs from RAM; eMMC always mounted read-only."""
import base64,importlib.util,json,os,re,secrets,shutil,subprocess,sys,tarfile,time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
sys.path.insert(0,str(Path(__file__).parent))
import harvest
import slots
USB=Path('/mnt/phoenix-usb');DISK=Path('/mnt/recording-disk');STOCK_ROOT=Path('/mnt/phoenix-stock-root')
SECRETS=[]
ETHERNET=os.environ.get('PHOENIX_ETHERNET_INSTALL')=='1'

class ExistingInstallationError(ValueError):
    pass


class RecoverableError(OSError):
    pass

def event(kind,**fields):print('PHOENIX_EVENT '+json.dumps(dict(event=kind,**fields)),flush=True)
def run(*cmd,**kwargs):return subprocess.run(list(cmd),check=True,**kwargs)


def request_retry(code,message,**fields):
    request=secrets.token_hex(8)
    if code=='hdd-space' and 'access' not in fields:fields['access']=maintenance_access()
    event('need-retry',protocol_version=1,retry_id=request,code=code,message=message,**fields)
    line=sys.stdin.readline(2048)
    if not line.startswith('PHOENIX_RETRY '):raise ValueError('Expected retry decision')
    value=json.loads(base64.b64decode(line[14:].strip(),validate=True))
    if value.get('retry_id')!=request or not isinstance(value.get('retry'),bool):raise ValueError('Invalid retry decision')
    if not value['retry']:raise InterruptedError('Setup stopped by the user at a repair checkpoint')
    event('progress',message='Retrying '+code)


def retry_step(code,message,operation):
    while True:
        try:return operation()
        except (OSError,subprocess.SubprocessError):
            # Retry only the named operation; never repeat staging or cutover.
            request_retry(code,message)


def maintenance_access():
    try:
        import recovery
        return recovery.start(ETHERNET)
    except Exception as error:
        return {'ready':False,'error':'Maintenance SSH unavailable: '+type(error).__name__}


def usb_mount():
    for line in Path('/proc/mounts').read_text().splitlines():
        fields=line.split()
        if len(fields)>=4 and fields[1]==str(USB):
            if fields[2]!='vfat' or not re.fullmatch('/dev/sd[a-z]1',fields[0]):raise ValueError('Unexpected filesystem at the USB mount')
            return fields[0]
    return None


def locate_usb():
    USB.mkdir(parents=True,exist_ok=True)
    mounted=usb_mount()
    if mounted:
        try:
            if (USB/'phoenix/usb-id.txt').read_text().strip()=='PHOENIX_INSTALLER_USB_V2':return mounted
        except OSError:pass
        run('umount',str(USB))
    for dev in sorted(Path('/dev').glob('sd[a-z]1')):
        result=subprocess.run(['mount','-t','vfat','-o','rw',str(dev),str(USB)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if result.returncode:continue
        try:
            if (USB/'phoenix/usb-id.txt').read_text().strip()=='PHOENIX_INSTALLER_USB_V2':return str(dev)
        except OSError:pass
        run('umount',str(USB))
    raise RecoverableError('Installer USB not found')


def ensure_usb(expected_manifest):
    device=locate_usb()
    if harvest.sha(USB/'phoenix/SHA256SUMS')!=expected_manifest:raise RecoverableError('Reinsert the original prepared USB payload')
    if device=='/dev/sda1':raise ValueError('USB occupies the internal-disk device')
    return device


def require_readonly_disk():
    mounts=[line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    record=next((fields for fields in mounts if len(fields)>=4 and fields[1]==str(DISK)),None)
    if record is None:raise RecoverableError('Mount the verified HDD read-only before retrying')
    if record[0]!='/dev/sda1' or record[2]!='xfs':raise ValueError('Unexpected filesystem at the HDD mount')
    if 'ro' not in record[3].split(','):raise RecoverableError('Return the HDD mount to read-only before retrying')


def check_space():
    require_readonly_disk()
    free=shutil.disk_usage(DISK).free
    if free<20*1024**3:raise RecoverableError('At least 20 GiB free space is required')
    return free


def configure_network():
    run('ip','link','set','lo','up');run('ip','link','set','eth0','up')
    if not ETHERNET:
        if Path('/sys/class/net/eth0/carrier').read_text().strip()!='1':raise RecoverableError('Ethernet cable is disconnected')
        run('udhcpc','-i','eth0','-q','-n','-f','-t','5','-T','3',timeout=40,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    result=run('ip','-4','-o','addr','show','dev','eth0',capture_output=True,text=True)
    if not re.search(r'\binet\s+\d+\.\d+\.\d+\.\d+/',result.stdout):raise RecoverableError('Ethernet has no IPv4 address')
def unpack(path,destination):
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            if member.name.startswith('/') or '..' in Path(member.name).parts:raise ValueError('Unsafe archive path')
        archive.extractall(destination,filter='fully_trusted')
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def validate_config(c):
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?',c['hostname']):raise ValueError('Invalid hostname')
    if c['band_plan'] not in ('au','eu'):raise ValueError('Invalid band plan')
    for field in ('root_password','tvh_password'):
        if not isinstance(c[field],str) or not 1<=len(c[field])<=128 or re.search(r'[\x00-\x1f\x7f]',c[field]):raise ValueError('Invalid password')
    if not isinstance(c['utc_seconds'],int) or not 1767225600<=c['utc_seconds']<=4102444800:raise ValueError('Invalid host time')
    if not isinstance(c['authorized_key'],str) or '\n' in c['authorized_key'] or len(c['authorized_key'])>2048:raise ValueError('Invalid public key')
    validate_wifi(c)
    return c

def validate_wifi(c):
    # Optional Wi-Fi: the same rules as phoenix-wifi (WPA2 passphrase 8-63 characters,
    # 64 hex digits, or empty for an open network).
    ssid=c.setdefault('wifi_ssid','');password=c.setdefault('wifi_password','')
    if not isinstance(ssid,str) or not isinstance(password,str):raise ValueError('Invalid Wi-Fi settings')
    if not ssid:
        if password:raise ValueError('Invalid Wi-Fi settings')
        return c
    if not 1<=len(ssid.encode('utf-8'))<=32 or re.search(r'[\x00-\x1f\x7f]',ssid):raise ValueError('Invalid Wi-Fi network name')
    if password and (not (8<=len(password)<=63 or re.fullmatch(r'[0-9A-Fa-f]{64}',password)) or re.search(r'[\x00-\x1f\x7f]',password)):raise ValueError('Invalid Wi-Fi password')
    return c

def write_wifi(disk,config):
    # Read by phoenix-wifi at boot; root-only and never logged.
    if not config.get('wifi_ssid'):return False
    folder=Path(disk)/'phoenix-pvr/settings';folder.mkdir(parents=True,exist_ok=True)
    value={'enabled':True,'ssid':config['wifi_ssid'],'psk':config['wifi_password'],'security':'wpa2' if config['wifi_password'] else 'open','cipher':'aes','source':'installer'}
    target=folder/'wifi.json';temporary=folder/'.wifi.json.new'
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)
    return True

def receive_policy():
    event('need-policy',protocol_version=2,message='Waiting for the selected firmware verification policy')
    line=sys.stdin.readline(1024)
    if not line.startswith('PHOENIX_POLICY '):raise ValueError('Expected firmware verification policy')
    policy=json.loads(base64.b64decode(line[15:].strip(),validate=True))
    if not isinstance(policy.get('ignore_sha_mismatches'),bool):raise ValueError('Invalid firmware verification policy')
    return policy['ignore_sha_mismatches']


def receive_config(model,free):
    attrs=None
    if not ETHERNET:
        import termios
        attrs=termios.tcgetattr(sys.stdin.fileno());quiet=attrs.copy();quiet[3]&=~termios.ECHO;termios.tcsetattr(sys.stdin.fileno(),termios.TCSANOW,quiet)
    try:
        while True:
            event('need-config',disk_model=model,free_gib=round(free/1024**3,1),echo_disabled=True)
            line=sys.stdin.readline(16384)
            if not line.startswith('PHOENIX_CONFIG '):raise ValueError('Expected approved installation configuration')
            try:
                config=validate_config(json.loads(base64.b64decode(line[15:].strip(),validate=True)))
                ZoneInfo(config['timezone'])
            except (ValueError,ZoneInfoNotFoundError):
                line=None;config=None
                request_retry('configuration','Configuration was not accepted. Check the hostname, IANA timezone, band plan, passwords and PC clock. Retry reopens configuration without restarting setup.')
                continue
            event('config-accepted',message='Configuration received; passwords are excluded from logs')
            return config
    finally:
        if attrs is not None:termios.tcsetattr(sys.stdin.fileno(),termios.TCSANOW,attrs)

def check_disk(usbdev):
    if usbdev=='/dev/sda1':raise ValueError('USB occupies sda1; internal-disk layout differs')
    if not Path('/dev/sda1').is_block_device():raise RecoverableError('Internal HDD partition missing')
    if Path('/sys/class/block/sda/removable').read_text().strip()!='0':raise ValueError('Internal disk is removable')
    if '/ata' not in str(Path('/sys/class/block/sda/device').resolve()):raise ValueError('sda is not the expected SATA disk')
    return Path('/sys/class/block/sda/device/model').read_text().strip()

def check_initial_target(disk):
    disk=Path(disk)
    existing=[name for name in ('alpine-system','phoenix-pvr','alpine-pvr') if (disk/name).exists() or (disk/name).is_symlink()]
    if existing:
        raise ExistingInstallationError('This box already has an installation.\n\nExisting HDD folders: '+', '.join(existing)+'.\n\nSetup is paused to protect your settings and recordings. No installed files were replaced.')


def review_initial_target():
    while True:
        try:
            require_readonly_disk();check_initial_target(DISK);return
        except RecoverableError:
            request_retry('hdd-readonly','Return the verified HDD mount to read-only, close files on the HDD and Retry.')
        except ExistingInstallationError as error:
            request_retry('existing-installation',str(error)+'\n\nUse a maintenance shell to resolve the listed installation folders, return the HDD mount to read-only, then Retry. Existing folders are never removed or overwritten automatically.',access=maintenance_access())


def commit_initial(system,disk,persistence):
    system=Path(system);disk=Path(disk);active=disk/'alpine-system'
    if any((disk/name).exists() or (disk/name).is_symlink() for name in ('alpine-system','phoenix-pvr','alpine-pvr')):
        raise ValueError('Existing installation appeared before cutover')
    system.rename(active)
    try:
        persistence.initialize(active/'data',disk)
    except BaseException:
        store=disk/'phoenix-pvr'
        if store.exists():store.rename(system.parent/'failed-persistent-store')
        active.rename(system)
        raise
    return active


def hydrate_base_rootfs(package,source):
    package=Path(package);source=Path(source);target=package/'payload/base-rootfs.tar.gz'
    expected=json.loads((package/'MANIFEST.json').read_text())['rootfs_sha256']
    if target.is_symlink() or not target.parent.resolve().is_relative_to(package.resolve()):raise ValueError('Unsafe shared Alpine destination')
    if harvest.sha(source)!=expected:raise ValueError('Shared Alpine image checksum mismatch')
    if not target.exists():
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    if harvest.sha(target)!=expected:raise ValueError('Materialized Alpine image checksum mismatch')
    return target


def finish_committed(work,receipt,expected,usb_manifest,kernel_slot,root_slot):
    def write_boot():
        ensure_usb(usb_manifest)
        for name in (slots.name('b',kernel_slot,root_slot),'boot.txt'):
            boot=USB/'phoenix'/name;boot.write_bytes(expected)
            if boot.read_bytes()!=expected:raise RecoverableError('USB boot trigger verification failed')
    retry_step('usb-write','Phoenix is committed on the HDD. Reinsert the original USB, ensure it is writable, then Retry to finish its boot files. Do not restart installation.',write_boot)
    retry_step('hdd-receipt','Phoenix is committed, but its HDD receipt could not be saved. Check available space and disk access, then Retry. Installation will not run again.',lambda:(work/'installation-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n'))
    retry_step('usb-write','Reinsert the original USB and Retry to save the installation receipt. The installed system will not be recreated.',lambda:(ensure_usb(usb_manifest),(USB/'phoenix/installation-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')))
    os.sync()
    retry_step('hdd-busy','Installation is complete, but the HDD is busy. Run cd /root in all other shells and close HDD files, then Retry.',lambda:run('umount',str(DISK)))
    retry_step('usb-busy','Close any shell or program using the USB and press Retry to finish cleanly.',lambda:run('umount',str(USB)))
    event('complete',**{k:v for k,v in receipt.items() if k!='event'})


def main():
    os.umask(0o077);os.chdir('/')
    if os.uname().release!='4.1.20-1.9' or os.uname().machine!='armv7l':raise ValueError('Unsupported kernel/architecture')
    cmdline=Path('/proc/cmdline').read_text()
    kernel_slot,root_slot=slots.running(cmdline)
    root_device=slots.root_device(root_slot)
    firmware_slots={'kernel':kernel_slot,'root':root_slot,'root_device':str(root_device)}
    event('progress',kernel_slot=kernel_slot,root_slot=root_slot,message=f'Using kernel {kernel_slot}, root {root_slot}; GPT-verified stock root {root_device}')
    event('progress',message='Locating and verifying the installer USB')
    usbdev=retry_step('usb-missing','Insert the prepared Phoenix USB, close any shell using its mount, then press Retry. Keep the DVR powered on.',locate_usb)
    def verify_usb():
        device=locate_usb();run('sha256sum','-c','phoenix/SHA256SUMS',cwd=USB,stdout=subprocess.DEVNULL);return device
    usbdev=retry_step('usb-checksums','USB files could not be verified. Check the USB connection and restore the correct prepared payload, then Retry. Checksums remain required.',verify_usb)
    usb_manifest=harvest.sha(USB/'phoenix/SHA256SUMS')
    model=retry_step('hdd-unavailable','The internal HDD is unavailable. Check its availability, then Retry. No disk will be formatted.',lambda:check_disk(usbdev));DISK.mkdir(parents=True,exist_ok=True)
    retry_step('hdd-mount','The HDD could not be mounted read-only. Close any shell or program holding the disk, then Retry.',lambda:run('mount','-t','xfs','-o','ro,norecovery','/dev/sda1',str(DISK)))
    review_initial_target()
    free=retry_step('hdd-space','Phoenix requires at least 20 GiB free. Free space using your maintenance shell, close files on the HDD, then Retry. The installer does not delete existing files.',check_space)
    # Work in RAM until firmware hashes are known and the operator approves HDD writes.
    package_root=Path('/var/lib/phoenix/package');package_root.mkdir()
    unpack(USB/'phoenix/clean-boot-package.tar.gz',package_root)
    packages=[p for p in package_root.iterdir() if p.is_dir()]
    if len(packages)!=1:raise ValueError('Invalid package root')
    package=packages[0];hydrate_base_rootfs(package,USB/'phoenix/base-rootfs.tar.gz');prepare=module('package_prepare',package/'prepare.py');prepare.verify_package(package)
    STOCK_ROOT.mkdir(parents=True,exist_ok=True);run('mount','-t','ext4','-o','ro,noload',str(root_device),str(STOCK_ROOT))
    ignore_shas=receive_policy()
    if ignore_shas:event('progress',message='Ignore SHA mismatches - Proceed at your own risk! Target file presence and installer/copy integrity stay enforced.')
    event('progress',message='Locating target vendor dependencies; this may take several minutes')
    roots=[STOCK_ROOT,Path('/var/lib/phoenix/initramfs')]
    selection_report=[]
    rows,found=harvest.check(package,roots,lambda msg:event('progress',message=msg),ignore_mismatches=ignore_shas,selection_report=selection_report)
    module_hash=next((digest for digest,name in rows if name=='modules/nexus.ko'),None)
    if module_hash is None:raise ValueError('Selected Nexus module missing')
    slots.validate_module(found[module_hash],os.uname().release)
    config=receive_config(model,free)
    SECRETS.extend([config['root_password'],config['tvh_password']]+([config['wifi_password']] if config.get('wifi_password') else []))
    public_key=Path('/var/lib/phoenix/operator.pub');public_key.write_text(config['authorized_key']+'\n');prepare.public_keys(public_key)
    # Getpass is driven from validated values in RAM, never shell arguments or files.
    run('date','-u','-s','@'+str(config['utc_seconds']),stdout=subprocess.DEVNULL)
    event('progress',message='Configuring Ethernet')
    retry_step('ethernet','Connect Ethernet to a network with DHCP and check the cable/router, then Retry. For direct Ethernet installation, retain the PC cable and reconnect the app if required.',configure_network)
    # Reopen XFS normally only after the explicit GUI configuration approval.
    retry_step('usb-missing','Reinsert the original prepared USB and close any shell using its mount, then Retry.',lambda:ensure_usb(usb_manifest))
    retry_step('hdd-busy','The HDD is busy. Run cd /root in other DVR shells and close files on the HDD, then Retry.',lambda:run('umount',str(DISK)))
    def writable_disk():
        if check_disk(usbdev)!=model:raise ValueError('Internal HDD identity changed')
        run('mount','-t','xfs','-o','rw,noatime','/dev/sda1',str(DISK))
    retry_step('hdd-mount','The verified HDD could not be mounted for installation. Close other disk users, then Retry.',writable_disk)
    work=DISK/('phoenix-install-'+str(int(time.time()))+'-'+secrets.token_hex(4))
    work.mkdir(mode=0o700,exist_ok=False)
    # Move the package to HDD, keeping preparation below the RAM budget.
    shutil.move(str(package),work/package.name);package=work/package.name
    vendor=harvest.copy(rows,found,work/'dependencies')
    (work/'target-harvest.json').write_text(json.dumps({'ignore_sha_mismatches':ignore_shas,'files':selection_report},indent=2)+'\n')
    run('umount',str(STOCK_ROOT))
    authorized=public_key
    candidate=work/'candidate'
    args=SimpleNamespace(package=package,output=candidate,vendor_root=vendor/'runtime',fonts=vendor/'fonts',module=vendor/'modules/nexus.ko',authorized_key=authorized,validate_only=False,hostname=config['hostname'],band_plan=config['band_plan'],timezone=config['timezone'],allowed=None,tvh_user='admin',seed_port=19981,fresh_setup=True,ignore_target_shas=ignore_shas)
    answers=iter([config['root_password']]*2+[config['tvh_password']]*2)
    prepare.getpass.getpass=lambda prompt:next(answers)
    event('progress',message='Preparing the complete Phoenix system and private credentials')
    prepare.dependencies=lambda package,vendor,fonts,ko,ignore_mismatch=False:harvest.preparation_dependencies(prepare,package,vendor,fonts,ko,ignore_mismatch)
    slots.specialize_renderer(prepare,kernel_slot,root_slot)
    prepare.prepare(args);del answers;config.pop('root_password');config.pop('tvh_password')
    candidate_meta=candidate/'candidate.json';candidate_data=json.loads(candidate_meta.read_text());candidate_data['firmware_slots']=firmware_slots;candidate_meta.write_bytes((json.dumps(candidate_data,indent=2)+'\n').encode('utf-8'))
    activate=module('candidate_verifier',package/'activate.py');system,metadata=activate.verify(candidate)
    expected=slots.render((package/'project/source/boot/hdd/boot.txt').read_text(),kernel_slot,root_slot).encode('ascii')
    if (system/'boot/boot.txt').read_bytes()!=expected:raise ValueError('Prepared boot script differs from selected firmware slots')
    persistence=prepare.module('initial_persistence',package/'project/source/provision/persistence.py')
    # This is initial installation, not the existing-core activation path.
    if (DISK/'alpine-system').exists():raise ValueError('An installed core appeared; refusing cutover')
    event('progress',message='Committing verified system; retaining existing HDD files')
    retry_step('usb-missing','Reinsert the original prepared USB before committing Phoenix, then Retry.',lambda:ensure_usb(usb_manifest))
    commit_initial(system,DISK,persistence)
    if write_wifi(DISK,config):event('progress',message='Wi-Fi settings saved for '+config['wifi_ssid'])
    config.pop('wifi_password',None)
    receipt={'event':'complete','message':('Installation verified; HDD unmounted. Phoenix USB boot is configured.' if ETHERNET else 'Installation verified; HDD unmounted. Recapture BOLT to enable Phoenix boot.'),'package':metadata['package_id'],'ssh_host_fingerprint':metadata['ssh_host_fingerprint'],'hdd_model':model,'boot_trigger':'USB required','hardware_boot_test':'pending','ignore_sha_mismatches':ignore_shas}
    receipt['firmware_slots']=firmware_slots
    finish_committed(work,receipt,expected,usb_manifest,kernel_slot,root_slot)


if __name__=='__main__':
    try:main()
    except BaseException as error:
        # No password values or exception tracebacks are emitted on the wire.
        detail=str(error)
        for secret in SECRETS:detail=detail.replace(secret,'[redacted]')
        if isinstance(error,ExistingInstallationError):
            try:
                import recovery
                access=recovery.start(ETHERNET)
            except Exception as access_error:
                access={'ready':False,'error':'Recovery SSH unavailable: '+type(access_error).__name__}
            event('failed',code='existing-installation',message=detail[:1000],access=access)
        else:
            boot_note=(' The USB installer boot hook is already selected; keep the box powered on for recovery, or remove USB for stock fallback.' if ETHERNET else '. STARTUP has not been changed.')
            event('failed',message='Installer stopped: '+type(error).__name__+': '+detail[:1000]+boot_note)
        raise SystemExit(1)
