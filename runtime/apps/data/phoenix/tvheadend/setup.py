#!/usr/bin/env python3
"""Configure Tvheadend through its JSON API, so a fresh box needs no web-UI clicks
and no region-specific channel list.

Phases:

  seed       Run by the installer against a temporary Tvheadend started with
             `tvheadend -c CONFIG -C --noacl -b 127.0.0.1` (no tuners yet).
             Creates the admin login and access entry, enables the image cache,
             sets the DVR path, and adds the "NEXUS Multiplexes" IPTV automatic
             network (http://127.0.0.1:9990/muxes.m3u), capped at 3 streams because
             tuner 4 is reserved for live TV. The admin password comes from the
             PHOENIX_TVH_PASSWORD environment variable and is never printed.

  firstboot  Started explicitly from the welcome screen once the tuner manager is up.
             1. sweep.py finds the multiplexes this aerial receives -> muxes.txt
             2. the network reloades /muxes.m3u (one IPTV mux per multiplex)
             3. each mux's playlist "service name" is cleared, so channel names come
                from the broadcast (SDT), not from the mux name
             4. scan, wait until the service count settles, map all services
             5. apply logos from channel-icons.tsv by channel name
             Uses /mnt/recording-disk/phoenix-pvr/settings/frontend.json and removes
             /mnt/recording-disk/phoenix-pvr/settings/firstboot-pending on success.

  rescan     Steps 1-5 again (new transmitters, a move to another region).

STATUS: seed, Digest authentication and access isolation verified against the target build.\nRF scanning/service mapping still require the replacement boot test.
Every call logs its endpoint, so a failure says which step to do by hand.
"""
import argparse, importlib.util, json, os, re, signal, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

NETWORK_NAME = 'NEXUS Multiplexes'
PLAYLIST = 'http://127.0.0.1:9990/muxes.m3u'
RECORDINGS = '/mnt/recording-disk/phoenix-pvr/recordings'
PENDING = Path('/mnt/recording-disk/phoenix-pvr/settings/firstboot-pending')
ICONS = Path(__file__).with_name('channel-icons.tsv')
CAPACITY = Path('/data/nexus-tuner-manager/capacity.py')
SWEEP = Path('/data/nexus-tuner-manager/sweep.py')
MUXES = Path('/data/nexus-loader-test-v1/opt/tuner-manager/muxes.txt')


STATUS = Path('/mnt/recording-disk/phoenix-pvr/settings/setup-status.json')
PROGRESS = None

class Progress:
    def __init__(self, path=STATUS):
        self.path = Path(path)
        self.data = {'state':'running', 'stage':'configuring', 'pid':os.getpid(),
                     'current':0, 'total':0, 'muxes':[], 'channels':[], 'events':[]}

    def update(self, stage=None, **fields):
        if stage and stage != self.data['stage']:
            self.data.update(stage=stage, current=0, total=0)
        self.data.update(fields, updated=time.time())
        if self.data.get('state') in ('running','complete'):
            self.data.pop('error',None)
        message = fields.get('message')
        if message and (not self.data['events'] or message != self.data['events'][-1]):
            self.data['events'] = (self.data['events'] + [str(message)[:240]])[-40:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.new')
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)


def report(stage=None, **fields):
    if PROGRESS is not None:
        PROGRESS.update(stage, **fields)


