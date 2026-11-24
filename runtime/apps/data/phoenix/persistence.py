#!/usr/bin/env python3
"""Keep user state outside the replaceable core. Initialize defaults only once."""
import argparse
import json
import os
from pathlib import Path
import shutil
import time

DISK = Path('/mnt/recording-disk')
STORE_NAME = 'phoenix-pvr'
LEGACY_STORE_NAME = 'alpine-pvr'
MARKER = 'ALPINE_RECORDING_DISK_V1\n'
LOGO_FOLDER = 'australian-tv-network-logo-icons-master'
ICON_TEMPLATE = 'file:///mnt/recording-disk/network-icons/'+LOGO_FOLDER+'/colour/%U.png'
SUPPORTED_PROFILES = {'profile-mpegts','profile-htsp'}


def storage_root(disk=DISK):
    disk=Path(disk)
    current=disk/STORE_NAME
    if current.exists(): return current
    legacy=disk/LEGACY_STORE_NAME
    return legacy if legacy.exists() else current


def identity_path(data, disk=DISK):
    saved=Path(disk)/STORE_NAME/'settings/frontend.json'
    return saved if saved.is_file() else Path(data)/'native-frontend/config.json'


def atomic_json(path, value, mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.new')
    tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.chmod(mode);tmp.replace(path)


def rewrite_paths(value, disk=DISK):
    old=str(Path(disk)/LEGACY_STORE_NAME);new=str(Path(disk)/STORE_NAME)
    if isinstance(value,str): return value.replace(old,new)
    if isinstance(value,list): return [rewrite_paths(v,disk) for v in value]
    if isinstance(value,dict): return {k:rewrite_paths(v,disk) for k,v in value.items()}
    return value


def configure_offline(config, disk=DISK, defaults=True):
    """Run only with Tvheadend stopped. Preserve all unrelated user settings."""
    config=Path(config);disk=Path(disk)
    profiles=config/'profile'
    entries={p.name:json.loads(p.read_text()) for p in profiles.iterdir() if p.is_file()}
    keep={uuid:row for uuid,row in entries.items() if row.get('class') in SUPPORTED_PROFILES}
    passes=[uuid for uuid,row in keep.items() if row.get('class')=='profile-mpegts' and row.get('name')=='pass']
    if not passes: raise ValueError('The required pass streaming profile is missing')
    passthrough=passes[0];removed=set(entries)-set(keep)
    for path in config.rglob('*'):
        if not path.is_file() or path.is_symlink(): continue
        try: original=json.loads(path.read_text())
        except (UnicodeError,ValueError): continue
        value=rewrite_paths(original,disk)
        if isinstance(value,dict):
            for field in ('profile','profiles'):
                reference=value.get(field)
                if isinstance(reference,str) and reference in removed:
                    value[field]=passthrough
                elif isinstance(reference,list):
                    rewritten=[]
                    for entry in reference:
                        entry=passthrough if isinstance(entry,str) and entry in removed else entry
                        if entry not in rewritten: rewritten.append(entry)
                    value[field]=rewritten
        if value!=original: atomic_json(path,value)
    for uuid in removed: (profiles/uuid).unlink()
    # Packaged legacy recipes otherwise recreate shielded default profiles.
    atomic_json(config/'profiles',{})
    atomic_json(config/'transcoder/profiles',{})
    if defaults:
        base=config/'config'
        current=json.loads(base.read_text()) if base.exists() else {}
        current.update(chiconpath=ICON_TEMPLATE,chiconscheme=1)
        atomic_json(base,current)
        imagecache=config/'imagecache/config'
        cache=json.loads(imagecache.read_text()) if imagecache.exists() else {}
        cache['enabled']=True
        cache.setdefault('ignore_sslcert',False)
        atomic_json(imagecache,cache)
        for path in (config/'dvr/config').glob('*'):
            if not path.is_file(): continue
            value=json.loads(path.read_text())
            if not value.get('name') and not value.get('storage'):
                value['storage']=str(disk/STORE_NAME/'recordings')
            atomic_json(path,value)
    return {'removed_profiles':len(removed),'preserved_profiles':len(keep)}


def initialize(data, disk=DISK, bootstrap=None):
    data=Path(data);disk=Path(disk);bootstrap=Path(bootstrap) if bootstrap else data
    (data/'tvheadend').mkdir(parents=True,exist_ok=True)
    store=disk/STORE_NAME;legacy=disk/LEGACY_STORE_NAME
    if store.exists() and legacy.exists(): raise ValueError('Both storage directories exist; inspect before migrating')
    if store.is_symlink() or legacy.is_symlink(): raise ValueError('Storage directory must not be a symlink')
    if not store.exists() and legacy.exists():
        if (legacy/'storage-id.txt').read_text()!=MARKER: raise ValueError('Legacy storage identity differs')
        legacy.rename(store)
    store.mkdir(parents=True,exist_ok=True)
    marker=store/'storage-id.txt'
    if marker.exists() and marker.read_text()!=MARKER: raise ValueError('Storage identity differs')
    if not marker.exists(): marker.write_text(MARKER)
    for name in ('recordings','index','timeshift','settings','settings/tuning','tvheadend','diagnostics','diagnostics/tvh'):
        target=store/name
        if target.is_symlink(): raise ValueError('Persistent directory is a symlink: '+name)
        target.mkdir(parents=True,exist_ok=True)
    target=store/'tvheadend/config'
    if not target.exists():
        source=data/'tvheadend/config'
        if not (source/'accesscontrol').is_dir(): source=bootstrap/'tvheadend/config'
        if not (source/'accesscontrol').is_dir(): raise ValueError('No initialized backend configuration')
        temporary=target.with_name('config.initializing')
        if temporary.exists(): raise ValueError('Incomplete initial settings copy; inspect before retry')
        def volatile(folder,names):
            return [name for name in names if not (Path(folder,name).is_file() or Path(folder,name).is_dir() or Path(folder,name).is_symlink())]
        shutil.copytree(source,temporary,symlinks=True,ignore=volatile)
        configure_offline(temporary,disk)
        temporary.rename(target)
    settings=store/'settings'
    if not (settings/'.initialized').exists():
        identity=identity_path(data,disk)
        if not identity.is_file(): identity=bootstrap/'native-frontend/config.json'
        atomic_json(settings/'frontend.json',json.loads(identity.read_text()))
        # Read existing installation state before falling back to factory defaults.
        state=data/'phoenix' if (data/'phoenix').is_dir() else bootstrap/'phoenix'
        for name in ('band-plan','timezone','setup-status.json','setup-accepted','firstboot-pending','firstboot.log'):
            source=state/name
            if source.is_file(): shutil.copy2(source,settings/name)
        for name in ('positions.json','volume-keys'):
            source=data/'native-frontend'/name
            if source.is_file(): shutil.copy2(source,settings/name)
        tuning=data/'nexus-loader-test-v1/opt/tuner-manager'
        for name in ('muxes.txt','muxes.txt.ready'):
            if (tuning/name).is_file(): shutil.copy2(tuning/name,settings/'tuning'/name)
        disabled=data/'tvheadend/disable-hdd-autostart'
        if disabled.is_file(): shutil.copy2(disabled,settings/'disable-tvh-autostart')
        (settings/'.initialized').write_text('PHOENIX_PERSISTENT_SETTINGS_V1\n')
    # Only tuning data is restored into the vendor chroot; code is always new.
    runtime=data/'nexus-loader-test-v1/opt/tuner-manager'
    runtime.mkdir(parents=True,exist_ok=True)
    for name in ('muxes.txt','muxes.txt.ready'):
        source=settings/'tuning'/name
        if source.is_file(): shutil.copy2(source,runtime/name)
        elif name.endswith('.ready'): (runtime/name).unlink(missing_ok=True)
    logos=disk/'network-icons'/LOGO_FOLDER
    if not (logos/'colour').is_dir():
        source=bootstrap/'phoenix/bootstrap-icons'/LOGO_FOLDER
        if not (source/'colour').is_dir(): raise ValueError('Channel icons are not installed')
        logos.parent.mkdir(parents=True,exist_ok=True)
        temporary=logos.with_name(logos.name+'.initializing')
        shutil.copytree(source,temporary);temporary.rename(logos)
    return store


def restore_setup(disk, backup):
    """Restore the two archived settings directories after an ordinary cutover error."""
    store=Path(disk)/STORE_NAME;backup=Path(backup)
    for saved,target in ((backup/'settings',store/'settings'),
                         (backup/'tvheadend-config',store/'tvheadend/config')):
        if not saved.exists(): continue
        if target.exists(): target.rename(backup/('failed-new-'+saved.name))
        saved.rename(target)


def fresh_setup(data, disk, backup):
    """Called only after clean shutdown. Retain recordings and archive old settings."""
    data=Path(data);disk=Path(disk);backup=Path(backup);store=disk/STORE_NAME
    if backup.parent.resolve()!=(disk/'phoenix-settings-backups').resolve():
        raise ValueError('Settings backup must be in the dedicated HDD backup directory')
    if backup.exists() or backup.is_symlink(): raise ValueError('Settings backup already exists')
    if not (data/'phoenix/firstboot-pending').is_file(): raise ValueError('Fresh setup defaults are missing')
    if not (data/'tvheadend/config/accesscontrol').is_dir(): raise ValueError('Fresh backend defaults are missing')
    identity=json.loads((data/'native-frontend/config.json').read_text())
    if not identity.get('dvr_config_uuid'): raise ValueError('Fresh DVR identity is missing')
    for target in (store/'settings',store/'tvheadend/config'):
        if not target.is_dir() or target.is_symlink(): raise ValueError('Existing settings layout differs')
    backup.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if backup.parent.is_symlink(): raise ValueError('Settings backup directory is a symlink')
    backup.mkdir(mode=0o700)
    try:
        (store/'settings').rename(backup/'settings')
        (store/'tvheadend/config').rename(backup/'tvheadend-config')
        initialize(data,disk)
        # Keep completed recordings visible, detached from obsolete channel/schedule IDs.
        # INT32_MAX is Tvheadend DVR_RET_REM_FOREVER: never expire imported media.
        retained=0
        for old in (backup/'tvheadend-config/dvr/log').glob('*'):
            if not old.is_file() or old.is_symlink(): continue
            try: value=json.loads(old.read_text())
            except (ValueError,UnicodeError): continue
            if not isinstance(value,dict) or value.get('stop',0)>time.time(): continue
            paths=[value.get('filename')]+[v.get('filename') for v in value.get('files',[]) if isinstance(v,dict)]
            existing=[]
            for filename in paths:
                if not isinstance(filename,str): continue
                file=Path(filename)
                if file.is_file() and file.resolve().is_relative_to((store/'recordings').resolve()):existing.append(file)
            if not existing: continue
            value.update(config_name=identity['dvr_config_uuid'],channel='',autorec='',timerec='',
                         parent='',child='',retention=2147483647,removal=2147483647,noresched=True,norerecord=True)
            atomic_json(store/'tvheadend/config/dvr/log'/old.name,value);retained+=1
        for name in ('positions.json','volume-keys'):
            if (backup/'settings'/name).is_file():shutil.copy2(backup/'settings'/name,store/'settings'/name)
        atomic_json(backup/'backup.json',{'mode':'fresh-setup','completed_catalogue_entries_retained':retained,
                                        'recording_files_untouched':True})
        return retained
    except BaseException:
        restore_setup(disk,backup)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('/data'))
    parser.add_argument('--disk',type=Path,default=DISK)
    args=parser.parse_args()
    mounts=Path('/proc/mounts').read_text().splitlines()
    if not any(v.split()[:3]==['/dev/sda1',str(args.disk),'xfs'] for v in mounts):
        raise ValueError('The verified HDD must be mounted')
    print('PERSISTENT_SETTINGS_READY',initialize(args.data,args.disk))

if __name__=='__main__':main()
