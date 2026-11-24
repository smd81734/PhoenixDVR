#!/usr/bin/env python3
"""RAM-resident, authenticated Phoenix core updater. Never reset persistent state."""
import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

DISK = Path('/mnt/recording-disk')
STORE = DISK/'phoenix-pvr'
ACTIVE = DISK/'alpine-system'
KEY = Path(__file__).with_name('update-public-key.json')
LIVE = Path('/')
LOG = Path('/var/log')
LOCK = Path('/run/phoenix-update.lock')
MAX_PACKAGE = 512 * 1024 * 1024
FRONTEND = {'browser.py', 'initial_setup.py', 'frontend.py', 'html_frontend.py', 'epg_settings.py', 'xmltv.py', 'version.txt',
            'guide.py', 'guide.js', 'guide.css', 'menu.html', 'control.sh', 'html-control.sh', 'phoenix.png'}
MANAGER = {'autostart.sh', 'control.sh', 'launch.sh', 'sweep.py', 'capacity.py'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    temporary = path.with_name(path.name+'.new')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def allowed(name):
    parts = PurePosixPath(name).parts
    if not parts or '..' in parts or str(PurePosixPath(name)) != name or name.startswith('/'):
        return False
    if name.startswith('data/native-frontend/'):
        return name.removeprefix('data/native-frontend/') in FRONTEND
    if name.startswith('data/nexus-tuner-manager/'):
        return name.removeprefix('data/nexus-tuner-manager/') in MANAGER
    if name in {'data/phoenix/persistence.py', 'data/phoenix/update.py', 'data/phoenix/update-public-key.json', 'data/phoenix/tvheadend/setup.py',
                'data/phoenix/tvheadend/channel-icons.tsv', 'boot/alpine-init'}:
        return True
    if name.startswith('data/nexus-loader-test-v1/opt/frontend/'):
        return name.rsplit('/', 1)[-1] in {'mkindex', 'phoenix-player', 'player-controls.so', 'player-status.so', 'player-livefile.so'} and len(parts) == 5
    if name == 'data/nexus-loader-test-v1/opt/tuner-manager/tuner-manager':
        return True
    return len(parts) == 5 and parts[:4] in [('rootfs', 'usr', 'local', 'sbin'), ('rootfs', 'usr', 'local', 'bin')]


def verify_signature(raw, signature, key):
    n = int.from_bytes(base64.b64decode(key['n'], validate=True), 'big')
    e = int.from_bytes(base64.b64decode(key['e'], validate=True), 'big')
    size = (n.bit_length()+7)//8
    if size < 384 or e != 65537 or len(signature) != size or int.from_bytes(signature, 'big') >= n:
        raise ValueError('Invalid update signing key or signature')
    digest_info = bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(raw).digest()
    encoded = b'\x00\x01'+b'\xff'*(size-len(digest_info)-3)+b'\x00'+digest_info
    if pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big') != encoded:
        raise ValueError('Update signature rejected')


def package(path, key=KEY, allow_unsigned=False):
    if path.stat().st_size > MAX_PACKAGE:
        raise ValueError('Update package too large')
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or len(names) > 500:
            raise ValueError('Duplicate or excessive update members')
        if any(not m.isfile() or m.size > MAX_PACKAGE for m in members) or sum(m.size for m in members) > MAX_PACKAGE:
            raise ValueError('Invalid update member types or sizes')
        by_name = {m.name: m for m in members}
        signed = 'signature.bin' in by_name
        if 'manifest.json' not in by_name or by_name['manifest.json'].size > 262144 or (signed and by_name['signature.bin'].size > 1024):
            raise ValueError('Missing signed manifest')
        raw = archive.extractfile(by_name['manifest.json']).read()
        if signed:
            verify_signature(raw, archive.extractfile(by_name['signature.bin']).read(), json.loads(key.read_text()))
        elif not allow_unsigned:
            raise ValueError('Unsigned package requires explicit user approval')
        manifest = json.loads(raw)
        if manifest.get('format') != 1 or manifest.get('settings_migration') != 'none' or manifest.get('kernel') != '4.1.20-1.9' or manifest.get('alpine') != '3.24.3':
            raise ValueError('Incompatible update format, base or settings migration')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', manifest.get('version', '')):
            raise ValueError('Invalid release version')
        files = manifest.get('files', {})
        if set(names) != ({'manifest.json', 'signature.bin'} if signed else {'manifest.json'}) | {'files/'+name for name in files}:
            raise ValueError('Update archive differs from manifest')
        if not {'data/native-frontend/frontend.py', 'data/native-frontend/version.txt'} <= files.keys():
            raise ValueError('Missing frontend/version')
        result = {}
        for name, row in files.items():
            if not allowed(name) or row.get('mode') not in (0o644, 0o755):
                raise ValueError('Forbidden update target: '+name)
            data = archive.extractfile(by_name['files/'+name]).read()
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('Update checksum differs: '+name)
            result[name] = data
        if result['data/native-frontend/version.txt'].decode().strip() != manifest['version']:
            raise ValueError('Version file differs from signed release')
        return manifest, result


def exchange(left, right):
    # Linux 4.1/XFS supports renameat2; refuse devices lacking atomic exchange.
    numbers = {'armv7l': 382, 'x86_64': 316, 'aarch64': 276}
    number = numbers.get(platform.machine())
    if number is None: raise ValueError('Unsupported atomic-exchange architecture')
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(number), ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(left)),
                          ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(right)), ctypes.c_uint(2))
    if result: raise OSError(ctypes.get_errno(), 'Atomic core exchange unavailable')