def run_phase(path, *args):
    process = subprocess.Popen([sys.executable, '-I', '-u', str(path), *args],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in process.stdout:
            print(line.rstrip(), flush=True)
            if line.startswith('SETUP_PROGRESS '):
                event = json.loads(line[len('SETUP_PROGRESS '):])
                stage = event.pop('stage')
                report(stage, **event)
        if process.wait():
            raise RuntimeError(path.stem+' failed. Check the antenna connection and retry.')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def log(*parts):
    print('TVH_SETUP', *parts, flush=True)


class API:
    def __init__(self, base, username=None, password=None):
        self.base = base.rstrip('/')
        handlers = [urllib.request.ProxyHandler({})]
        if username:
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, self.base, username, password)
            handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))
        self.opener = urllib.request.build_opener(*handlers)

    def call(self, path, **params):
        data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                       for k, v in params.items()}).encode()
        with self.opener.open(self.base + '/api/' + path, data=data, timeout=30) as r:
            body = r.read()
        log('OK', path)
        return json.loads(body) if body.strip() else {}

    def grid(self, path, **params):
        return self.call(path, start=0, limit=99999, **params).get('entries', [])

    def wait(self, tries=60):
        for _ in range(tries):
            try:
                self.call('serverinfo')
                return
            except OSError:
                time.sleep(2)
        raise SystemExit('Tvheadend API did not answer')


def network(api):
    nets = [n for n in api.grid('mpegts/network/grid') if n.get('networkname') == NETWORK_NAME]
    return nets[0] if nets else None


def seed(api, username, password, allowed, auto_lan=False):
    api.wait()
    if not any(e.get('username') == username for e in api.grid('passwd/entry/grid')):
        api.call('passwd/entry/create', conf={'enabled': True, 'username': username, 'password': password})
    if not any(e.get('username') == username for e in api.grid('access/entry/grid')):
        api.call('access/entry/create', conf={
            'enabled': True, 'username': username, 'prefix': allowed, 'admin': True,
            'streaming': ['basic', 'advanced', 'htsp'], 'dvr': ['basic', 'htsp', 'all', 'all_rw', 'failed'],
            'webui': True, 'uilevel': 2, 'comment': 'phoenix automatic LAN' if auto_lan else 'phoenix installer'})
    # A freshly initialized server may contain the unrestricted wizard entry.
    for entry in api.grid('access/entry/grid'):
        if not entry.get('username') or entry.get('username') == '*':
            api.call('idnode/delete', uuid=entry['uuid'])
    api.call('config/save', node={'chiconpath':'file:///mnt/recording-disk/network-icons/australian-tv-network-logo-icons-master/colour/%U.png', 'chiconscheme':1})
    api.call('imagecache/config/save', node={'enabled': True, 'ignore_sslcert': False})
    for cfg in api.grid('dvr/config/grid'):
        if not cfg.get('name'):  # the default profile has an empty name
            api.call('idnode/save', node={'uuid': cfg['uuid'], 'storage': RECORDINGS,
                                          'storage-mfree': 1000, 'pathname': '$t$n.$x'})
    if not network(api):
        api.call('mpegts/network/create', **{'class': 'iptv_auto_network', 'conf': {
            'networkname': NETWORK_NAME, 'url': PLAYLIST, 'enabled':False, 'max_streams': 3, 'max_timeout': 30,
            'idlescan': False, 'scan_create': False, 'refetch_period': 60, 'channel_number': 0}})
    log('SEED_COMPLETE')


def muxes_of(api, net_uuid):
    return [m for m in api.grid('mpegts/mux/grid') if m.get('network_uuid') == net_uuid or m.get('network') == NETWORK_NAME]


def mux_key(mux):
    match = re.search(r'/mux/(\d+)(?:/([678]))?(?:\?.*)?$', mux.get('iptv_url', ''))
    return (int(match.group(1)), int(match.group(2) or 7)) if match else None


def refresh_playlist(api, net):
    expected = set()
    for line in MUXES.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            values = line.split()
            expected.add((int(values[0]), int(values[1])))
    if not expected:
        raise RuntimeError('No device-discovered multiplexes to load')
    # Changing the URL makes Tvheadend retrieve the playlist again; the manager ignores ?v=.
    api.call('idnode/save', node={'uuid': net['uuid'], 'url': '%s?v=%d' % (PLAYLIST, int(time.time()))})
    for _ in range(30):
        time.sleep(2)
        found = muxes_of(api, net['uuid'])
        if expected <= {mux_key(row) for row in found}:
            return [row for row in found if mux_key(row) in expected]
    raise SystemExit('Tvheadend did not load all scanned muxes from %s; check the sweep output' % PLAYLIST)


