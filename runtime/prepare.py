#!/usr/bin/env python3
"""Stage a clean replacement for the DVR; never stop or replace installed services."""
import argparse
import base64
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tarfile

DISK = Path('/mnt/recording-disk')


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def verify_package(package):
    package = Path(package).resolve()
    rows = []
    for line in (package/'SHA256SUMS').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        path = (package/relative).resolve()
        if not path.is_relative_to(package) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid package manifest path or checksum')
        if sha(path) != expected:
            raise ValueError('Package checksum mismatch: ' + relative)
        rows.append(relative)
    if not rows:
        raise ValueError('Empty package manifest')
    return json.loads((package/'MANIFEST.json').read_text())


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fingerprint_files(manifest, root, ignore_mismatch=False, replacements=None):
    result = []
    for line in Path(manifest).read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        expected, relative = line.split()[:2]
        if expected.startswith('LINK:'):
            raise ValueError('Unimplemented vendor-link manifest record')
        relative = (replacements or {}).get(relative,relative)
        path = Path(root)/relative
        if not path.is_file(): raise ValueError('Required target dependency is missing: '+relative)
        if expected != 'PRESENT' and sha(path) != expected and not ignore_mismatch:
            raise ValueError('Target dependency differs: ' + relative)
        result.append((path, relative))
    return result


def dependencies(package, vendor, fonts, ko, ignore_mismatch=False):
    catalog = json.loads((package/'project/dependencies/catalog.json').read_text())
    files = fingerprint_files(package/'project/dependencies/vendor-files.sha256', vendor, ignore_mismatch)
    browser_module = module('package_browser',package/'project/source/frontend/browser.py')
    executable = browser_module.newest(Path(vendor).glob('opt/*/bin/chrome.*'))
    browser = executable.parent.parent
    browser_files = fingerprint_files(package/'project/dependencies/browser-files.sha256', browser,
                                     ignore_mismatch, {'bin/chrome.202504071556':'bin/'+executable.name})
    # Copy the complete remote-map directory. File names are vendor-owned.
    maps = browser/'bin/irkeymap'
    if not maps.is_dir(): raise ValueError('Missing remote-map directory')
    map_files = sorted(p for p in maps.rglob('*') if p.is_file())
    if not map_files: raise ValueError('Empty remote-map directory')
    for source in map_files:
        if not source.resolve().is_relative_to(maps.resolve()): raise ValueError('Remote-map link escapes its directory')
        browser_files.append((source, source.relative_to(browser).as_posix()))
    for font in catalog['downloads']['fonts']:
        if not (Path(fonts)/font['name']).is_file(): raise ValueError('Target font is missing: '+font['name'])
        if sha(Path(fonts)/font['name']) != font['sha256'] and not ignore_mismatch:
            raise ValueError('Target font differs: ' + font['name'])
    if not Path(ko).is_file(): raise ValueError('Target kernel module is missing')
    if sha(ko) != catalog['target_firmware']['module_sha256'] and not ignore_mismatch:
        raise ValueError('Target kernel module differs')
    return files, browser_files, catalog


def copy_verified(files, destination):
    for source, relative in files:
        target = Path(destination)/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(stat.S_IMODE(source.stat().st_mode))
        if sha(source) != sha(target):
            raise ValueError('Dependency copy verification failed')


def public_keys(path):
    keys = []
    for line in Path(path).read_text().splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+)', parts[0]):
            continue
        try:
            wire = base64.b64decode(parts[1], validate=True)
            size = int.from_bytes(wire[:4], 'big')
            valid = wire[4:4+size].decode() == parts[0]
        except (ValueError, UnicodeError):
            valid = False
        if valid:
            keys.append(' '.join(parts))
    if not keys:
        raise ValueError('No usable OpenSSH public key; provide --authorized-key')
    return '\n'.join(dict.fromkeys(keys))+'\n'


def unpack(archive, destination):
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            if member.name.startswith('/') or '..' in Path(member.name).parts:
                raise ValueError('Unsafe archive member')
        # Only package-checksummed, builder-produced Linux rootfs/application archives.
        bundle.extractall(destination, filter='fully_trusted')


