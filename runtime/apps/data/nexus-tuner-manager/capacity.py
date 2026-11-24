#!/usr/bin/env python3
"""Check discovered multiplexes before Tvheadend maps them; no media is retained."""
import argparse
import json
import time
import urllib.request
from pathlib import Path

MANAGER = 'http://127.0.0.1:9990'
MUXES = Path('/data/nexus-loader-test-v1/opt/tuner-manager/muxes.txt')
PERSISTENT_MUXES = Path('/mnt/recording-disk/phoenix-pvr/settings/tuning/muxes.txt')


def load_muxes(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        values = line.split()
        frequency, bandwidth = map(int, values[:2])
        if not 45000000 <= frequency <= 870000000 or frequency % 1000 or bandwidth not in (6, 7, 8):
            raise ValueError('Invalid discovered multiplex')
        if (frequency, bandwidth) not in rows:
            rows.append((frequency, bandwidth))
    if not rows:
        raise ValueError('No discovered multiplexes to validate')
    return rows


def packet_counts(data):
    if not data or len(data) % 188:
        raise ValueError('Empty or incomplete transport stream')
    pids = set()
    for at in range(0, len(data), 188):
        if data[at] != 0x47 or data[at + 1] & 0x80:
            raise ValueError('Transport stream sync/error flag')
        pids.add(((data[at + 1] & 31) << 8) | data[at + 2])
    return len(pids)


def check_one(frequency, bandwidth, seconds=3, opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = bytearray()
    deadline = time.monotonic() + seconds
    with opener.open('%s/mux/%d/%d' % (MANAGER, frequency, bandwidth), timeout=8) as stream:
        while time.monotonic() < deadline:
            block = stream.read(1316)
            if not block:
                break
            data.extend(block)
            if len(data) > 32 * 1024 * 1024:
                break
        # Inspect the slot before closing its stream, not after it has been released.
        with opener.open(MANAGER + '/status', timeout=5) as response:
            status = json.loads(response.read(16384))
        slots = [s for s in status.get('slots', []) if s.get('mode') == 'mux'
                 and s.get('frequency') == frequency and s.get('bandwidth') == bandwidth
                 and s.get('state') == 'streaming']
        if len(slots) != 1 or slots[0].get('pid_limit') != 0:
            raise ValueError('Multiplex did not pass PID capacity validation')
        pids = packet_counts(data)
    return {'frequency': frequency, 'bandwidth': bandwidth, 'bytes': len(data),
            'observed_pids': pids, 'pid_limit': 0}


def crc32_mpeg(data):
    value = 0xffffffff
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value


def validate(path=MUXES, seconds=3):
    path = Path(path)
    snapshot = path.read_bytes()
    results = []
    rows = load_muxes(path)
    for index, (frequency, bandwidth) in enumerate(rows):
        print('SETUP_PROGRESS '+json.dumps({'stage':'capacity', 'current':index, 'total':len(rows),
              'message':'Checking multiplex %.3f MHz (%d of %d)' % (frequency/1000000,index+1,len(rows))}), flush=True)
        result = check_one(frequency, bandwidth, seconds)
        results.append(result)
        print('MUX_CAPACITY_OK', json.dumps(result), flush=True)
        print('SETUP_PROGRESS '+json.dumps({'stage':'capacity','current':index+1,'total':len(rows)}), flush=True)
    if path.read_bytes() != snapshot:
        raise ValueError('Multiplex list changed during capacity validation')
    ready = path.with_suffix(path.suffix + '.ready')
    tmp = ready.with_suffix('.new')
    tmp.write_text('%08x\n' % crc32_mpeg(snapshot))
    tmp.replace(ready)
    if path == MUXES:
        PERSISTENT_MUXES.parent.mkdir(parents=True,exist_ok=True)
        saved=PERSISTENT_MUXES.with_suffix('.new');saved.write_bytes(snapshot);saved.replace(PERSISTENT_MUXES)
        target=PERSISTENT_MUXES.with_suffix('.txt.ready')
        saved=target.with_suffix('.new');saved.write_text('%08x\n' % crc32_mpeg(snapshot));saved.replace(target)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--muxes', type=Path, default=MUXES)
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args()
    if not 0 < args.seconds <= 30:
        parser.error('--seconds must be in (0, 30]')
    validate(args.muxes, args.seconds)
