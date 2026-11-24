#!/usr/bin/env python3
"""Render our clean-install layout from probed device values; never access disks."""
import argparse
from pathlib import Path
import re
import shlex
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def render(output, model, disk_sectors, part_sectors, hostname, band_plan, timezone):
    if not 0 < part_sectors <= disk_sectors:
        raise ValueError('Invalid probed disk/partition sizes')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', hostname):
        raise ValueError('Invalid hostname')
    if band_plan not in ('au', 'eu'):
        raise ValueError('Unsupported band plan')
    if not model.strip():
        raise ValueError('Missing probed disk model')
    ZoneInfo(timezone)
    model = ''.join(model.split())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    data = 'hdd/alpine-system/data/'
    files = {
        'rootfs/etc/hostname': hostname+'\n',
        'rootfs/etc/phoenix/disk.conf': 'DISK_MODEL=%s\nDISK_SECTORS=%d\nPART_SECTORS=%d\n' % (
            shlex.quote(model), disk_sectors, part_sectors),
        'hdd/alpine-system/storage-id.txt': 'ALPINE_HDD_SYSTEM_V1\n',
        'hdd/phoenix-pvr/storage-id.txt': 'ALPINE_RECORDING_DISK_V1\n',
        data+'persistence-test.txt': 'ALPINE_PERSISTENCE_V1\n',
        data+'nexus-loader-test-v1/runtime-id.txt': 'NEXUS_RUNTIME_V1\n',
        data+'phoenix/band-plan': band_plan+'\n',
        data+'phoenix/timezone': timezone+'\n',
        data+'phoenix/firstboot-pending': '',
        data+'nexus-tuner-manager/enable-autostart': '',
        data+'native-frontend/enable-html-autostart': '',
    }
    selector = (ROOT/'source/boot/hdd/selector.sh.in').read_text()
    selector = selector.replace('@DISK_SECTORS@', str(disk_sectors)).replace('@PART_SECTORS@', str(part_sectors))
    files['hdd/alpine-system/boot/selector.sh'] = selector
    for name in ('boot.txt', 'alpine-init'):
        files['hdd/alpine-system/boot/'+name] = (ROOT/'source/boot/hdd'/name).read_text()
    for name in ('boot-policy', 'storage-policy'):
        files['rootfs/etc/'+name] = (ROOT/'source/boot/rootfs/etc'/name).read_text()
    for relative, text in files.items():
        path = output/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disk-model', required=True)
    parser.add_argument('--disk-sectors', type=int, required=True)
    parser.add_argument('--part-sectors', type=int, required=True)
    parser.add_argument('--hostname', default='phoenix')
    parser.add_argument('--band-plan', choices=('au', 'eu'), required=True)
    parser.add_argument('--timezone', required=True)
    args = parser.parse_args()
    print('DEVICE_LAYOUT_RENDERED', render(args.output, args.disk_model, args.disk_sectors,
          args.part_sectors, args.hostname, args.band_plan, args.timezone))