def identity():
    return {'model': ''.join(Path('/sys/class/block/sda/device/model').read_text().split()),
            'sectors': Path('/sys/class/block/sda/size').read_text().strip(),
            'partition': Path('/sys/class/block/sda1/size').read_text().strip()}


def environment():
    if os.geteuid() != 0 or platform.machine() != 'armv7l' or platform.release() != '4.1.20-1.9':
        raise ValueError('Requires the supported Phoenix ARM kernel as root')
    if 'bmem=768m@1279m bmem=970m@2099m' not in Path('/proc/cmdline').read_text():
        raise ValueError('Unsupported memory layout')
    if Path('/etc/alpine-release').read_text().strip() != '3.24.3':
        raise ValueError('Unsupported installed Alpine base')
    mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    if not any(row[:3] == ['/dev/sda1', str(DISK), 'xfs'] and 'rw' in row[3].split(',') for row in mounts):
        raise ValueError('Expected writable recording HDD is not mounted')
    if ACTIVE.is_symlink() or STORE.is_symlink() or (STORE/'recordings').is_symlink():
        raise ValueError('Unexpected storage links')
    if (ACTIVE/'storage-id.txt').read_text().strip() != 'ALPINE_HDD_SYSTEM_V1' or (STORE/'storage-id.txt').read_text().strip() != 'ALPINE_RECORDING_DISK_V1':
        raise ValueError('Installed system/storage identity differs')
    if not (STORE/'settings/.initialized').is_file():
        raise ValueError('Persistent settings must already be initialized')
    actual = identity()
    policy = {}
    for line in Path('/etc/phoenix/disk.conf').read_text().splitlines():
        if '=' in line:
            name, value = line.split('=', 1)
            policy[name] = shlex.split(value)[0]
    if actual != {'model': policy.get('DISK_MODEL'), 'sectors': policy.get('DISK_SECTORS'), 'partition': policy.get('PART_SECTORS')}:
        raise ValueError('HDD differs from the installation disk policy')
    if not os.path.samefile('/data', ACTIVE/'data'):
        raise ValueError('Updates require the running installed core, not temporary installation/recovery Linux')
    return actual


def api(path):
    config = json.loads((STORE/'settings/frontend.json').read_text())
    # The update worker never sends backend credentials to a configurable host.
    base = 'http://127.0.0.1:9981'
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, base, config['username'], config['password'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPDigestAuthHandler(manager))
    with opener.open(base+'/api/'+path, timeout=10) as response:
        data = response.read(4*1024*1024+1)
    if len(data) > 4*1024*1024: raise ValueError('Backend response exceeds limit')
    return json.loads(data)


def recording_status():
    rows = []
    for start in range(0, 10000, 200):
        page = api('dvr/entry/grid_upcoming?start=%d&limit=200' % start)
        entries = page.get('entries')
        if not isinstance(entries, list): raise ValueError('Cannot verify recording status')
        rows.extend(entries)
        if len(entries) < 200: break
    else: raise ValueError('Recording list exceeds limit')
    active, soon = [], []
    for row in rows:
        state = str(row.get('sched_status', row.get('state', ''))).lower()
        title = str(row.get('disp_title') or row.get('title') or 'Recording')[:120]
        if state.startswith('recording'): active.append(title)
        elif state in ('scheduled', 'pending') and time.time()-60 <= int(row.get('start', 0)) <= time.time()+300:
            soon.append(title)
    return {'active_recordings': active, 'starting_soon': soon, 'can_apply': not active and not soon}