def discover_services(api, net_uuid, muxes):
    # Scan individually: a blanket network scan can strand muxes when all three
    # IPTV slots are initially occupied. Never treat a partial service list as success.
    wanted = {m['uuid'] for m in muxes}
    for index, mux in enumerate(muxes):
        name = mux.get('pnetwork_name') or mux.get('name') or mux.get('iptv_muxname') or 'Multiplex '+str(index+1)
        name = re.sub(r'^muxes\.m3u - ', '', name)
        for attempt in range(2):
            report('discovery', current=index, total=len(muxes),
                   message='Discovering channels: '+name+(' (retry)' if attempt else ''))
            api.call('idnode/save', node={'uuid':mux['uuid'], 'iptv_sname':'', 'scan_state':1})
            stable, previous, completed = 0, -1, False
            for _ in range(45):
                time.sleep(2)
                rows = muxes_of(api, net_uuid)
                row = next((m for m in rows if m['uuid']==mux['uuid']), {})
                services = [v for v in api.grid('mpegts/service/grid') if v.get('multiplex_uuid') in wanted]
                names = sorted({v.get('svcname') or v.get('name') for v in services if v.get('svcname') or v.get('name')})
                report('discovery', channels=names, service_count=len(services),
                       message='Discovering channels: '+name+' — '+str(len(names))+' services found')
                count = sum(v.get('multiplex_uuid')==mux['uuid'] for v in services)
                stable = stable+1 if count==previous and count else 0
                previous = count
                if count and stable>=2 and row.get('scan_state', 0)==0 and row.get('scan_result', 1)==1:
                    completed=True; break
            if completed: break
        else:
            raise RuntimeError('Could not finish channel discovery on '+name+'. Check the antenna and retry.')
        report('discovery', current=index+1, total=len(muxes))


def map_services(api, net_uuid):
    mux_ids = {m['uuid'] for m in muxes_of(api, net_uuid)}
    services = [s for s in api.grid('mpegts/service/grid') if s.get('multiplex_uuid') in mux_ids]
    services = [s for s in services if s.get('svcname') or s.get('name')]
    if not services:
        raise RuntimeError('No named services were available to map')
    names = {s['uuid']:s.get('svcname') or s.get('name') for s in services}
    report('mapping', current=0, total=len(services), message='Mapping channels…')
    before = api.call('service/mapper/status')
    started = False
    api.call('service/mapper/save', node={'services':list(names), 'check_availability':True,
              'encrypted':False, 'merge_same_name':False, 'type_tags':True,
              'provider_tags':False, 'network_tags':False})
    for tick in range(450):
        time.sleep(2)
        status = api.call('service/mapper/status')
        total = int(status.get('total', 0)); done = sum(int(status.get(k, 0)) for k in ('ok','fail','ignore'))
        channels = api.grid('channel/grid')
        ours = [c for c in channels if set(c.get('services', [])) & set(names)]
        label = names.get(status.get('active'), '')
        report('mapping', current=done, total=total, mapped_count=len(ours),
               channels=sorted(c.get('name', '') for c in ours),
               message='Mapping '+label if label else str(len(ours))+' channels mapped; '+str(max(0,total-done))+' remaining')
        # The mapper starts in Tvheadend's deferred settings-save callback.
        # A successful save response can precede the new queue by several seconds;
        # do not mistake zero counters or a previous run's counters for completion.
        started = started or status != before or bool(ours)
        if not started or (not total and not ours):
            report('mapping', message='Starting channel mapping…')
            if tick>=29: raise RuntimeError('Tvheadend did not start channel mapping. Check the setup log and retry.')
            continue
        if not status.get('active') and done>=total:
            if status.get('fail'):
                raise RuntimeError(str(status['fail'])+' services could not be mapped. Check the antenna and retry.')
            if ours: return len(ours)
            raise RuntimeError('No channels were mapped. Check the setup log and retry.')
    raise RuntimeError('Channel mapping did not finish. Please retry.')