def seed_tvh(package, root, user, password, allowed, port, auto_lan=False):
    setup = module('phoenix_tvh_setup', package/'project/source/tvheadend/setup.py')
    inside = '/var/lib/phoenix/tvh-seed'
    config = root/inside.lstrip('/')
    config.mkdir(parents=True)
    log = (root.parent/'tvh-seed.log').open('wb')
    command = ['chroot', str(root), '/usr/bin/tvheadend', '-c', inside, '-u', 'root', '-g', 'root',
               '-C', '--noacl', '-b', '127.0.0.1', '--http_port', str(port), '--htsp_port', str(port+1),
               '-a', '-1', '--nosatipcli', '-S']
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        api = setup.API('http://127.0.0.1:%d' % port)
        setup.seed(api, user, password, allowed, auto_lan=auto_lan)
        defaults = [row for row in api.grid('dvr/config/grid') if not row.get('name')]
        if len(defaults) != 1:
            raise ValueError('Expected one default DVR profile')
        uuid = defaults[0]['uuid']
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        log.close()
    return config, uuid


def confirmed_password(label):
    while True:
        password = getpass.getpass('New '+label+' password: ')
        confirmation = getpass.getpass('Confirm '+label+' password: ')
        if not password:
            print(label+' password must not be empty. Please try again.')
        elif password != confirmation:
            print(label+' passwords do not match. Please try again.')
        else:
            return password