def require_idle():
    result = recording_status()
    if not result['can_apply']:
        raise ValueError('Update deferred: recording active or scheduled within five minutes')
    return result


def txn_path(value):
    if not re.fullmatch('[0-9a-f]{32}', value): raise ValueError('Invalid update session')
    parent = DISK/'phoenix-updates'
    if parent.is_symlink(): raise ValueError('Update directory must not be a symlink')
    parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    path = parent/value
    if path.is_symlink(): raise ValueError('Update session must not be a symlink')
    return path


def read_state(path): return json.loads((path/'state.json').read_text())


def save_state(path, state, phase, **fields):
    state.update(phase=phase, updated=time.time(), **fields)
    atomic_json(path/'state.json', state)


def clone_core(source, target):
    # Runtime mountpoints and browser scratch data are regenerated at boot.
    volatile = {'data/nexus-loader-test-v1/proc', 'data/nexus-loader-test-v1/sys',
                'data/nexus-loader-test-v1/tmp', 'data/nexus-loader-test-v1/dev',
                'data/nexus-loader-test-v1/media', 'data/nexus-loader-test-v1/opt/chromium/html-menu'}
    target.mkdir(mode=stat.S_IMODE(source.stat().st_mode))
    target.chmod(stat.S_IMODE(source.stat().st_mode))
    for child in source.iterdir():
        relative = child.relative_to(ACTIVE).as_posix()
        destination = target/child.name
        meta = child.lstat()
        if relative in volatile:
            destination.mkdir(mode=0o1777 if child.name == 'tmp' else 0o755)
            destination.chmod(0o1777 if child.name == 'tmp' else 0o755)
            if child.name == 'dev':
                (destination/'shm').mkdir(mode=0o1777)
                (destination/'shm').chmod(0o1777)
                for name in ('null', 'zero', 'random', 'urandom'):
                    node = (Path('/dev')/name).stat()
                    os.mknod(destination/name, node.st_mode, node.st_rdev)
        elif child.is_symlink(): destination.symlink_to(os.readlink(child))
        elif stat.S_ISDIR(meta.st_mode): clone_core(child, destination)
        elif stat.S_ISREG(meta.st_mode): shutil.copy2(child, destination)
        # Sockets, pipes and runtime devices are never read as file data.


def write_payload(root, name, data, mode):
    destination = root/name
    if destination.is_symlink() or not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError('Update target follows an external link')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data); destination.chmod(mode)


def credential_hashes():
    files = [LIVE/name for name in ('etc/shadow', 'etc/passwd', 'etc/group', 'etc/hostname', 'etc/phoenix/disk.conf')]
    for folder in ('etc/dropbear', 'root/.ssh'):
        source = LIVE/folder
        if source.is_symlink() or not source.is_dir(): raise ValueError('Missing SSH identity directory')
        files.extend(p for p in source.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in files): raise ValueError('Unexpected identity link')
    return {p.relative_to(LIVE).as_posix(): sha(p) for p in files}


def preserve_identity(rootfs):
    for name in ('etc/dropbear', 'root/.ssh'):
        target, source = rootfs/name, LIVE/name
        if target.is_symlink(): raise ValueError('Unexpected identity link in boot image')
        if target.exists(): shutil.rmtree(target)
        target.mkdir(parents=True, mode=stat.S_IMODE(source.stat().st_mode))
        target.chmod(stat.S_IMODE(source.stat().st_mode))
    for filename in credential_hashes():
        source = LIVE/filename
        write_payload(rootfs, filename, source.read_bytes(), stat.S_IMODE(source.stat().st_mode))


def refresh_rollback_identity(path):
    root = path/'rollback-rootfs'
    root.mkdir(mode=0o700)
    boot = path/'core/boot/alpine.tar.gz'
    with tarfile.open(boot) as archive: archive.extractall(root, filter='fully_trusted')
    preserve_identity(root)
    temporary = boot.with_suffix('.new')
    with tarfile.open(temporary, 'w:gz', format=tarfile.GNU_FORMAT) as archive: archive.add(root, arcname='.')
    temporary.chmod(0o600); temporary.replace(boot)
    shutil.rmtree(root)