def channel_service_name(channel, services):
    linked = [services[sid] for sid in channel.get('services', [])
              if sid in services and str(services[sid].get('svcname') or '').strip()]
    # An alias channel can have several services. Prefer its own logical number.
    for service in linked:
        try:
            if float(service.get('lcn')) == float(channel.get('number')):
                return service['svcname'].strip()
        except (TypeError, ValueError): pass
    return linked[0]['svcname'].strip() if linked else None


def set_channel_service_names(api, net_uuid):
    mux_ids = {m['uuid'] for m in muxes_of(api, net_uuid)}
    services = {s['uuid']:s for s in api.grid('mpegts/service/grid')
                if s.get('multiplex_uuid') in mux_ids}
    expected = {}
    changed = 0
    for channel in api.grid('channel/grid'):
        name = channel_service_name(channel, services)
        if name is None: continue
        expected[channel['uuid']] = name
        # The automatic name is the IPTV mux label and can change on reload.
        if channel.get('name') != name or channel.get('autoname', True):
            api.call('idnode/save', node={'uuid':channel['uuid'], 'autoname':False, 'name':name})
            changed += 1
    actual = {c['uuid']:c for c in api.grid('channel/grid')}
    if any(actual.get(uuid, {}).get('name') != name or actual.get(uuid, {}).get('autoname', True)
           for uuid, name in expected.items()):
        raise RuntimeError('Channel service names were not saved correctly')
    log('CHANNEL_NAMES_SET', changed)
    return expected


def apply_icons(api):
    if not ICONS.exists():
        log('NO_ICON_FILE', ICONS)
        return
    wanted = {}
    for line in ICONS.read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            name, url = line.split('\t', 1)
            wanted[name.strip().lower()] = url.strip()  # no leading spaces: Tvheadend fails on them
    done = 0
    for ch in api.grid('channel/grid'):
        url = wanted.get(ch.get('name', '').strip().lower())
        if url and ch.get('icon') != url:
            api.call('idnode/save', node={'uuid': ch['uuid'], 'icon': url})
            done += 1
    log('ICONS_SET', done)


def firstboot(api, plan):
    report('configuring', message='Configuring TV Headend…')
    api.wait()
    net = network(api)
    if not net:
        raise RuntimeError(NETWORK_NAME+' network missing; prepare the installation again.')
    # Keep old/background IPTV subscriptions away from the hardware sweep.
    api.call('idnode/save', node={'uuid':net['uuid'], 'enabled':False, 'scan_create':False,
                                 'max_streams':3, 'idlescan':False})
    report('sweep', message='Beginning multiplex scan…')
    run_phase(SWEEP, '--plan', plan, '--write')
    report('capacity', message='Checking multiplex capacity…')
    run_phase(CAPACITY)
    api.call('idnode/save', node={'uuid':net['uuid'], 'enabled':True})
    report('discovery', message='Loading discovered multiplexes…')
    muxes = refresh_playlist(api, net)
    log('MUXES', len(muxes))
    discover_services(api, net['uuid'], muxes)
    finish_setup(api, net)


def finish_setup(api, net):
    mapped = map_services(api, net['uuid'])
    log('MAPPED', mapped)
    report('mapping', message='Setting channel names from broadcast services…')
    set_channel_service_names(api, net['uuid'])
    report('icons', message='Applying channel icons…')
    apply_icons(api)
    # Persist success before removing the pending marker. A completion screen is
    # acknowledged separately by the user, including after a reboot.
    report('complete', state='complete', current=mapped, total=mapped,
           mapped_count=mapped, message='Configuration is complete !')
    PENDING.unlink(missing_ok=True)
    log('FIRSTBOOT_COMPLETE')


