#!/usr/bin/env python3
"""Find which DVB-T multiplexes this box can receive and write the tuner manager's
multiplex list. Tvheadend then gets every channel from /muxes.m3u by scanning
the multiplexes, so no region's channel list is built into the code.

    python3 sweep.py [--plan au|eu] [--extra HZ ...] [--write]

The sweep asks the manager to tune each candidate frequency (GET /lock/<hz>/<bw>),
using one of the three Tvheadend tuners, so run it before Tvheadend starts using the
tuners (first boot does this). Each probe takes about 1 to 3 s. Australian
multiplexes sit at the channel centre or 125 kHz above or below it, so all three are
tried until one locks. Without --write it only prints what it found.
"""
import argparse, json, sys, time, urllib.error, urllib.request
from pathlib import Path

MANAGER = 'http://127.0.0.1:9990'
MUXES = Path('/data/nexus-loader-test-v1/opt/tuner-manager/muxes.txt')
PERSISTENT_MUXES = Path('/mnt/recording-disk/phoenix-pvr/settings/tuning/muxes.txt')


def plan_au():
    """Australian DVB-T: 7 MHz, VHF 6-12 (with 9A) and UHF 28-51."""
    vhf = [('6', 177500000), ('7', 184500000), ('8', 191500000), ('9', 198500000), ('9A', 205625000),
           ('10', 212500000), ('11', 219500000), ('12', 226500000)]
    uhf = [(str(n), 529500000 + 7000000 * (n - 28)) for n in range(28, 52)]
    return [('VHF ' + c, f, 7) for c, f in vhf] + [('UHF ' + c, f, 7) for c, f in uhf]


def plan_eu():
    """Europe DVB-T: VHF 5-12 at 7 MHz, UHF 21-60 at 8 MHz."""
    vhf = [('VHF %d' % n, 177500000 + 7000000 * (n - 5), 7) for n in range(5, 13)]
    uhf = [('UHF %d' % n, 474000000 + 8000000 * (n - 21), 8) for n in range(21, 61)]
    return vhf + uhf


PLANS = {'au': (plan_au, (0, 125000, -125000)), 'eu': (plan_eu, (0, 166000, -166000))}


def probe(freq, bw, timeout=20):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(5):  # 503 = all three Tvheadend tuners busy; wait and retry
        try:
            with opener.open('%s/lock/%d/%d' % (MANAGER, freq, bw), timeout=timeout) as r:
                return bool(json.loads(r.read(256)).get('locked'))
        except urllib.error.HTTPError as e:
            if e.code != 503:
                raise
            time.sleep(5)
    raise SystemExit('Tuner manager stayed busy; stop Tvheadend recordings and retry')


def sweep(candidates, offsets, lock=probe, log=print):
    found = []
    for index, (name, centre, bw) in enumerate(candidates):
        log('SETUP_PROGRESS '+json.dumps({'stage':'sweep','current':index,'total':len(candidates),'message':'Scanning '+name+' — '+str(len(found))+' multiplexes found'}))
        for off in offsets:
            f = centre + off
            log('SETUP_PROGRESS '+json.dumps({'stage':'sweep','current':index,'total':len(candidates),
                'message':'Scanning %s at %.3f MHz — %d multiplexes found' % (name,f/1000000,len(found))}))
            if lock(f, bw):
                found.append((f, bw, name))
                log('SWEEP_LOCKED %s %d' % (name, f))
                break
        else:
            log('SWEEP_NONE %s' % name)
        log('SETUP_PROGRESS '+json.dumps({'stage':'sweep','current':index+1,'total':len(candidates),'found':len(found),
             'muxes':['%s — %.3f MHz' % (n,f/1000000) for f,b,n in found],
             'message':name+' checked; '+str(len(found))+' multiplexes found'}))
    return found


def render(found):
    lines = ['# phoenix sweep %s: <frequency_hz> <bandwidth_mhz> <name>' % time.strftime('%Y-%m-%d %H:%M')]
    lines += ['%d %d %s %d.%03d MHz' % (f, bw, name, f // 1000000, f // 1000 % 1000) for f, bw, name in found]
    return '\n'.join(lines) + '\n'


def write_muxes(found, path=MUXES):
    if not found:
        raise ValueError('Refusing to replace the multiplex list with an empty scan')
    for frequency, bandwidth, name in found:
        if not 45000000 <= frequency <= 870000000 or frequency % 1000 or bandwidth not in (6, 7, 8):
            raise ValueError('Invalid scan result')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.new')
    tmp.write_text(render(found))
    path.with_suffix(path.suffix + '.ready').unlink(missing_ok=True)
    tmp.replace(path)
    if path == MUXES:
        PERSISTENT_MUXES.parent.mkdir(parents=True,exist_ok=True)
        PERSISTENT_MUXES.with_suffix('.txt.ready').unlink(missing_ok=True)
        saved=PERSISTENT_MUXES.with_suffix('.new')
        saved.write_text(render(found));saved.replace(PERSISTENT_MUXES)


def self_test():
    au = plan_au()
    assert len(au) == 32 and ('VHF 8', 191500000, 7) in au and ('UHF 51', 690500000, 7) in au
    assert len(plan_eu()) == 48 and ('UHF 21', 474000000, 8) in plan_eu()
    on = {191625000, 177500000, 536625000}
    found = sweep(au, PLANS['au'][1], lock=lambda f, bw: f in on, log=lambda *_: None)
    assert [f for f, _, _ in found] == [177500000, 191625000, 536625000], found
    text = render(found)
    assert '191625000 7 VHF 8 191.625 MHz\n' in text and text.startswith('# phoenix sweep')
    print('SWEEP_SELF_TEST_PASSED')


def main():
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('--plan', choices=sorted(PLANS), default='au')
    a.add_argument('--extra', type=int, nargs='*', default=[], help='more frequencies (Hz) to try at 7 MHz')
    a.add_argument('--output', type=Path, default=MUXES)
    a.add_argument('--extra-bandwidth', type=int, choices=(6, 7, 8), default=7)
    a.add_argument('--write', action='store_true', help='write %s' % MUXES)
    a.add_argument('--self-test', action='store_true')
    args = a.parse_args()
    if args.self_test:
        return self_test()
    make, offsets = PLANS[args.plan]
    candidates = make() + [('Extra', f, args.extra_bandwidth) for f in args.extra]
    found = sweep(candidates, offsets)
    print('SWEEP_FOUND %d' % len(found))
    if not found:
        sys.exit('No multiplex locked: check the aerial before writing an empty list')
    if args.write:
        write_muxes(found, args.output)
        print('SWEEP_WRITTEN', args.output)
    else:
        sys.stdout.write(render(found))


if __name__ == '__main__':
    main()