def stage(path):
    state = read_state(path)
    if state['phase'] not in ('uploaded', 'stage-queued'): raise ValueError('This session cannot be staged again')
    if sha(path/'package.tar.gz') != state['package_sha256']: raise ValueError('Transferred package differs')
    manifest, files = package(path/'package.tar.gz', allow_unsigned=state.get('allow_unsigned', False))
    if environment() != state['device']: raise ValueError('Device identity changed')
    if shutil.disk_usage(DISK).free < max(2*1024**3, sum(p.stat().st_size for p in ACTIVE.rglob('*') if p.is_file() and not p.is_symlink())*2):
        raise ValueError('Insufficient disk space for a staged core and backup')
    left, right = path/'exchange-probe-a', path/'exchange-probe-b'
    left.mkdir(); right.mkdir()
    try: exchange(left, right)
    finally: left.rmdir(); right.rmdir()
    save_state(path, state, 'staging')
    core = path/'core'
    clone_core(ACTIVE, core)
    rootfs = path/'rootfs'
    rootfs.mkdir(mode=0o700)
    # This archive is the installed device's own trusted private boot image.
    with tarfile.open(ACTIVE/'boot/alpine.tar.gz') as archive:
        archive.extractall(rootfs, filter='fully_trusted')
    for name, data in files.items():
        row = manifest['files'][name]
        write_payload(rootfs if name.startswith('rootfs/') else core,
                      name.removeprefix('rootfs/'), data, row['mode'])
    # Preserve the current complete identity, including revoked/removed keys.
    preserve_identity(rootfs)
    atomic_json(path/'identity-hashes.json', credential_hashes())
    boot = core/'boot/alpine.tar.gz'
    with tarfile.open(boot, 'w:gz', format=tarfile.GNU_FORMAT) as archive: archive.add(rootfs, arcname='.')
    boot.chmod(0o600)
    atomic_json(core/'update-release.json', {'session': state['session'], 'version': manifest['version']})
    critical = {p.relative_to(core).as_posix(): sha(p) for p in core.rglob('*') if p.is_file() and not p.is_symlink()}
    atomic_json(path/'core-hashes.json', critical)
    shutil.rmtree(rootfs)
    os.sync()
    save_state(path, state, 'ready', version=manifest['version'], previous_release=current_session(), previous_boot_sha256=sha(ACTIVE/'boot/alpine.tar.gz'))
    return dict(state, **recording_status())


def current_session():
    marker = ACTIVE/'update-release.json'
    return json.loads(marker.read_text()).get('session') if marker.is_file() else None


def snapshot_settings(path):
    backup = path/'settings-backup'
    backup.mkdir(mode=0o700)
    # Called after clean shutdown. Recording media is deliberately outside it.
    shutil.copytree(STORE/'settings', backup/'settings', symlinks=True)
    shutil.copytree(STORE/'tvheadend/config', backup/'tvheadend-config', symlinks=True)


def apply(path, rollback=False):
    state = read_state(path)
    if environment() != state['device']: raise ValueError('Device identity changed')
    if rollback:
        if current_session() != state['session'] or state['phase'] not in ('awaiting-reboot', 'confirmed', 'activated', 'rollback-queued'):
            raise ValueError('This previous core is not the active release rollback target')
        if sha(path/'core/boot/alpine.tar.gz') != state['previous_boot_sha256']:
            raise ValueError('Previous boot image differs')
    else:
        if state['phase'] not in ('ready', 'apply-queued') or current_session() != state.get('previous_release') or sha(ACTIVE/'boot/alpine.tar.gz') != state['previous_boot_sha256']:
            raise ValueError('Installed core changed or update is not ready')
        for name, expected in json.loads((path/'core-hashes.json').read_text()).items():
            candidate = path/'core'/name
            if not candidate.resolve().is_relative_to((path/'core').resolve()) or sha(candidate) != expected:
                raise ValueError('Staged core differs')
        if credential_hashes() != json.loads((path/'identity-hashes.json').read_text()):
            raise ValueError('Device credentials changed since staging; stage a new update to retain them')
    require_idle()  # Always recheck on-device after the Windows confirmation.
    save_state(path, state, 'stopping', rollback=rollback)
    os.chdir('/')
    subprocess.run(['/usr/local/sbin/data-stop'], check=True)
    subprocess.run(['mount', '-t', 'xfs', '-o', 'rw,noatime', '/dev/sda1', str(DISK)], check=True)
    if rollback:
        refresh_rollback_identity(path)
        save_state(path, state, 'stopping', previous_boot_sha256=sha(path/'core/boot/alpine.tar.gz'))
    else: snapshot_settings(path)
    save_state(path, state, 'exchanging')
    exchange(ACTIVE, path/'core')
    sync_dir(DISK); sync_dir(path); os.sync()
    save_state(path, state, 'rolled-back' if rollback else 'activated')
    subprocess.run(['umount', str(DISK)], check=True)
    os.execv('/sbin/reboot', ['reboot', '-f'])