def resume_mapping(api):
    previous = json.loads(STATUS.read_text())
    if not PENDING.exists() or previous.get('state') != 'error':
        raise RuntimeError('No failed mapping checkpoint to resume')
    spec = importlib.util.spec_from_file_location('setup_capacity', CAPACITY)
    capacity = importlib.util.module_from_spec(spec); spec.loader.exec_module(capacity)
    expected = set(capacity.load_muxes(MUXES))
    signature = MUXES.with_suffix(MUXES.suffix+'.ready')
    if signature.read_text().strip() != '%08x' % capacity.crc32_mpeg(MUXES.read_bytes()):
        raise RuntimeError('Multiplex approval changed; run a new scan')
    net = network(api)
    if not net: raise RuntimeError('Setup network is missing')
    if PROGRESS is not None:
        PROGRESS.data.update(previous, state='running', pid=os.getpid())
    report('mapping', message='Resuming channel mapping…')
    # Disabled networks retain their objects but hide them from the API grids.
    api.call('idnode/save', node={'uuid':net['uuid'], 'enabled':True})
    muxes = [m for m in muxes_of(api, net['uuid']) if mux_key(m) in expected]
    services = api.grid('mpegts/service/grid')
    if {mux_key(m) for m in muxes} != expected or not all(m.get('scan_result')==1 and any(v.get('multiplex_uuid')==m['uuid'] for v in services) for m in muxes):
        raise RuntimeError('Channel discovery is incomplete; run a new scan')
    finish_setup(api, net)


def stop_setup(proc=Path('/proc'), lock=Path('/run/channel-setup.lock')):
    signalled = set()
    for _ in range(75):
        jobs=[]
        for file in proc.glob('[0-9]*/cmdline'):
            try:
                if b'/data/phoenix/tvheadend/setup.py' in file.read_bytes().split(bytes([0])) and int(file.parent.name)!=os.getpid():
                    jobs.append(int(file.parent.name))
            except (OSError, ValueError): pass
        if not jobs:
            if not lock.exists(): return
            try:
                pid=int((lock/'pid').read_text())
                os.kill(pid,0)
            except ProcessLookupError:
                (lock/'pid').unlink();lock.rmdir();return
            except (OSError, ValueError):
                raise RuntimeError('Setup lock cannot be verified; do not unmount storage')
        for pid in jobs:
            if pid in signalled: continue
            signalled.add(pid)
            try: os.kill(pid,signal.SIGTERM)
            except ProcessLookupError: pass
        time.sleep(0.2)
    raise RuntimeError('Setup did not stop; do not unmount storage')


def main():
    global PROGRESS
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=['seed', 'firstboot', 'rescan', 'resume-mapping', 'stop'])
    p.add_argument('--url', default='http://127.0.0.1:9981')
    p.add_argument('--username', default='admin')
    p.add_argument('--allowed', default='0.0.0.0/0', help='Tvheadend access prefix, e.g. 192.168.1.0/24')
    p.add_argument('--config', default='/mnt/recording-disk/phoenix-pvr/settings/frontend.json')
    p.add_argument('--plan', default=os.environ.get('PHOENIX_BAND_PLAN', 'au'), choices=('au', 'eu'), help='sweep band plan: au or eu')
    a = p.parse_args()
    if a.phase == 'stop':
        stop_setup(); return
    if a.phase == 'seed':
        password = os.environ.get('PHOENIX_TVH_PASSWORD') or sys.exit('PHOENIX_TVH_PASSWORD not set')
        seed(API(a.url), a.username, password, a.allowed)
    else:
        c = json.loads(Path(a.config).read_text())
        PROGRESS = Progress()
        def interrupted(*_):
            raise RuntimeError('Setup was interrupted. Press Retry to try again.')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        try:
            api = API(c['url'], c['username'], c['password'])
            if a.phase == 'resume-mapping': resume_mapping(api)
            else: firstboot(api, a.plan)
        except BaseException as exc:
            report(state='error', error=str(exc)[:240], message=str(exc)[:240])
            try:
                net = network(api)
                if net: api.call('idnode/save', node={'uuid':net['uuid'], 'enabled':False})
            except Exception: pass
            raise


if __name__ == '__main__':
    main()
