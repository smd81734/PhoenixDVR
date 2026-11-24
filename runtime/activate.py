#!/usr/bin/env python3
"""Check or explicitly activate a prepared replacement for the DVR; never format disks."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

DISK = Path('/mnt/recording-disk')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda: stream.read(1048576), b''): h.update(b)
    return h.hexdigest()


def verify(candidate):
    candidate = Path(candidate).resolve()
    if candidate == DISK or not candidate.is_relative_to(DISK) or candidate.is_relative_to(DISK/'alpine-system'):
        raise ValueError('Candidate is not separate from the installed system')
    metadata = json.loads((candidate/'candidate.json').read_text())
    if metadata['state'] != 'ready': raise ValueError('Candidate is not ready')
    required={'boot/alpine.tar.gz','boot/selector.sh','boot/alpine-init','modules/nexus.ko'}
    if not required <= metadata['files'].keys(): raise ValueError('Missing critical boot manifest entries')
    system = candidate/'alpine-system'
    if system.is_symlink(): raise ValueError('Candidate system must not be a symlink')
    for relative, expected in metadata['files'].items():
        path = (system/relative).resolve()
        if not path.is_relative_to(system) or sha(path) != expected:
            raise ValueError('Candidate differs: ' + relative)
    model = ''.join(Path('/sys/class/block/sda/device/model').read_text().split())
    if model != metadata['disk_model'] or int(Path('/sys/class/block/sda/size').read_text()) != metadata['disk_sectors'] \
       or int(Path('/sys/class/block/sda1/size').read_text()) != metadata['part_sectors']:
        raise ValueError('Candidate was prepared for a different disk')
    return system, metadata


def persistence_module():
    path=Path(__file__).with_name('persistence.py')
    if not path.is_file(): path=Path(__file__).parent/'project/source/provision/persistence.py'
    spec=importlib.util.spec_from_file_location('phoenix_persistence',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def check_recordings():
    config = json.loads(persistence_module().identity_path(Path('/data'),DISK).read_text())
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, config['url'], config['username'], config['password'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPDigestAuthHandler(manager))
    with opener.open(config['url'].rstrip('/')+'/api/dvr/entry/grid?start=0&limit=99999', timeout=10) as response:
        rows = json.loads(response.read()).get('entries', [])
    if any(str(row.get('sched_status', row.get('state', ''))).lower().startswith('recording') for row in rows):
        raise ValueError('Recording active; activation was not started')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--reboot', action='store_true')
    args = parser.parse_args()
    if args.reboot and not args.apply: parser.error('--reboot requires --apply')
    if os.geteuid() != 0: raise ValueError('Run as root')
    args.candidate = args.candidate.resolve()
    system, metadata = verify(args.candidate)
    suffix = re.sub('[^A-Za-z0-9_.-]', '_', metadata['package_id'])
    active = DISK/'alpine-system'
    backup = DISK/('alpine-system.before-'+suffix)
    if backup.exists() or active.is_symlink() or not (active/'data').is_dir():
        raise ValueError('Unexpected installed-system or backup layout')
    fresh = metadata.get('fresh_setup',False)
    if not isinstance(fresh,bool): raise ValueError('Invalid fresh-setup mode')
    settings_backup = DISK/'phoenix-settings-backups'/suffix
    if fresh and settings_backup.exists(): raise ValueError('Fresh settings backup already exists')
    if fresh and (not (system/'data/phoenix/firstboot-pending').is_file() or not (system/'data/tvheadend/config/accesscontrol').is_dir()):
        raise ValueError('Fresh setup payload is missing')
    check_recordings()
    print('VERIFIED_REPLACEMENT', system)
    print('OLD_SYSTEM_BACKUP', backup)
    print('RECORDING_FILES_RETAINED')
    if fresh: print('FRESH_SETUP_SETTINGS_BACKUP',settings_backup)
    else: print('Existing Tvheadend settings stay in phoenix-pvr.')
    if not args.apply:
        print('Check only. No services or files changed. Add --apply for cutover.')
        return
    os.chdir('/')
    persistence = persistence_module()
    original_store = persistence.storage_root(DISK)
    marker = original_store/'storage-id.txt'
    previous_marker = marker.read_bytes()
    # This must succeed without forcing any unmount. Parent shells must be off the HDD.
    subprocess.run(['/usr/local/sbin/data-stop'], check=True)
    subprocess.run(['mount','-t','xfs','-o','rw,noatime','/dev/sda1',str(DISK)], check=True)
    moved_old = moved_new = reset_settings = False
    try:
        persistence.initialize(active/'data',DISK,bootstrap=system/'data')
        if fresh:
            persistence.fresh_setup(system/'data',DISK,settings_backup)
            reset_settings = True
        marker = DISK/'phoenix-pvr/storage-id.txt'
        active.rename(backup); moved_old = True
        (backup/'recording-storage-id.before').write_bytes(previous_marker)
        os.sync()
        system.rename(active); moved_new = True
        pending_marker = marker.with_name('.storage-id.'+suffix)
        pending_marker.write_text('ALPINE_RECORDING_DISK_V1\n')
        pending_marker.replace(marker)
        (args.candidate/'activation.json').write_text(json.dumps({'active':str(active),'backup':str(backup),'fresh_setup':fresh,'settings_backup':str(settings_backup) if fresh else None},indent=2)+'\n')
        os.sync()
    except BaseException:
        if reset_settings: persistence.restore_setup(DISK,settings_backup)
        if moved_new: active.rename(system)
        if moved_old: backup.rename(active)
        if original_store != DISK/'phoenix-pvr' and not original_store.exists() and (DISK/'phoenix-pvr').exists():
            (DISK/'phoenix-pvr').rename(original_store)
        (original_store/'storage-id.txt').write_bytes(previous_marker); os.sync()
        raise
    subprocess.run(['umount',str(DISK)], check=True)
    print('REPLACEMENT_ACTIVATED. Disk unmounted; reboot is required before using tuners.')
    if args.reboot:
        os.execv('/sbin/reboot', ['reboot','-f'])
    print('Reboot with: /sbin/reboot -f')


if __name__ == '__main__':
    main()