def status(path):
    state = read_state(path)
    if current_session() == state['session'] and state['phase'] in ('stopping', 'exchanging', 'activated', 'awaiting-reboot'):
        state['phase'] = 'awaiting-reboot'
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != state['boot_id']:
            try:
                api('serverinfo')
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:9993/api/view', timeout=5) as r:
                    view = json.loads(r.read(262144))
                if not isinstance(view.get('page'), str) or view['page'] == 'setup' or not isinstance(view.get('rows'), list):
                    raise ValueError('Phoenix UI is not ready or unexpectedly reset')
                if Path('/data/native-frontend/version.txt').read_text().strip() != state['version']:
                    raise ValueError('Running version differs from the staged update')
                display = Path('/run/nexus-display-status').read_text().strip()
                if display not in ('SERVER_RUNNING', 'DISPLAY_TEST_PASSED', 'DISPLAY_TEST_FAILED'):
                    raise ValueError('Display startup has not completed')
                pid = int(Path('/run/nexus-tuner-manager.pid').read_text())
                os.kill(pid, 0)
                save_state(path, state, 'confirmed')
            except (OSError, ValueError): state['health'] = 'Waiting for Phoenix UI and Tvheadend'
    return state


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['init', 'uploaded', 'stage', 'recordings', 'launch', 'rollback', 'status', 'stage-worker', 'apply-worker', 'rollback-worker'])
    parser.add_argument('--session', required=True)
    parser.add_argument('--sha256')
    parser.add_argument('--allow-unsigned', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    device = environment()
    path = txn_path(args.session)
    # Serialize stages/activation globally, including detached workers.
    with LOCK.open('a') as lock:
        if args.operation not in ('status', 'recordings'):
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if args.operation.endswith('-worker') else fcntl.LOCK_NB))
        if args.operation == 'init':
            if not re.fullmatch('[0-9a-f]{64}', args.sha256 or ''): raise ValueError('Missing package checksum')
            if path.exists(): raise ValueError('Update session already exists; check its status')
            path.mkdir(mode=0o700)
            state = {'session': args.session, 'device': device, 'package_sha256': args.sha256, 'allow_unsigned': args.allow_unsigned,
                     'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
            save_state(path, state, 'uploading')
            result = state
        else:
            state = read_state(path)
            if state['device'] != device: raise ValueError('Update belongs to another device')
            if args.operation == 'uploaded':
                if state['phase'] != 'uploading' or sha(path/'package.part') != state['package_sha256']:
                    raise ValueError('Upload incomplete or checksum differs')
                (path/'package.part').rename(path/'package.tar.gz')
                save_state(path, state, 'uploaded'); result = state
            elif args.operation == 'stage-worker': result = stage(path)
            elif args.operation == 'stage':
                if state['phase'] != 'uploaded': raise ValueError('Update is not uploaded')
                save_state(path, state, 'stage-queued')
                with (LOG/('phoenix-update-'+args.session+'.log')).open('ab') as log:
                    subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), 'stage-worker', '--session', args.session],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd='/', start_new_session=True)
                result = dict(state, phase='staging', worker_started=True)
            elif args.operation == 'recordings': result = dict(status(path), **recording_status())
            elif args.operation == 'status': result = status(path)
            elif args.operation in ('apply-worker', 'rollback-worker'):
                apply(path, args.operation == 'rollback-worker'); return
            else:
                require_idle()
                expected = 'ready' if args.operation == 'launch' else None
                if expected and state['phase'] != expected: raise ValueError('Update is not ready')
                if not expected and (state['phase'] not in ('awaiting-reboot', 'confirmed', 'activated') or current_session() != state['session']):
                    raise ValueError('Rollback is not available for this session')
                save_state(path, state, 'apply-queued' if expected else 'rollback-queued')
                operation = 'apply-worker' if args.operation == 'launch' else 'rollback-worker'
                # The worker must hold no file descriptor on the HDD at unmount.
                with (LOG/('phoenix-update-'+args.session+'.log')).open('ab') as log:
                    subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), operation, '--session', args.session],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd='/', start_new_session=True)
                result = dict(state, worker_started=True)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        # Keep failure evidence across reboot without holding an HDD log open.
        try:
            if any(name in sys.argv for name in ('stage-worker', 'apply-worker', 'rollback-worker')):
                session = sys.argv[sys.argv.index('--session')+1]
                failed_path = txn_path(session)
                failed = read_state(failed_path)
                atomic_json(failed_path/'state.json', dict(failed, failure=type(error).__name__+': '+str(error)))
        except Exception: pass
        print(json.dumps({'error': type(error).__name__+': '+str(error)}), flush=True)
        sys.exit(1)