def prepare(args):
    if not args.tvh_user.strip() or args.tvh_user.strip() == '*': raise ValueError('Choose a named Tvheadend administrator')
    package = args.package.resolve()
    manifest = verify_package(package)
    if os.geteuid() != 0 or os.uname().machine != 'armv7l':
        raise ValueError('Run on the armv7 box as root')
    if os.uname().release != '4.1.20-1.9' or 'bmem=768m@1279m bmem=970m@2099m' not in Path('/proc/cmdline').read_text():
        raise ValueError('Unsupported kernel or memory layout')
    if not any(line.split()[:3] == ['/dev/sda1', str(DISK), 'xfs'] for line in Path('/proc/mounts').read_text().splitlines()):
        raise ValueError('Expected XFS disk is not mounted at the supported path')
    vendor_files, browser_files, catalog = dependencies(package, args.vendor_root, args.fonts, args.module, getattr(args,'ignore_target_shas',False))
    keys = public_keys(args.authorized_key)
    if args.validate_only:
        print('PACKAGE_AND_TARGET_DEPENDENCIES_VERIFIED', manifest['id'])
        return
    output = args.output.resolve()
    if output == DISK or not output.is_relative_to(DISK) or output.is_relative_to(DISK/'alpine-system'):
        raise ValueError('Candidate must be a new directory outside the active alpine-system')
    model = Path('/sys/class/block/sda/device/model').read_text().strip()
    sectors = int(Path('/sys/class/block/sda/size').read_text())
    part = int(Path('/sys/class/block/sda1/size').read_text())
    # The installer may be on a temporary direct link. Runtime DHCP supplies
    # the household subnet; never persist the installation transport as the LAN.
    auto_lan = not args.allowed
    allowed = ['127.0.0.1/32'] + [str(ipaddress.ip_network(v.strip(), strict=False)) for v in (args.allowed or '').split(',') if v.strip()]
    with socket.socket() as check:
        check.bind(('127.0.0.1', args.seed_port))
    root_password = confirmed_password('root')
    persistent = module('phoenix_persistence', package/'project/source/provision/persistence.py')
    identity_file = persistent.identity_path(Path('/data'), DISK)
    previous_config = DISK/'phoenix-pvr/tvheadend/config'
    if not previous_config.is_dir(): previous_config = Path('/data/tvheadend/config')
    saved_identity = json.loads(identity_file.read_text()) if identity_file.is_file() and previous_config.is_dir() else None
    if getattr(args, 'fresh_setup', False): saved_identity = None
    if saved_identity is not None and not {'url','username','password','dvr_config_uuid'} <= saved_identity.keys():
        raise ValueError('Existing backend connection settings are incomplete')
    tvh_password = None if saved_identity is not None else confirmed_password('Tvheadend')
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    system = output/'alpine-system'; system.mkdir()
    root = output/'rootfs'; root.mkdir()
    unpack(package/'payload/base-rootfs.tar.gz', root)
    unpack(package/'payload/apps.tar.gz', system)
    shutil.copytree(package/'project/source/boot/rootfs', root, dirs_exist_ok=True)
    for original in (package/'project/source/boot/rootfs').rglob('*'):
        if original.is_file():
            relative = original.relative_to(package/'project/source/boot/rootfs')
            (root/relative).chmod(0o755 if 'usr/local/' in relative.as_posix() else 0o644)
    for folder in ('usr/local/bin', 'usr/local/sbin'):
        for path in (root/folder).iterdir(): path.chmod(0o755)
    renderer = module('phoenix_device_layout', package/'project/tools/render-device-config.py')
    layout = output/'layout'
    renderer.render(layout, model, sectors, part, args.hostname, args.band_plan, args.timezone)
    shutil.copytree(layout/'hdd/alpine-system', system, dirs_exist_ok=True)
    shutil.copytree(layout/'rootfs', root, dirs_exist_ok=True)
    (root/'etc/phoenix/firmware-policy').write_text('RELAXED_VENDOR_HASHES_V1\n' if getattr(args,'ignore_target_shas',False) else 'STRICT_VENDOR_HASHES_V1\n')
    data = system/'data'; runtime = data/'nexus-loader-test-v1'
    copy_verified(vendor_files, runtime)
    copy_verified(browser_files, runtime/'opt/chromium')
    browser_module=module('verify_browser',package/'project/source/frontend/browser.py')
    browser_executable=browser_module.select(runtime/'opt/chromium')
    subprocess.run(['chroot',str(runtime),'/lib/ld-linux-armhf.so.3','--list','--library-path',
                    '/opt/chromium/lib:/lib:/opt/blit/lib:/opt/tuner-libs',
                    '/opt/chromium/bin/'+browser_executable.name],check=True,stdout=subprocess.DEVNULL)
    # Retain the fingerprinted stock launcher as input for future clean preparations.
    for name in ('tmp', 'dev', 'proc', 'sys', 'dev/shm'):
        (runtime/name).mkdir(parents=True, exist_ok=True)
    (runtime/'tmp').chmod(0o1777); (runtime/'dev/shm').chmod(0o1777)
    for name in ('null', 'random', 'urandom'):
        source = Path('/dev')/name; device = runtime/'dev'/name
        meta = source.stat(); os.mknod(device, meta.st_mode, meta.st_rdev)
    for name in ('null', 'zero', 'random', 'urandom'):
        device = root/'dev'/name
        if not device.exists():
            meta = (Path('/dev')/name).stat(); os.mknod(device, meta.st_mode, meta.st_rdev)
    for font in catalog['downloads']['fonts']:
        destination = data/'native-frontend/fonts'/font['name']; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.fonts/font['name'], destination)
        if sha(args.fonts/font['name'])!=sha(destination): raise ValueError('Font copy verification failed: '+font['name'])
    (system/'modules').mkdir(); shutil.copyfile(args.module, system/'modules/nexus.ko')
    if sha(args.module)!=sha(system/'modules/nexus.ko'): raise ValueError('Kernel module copy verification failed')
    shutil.copyfile(root/'etc/ssl/cert.pem', runtime/'host.cert')
    subprocess.run(['chroot', str(root), '/usr/sbin/chpasswd', '-c', 'SHA512'],
                   input='root:'+root_password+'\n', text=True, check=True)
    del root_password
    (root/'root/.ssh').mkdir(mode=0o700)
    (root/'root/.ssh/authorized_keys').write_text(keys); (root/'root/.ssh/authorized_keys').chmod(0o600)
    subprocess.run(['chroot', str(root), '/usr/bin/dropbearkey', '-t', 'ed25519', '-f',
                    '/etc/dropbear/dropbear_ed25519_host_key'], check=True, stdout=subprocess.DEVNULL)
    identity = subprocess.check_output(['chroot', str(root), '/usr/bin/dropbearkey', '-y', '-f',
                                       '/etc/dropbear/dropbear_ed25519_host_key'], text=True)
    fingerprint = next((line.strip() for line in identity.splitlines() if 'SHA256:' in line), '')
    (data/'tvheadend').mkdir(exist_ok=True)
    if saved_identity is None:
        config, dvr_uuid = seed_tvh(package, root, args.tvh_user, tvh_password, ','.join(dict.fromkeys(allowed)), args.seed_port, auto_lan=auto_lan)
        persistent.configure_offline(config, DISK)
        (data/'tvheadend').mkdir(exist_ok=True)
        shutil.move(config, data/'tvheadend/config')
        identity={'url':'http://127.0.0.1:9981','username':args.tvh_user,'password':tvh_password,'dvr_config_uuid':dvr_uuid}
    else:
        identity=saved_identity
        print('EXISTING_TVHEADEND_SETTINGS_WILL_BE_REUSED')
    (data/'native-frontend/config.json').write_text(json.dumps(identity))
    (data/'native-frontend/config.json').chmod(0o600)
    del tvh_password
    unpack(package/'payload/channel-icons.tar.gz', data/'phoenix/bootstrap-icons')
    for name in ('null', 'zero', 'random', 'urandom'): (root/'dev'/name).unlink()
    shutil.rmtree(root/'var/lib/phoenix', ignore_errors=True)
    archive = system/'boot/alpine.tar.gz'
    with tarfile.open(archive, 'w:gz', format=tarfile.GNU_FORMAT, compresslevel=6) as bundle: bundle.add(root, arcname='.')
    archive.chmod(0o600)
    critical = sorted(p.relative_to(system).as_posix() for p in system.rglob('*') if p.is_file() and not p.is_symlink())
    ready = {'state':'ready','fresh_setup':getattr(args,'fresh_setup',False),'ignore_target_shas':getattr(args,'ignore_target_shas',False),'ssh_host_fingerprint':fingerprint,'package_id':manifest['id'],'disk_model':''.join(model.split()),
             'disk_sectors':sectors,'part_sectors':part,'files':{p:sha(system/p) for p in critical}}
    (output/'candidate.json').write_text(json.dumps(ready,indent=2)+'\n')
    shutil.rmtree(root); shutil.rmtree(layout)
    os.sync()
    print('CANDIDATE_READY', output)
    print('NEW_SSH_HOST_IDENTITY', fingerprint)
    print('Not activated. Review candidate.json, then use activate.py --candidate PATH --apply.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, default=DISK/'phoenix-candidates/clean-boot-r12')
    parser.add_argument('--vendor-root', type=Path, default=Path('/data/nexus-loader-test-v1'))
    parser.add_argument('--fonts', type=Path, default=Path('/data/native-frontend/fonts'))
    parser.add_argument('--module', type=Path, default=DISK/'alpine-system/modules/nexus.ko')
    parser.add_argument('--authorized-key', type=Path, default=Path('/root/.ssh/authorized_keys'))
    parser.add_argument('--hostname', default='phoenix')
    parser.add_argument('--band-plan', choices=('au','eu'), default='au')
    parser.add_argument('--timezone', default='UTC')
    parser.add_argument('--allowed', help='Fixed allowed networks; by default follow the runtime Ethernet LAN')
    parser.add_argument('--tvh-user', default='admin')
    parser.add_argument('--seed-port', type=int, default=19981)
    parser.add_argument('--ignore-target-shas', action='store_true', help='Ignore known target firmware SHA mismatches - Proceed at your own risk! Package and copy hashes stay enforced.')
    parser.add_argument('--fresh-setup', action='store_true', help='Archive settings at activation, keep recordings, and return to Welcome')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.seed_port < 65535: parser.error('Invalid seed port')
    prepare(args)


if __name__ == '__main__':
    main()
