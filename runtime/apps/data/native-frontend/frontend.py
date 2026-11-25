#!/usr/bin/env python3
"""Native NEXUS menu controller. No transcoding, shell interpolation or public listener."""
import argparse
import datetime as dt
import getpass
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from epg_settings import EPGSettings
from wifi_settings import WifiSettings

ROOT = Path('/data/nexus-loader-test-v1')
BASE = Path('/data/native-frontend')
STATE = ROOT/'opt/frontend/state'
VERSION_FILE = Path(__file__).with_name('version.txt')
UUID = re.compile(r'^[0-9a-f]{32}$')


def device_info():
    try:
        version = VERSION_FILE.read_text().strip() or 'Unknown'
    except OSError:
        version = 'Unknown'
    addresses = []
    try:
        result = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'dev', 'eth0'],
                                capture_output=True, text=True, timeout=2, check=True)
        addresses = list(dict.fromkeys(re.findall(r'\binet\s+([0-9.]+)/', result.stdout)))
    except (OSError, subprocess.SubprocessError):
        pass
    rows = [('Phoenix version: ' + version, None),
            ('IP address: ' + (', '.join(addresses) or 'Not connected'), None)]
    try:
        result = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'dev', 'wlan0'],
                                capture_output=True, text=True, timeout=2)
        wifi = list(dict.fromkeys(re.findall(r'\bwlan0\s+inet\s+([0-9.]+)/', result.stdout)))
    except (OSError, subprocess.SubprocessError):
        wifi = []
    if wifi:
        rows.append(('Wi-Fi address: ' + ', '.join(wifi), None))
    return rows

def system_power_off():
    """Full shutdown: init runs data-stop (menu, Tvheadend, tuners, display, disk) and halts."""
    subprocess.Popen(['poweroff'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

def identifier(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ValueError('Invalid Tvheadend identifier')
    return value

def label(value):
    if isinstance(value, dict):
        return str(value.get('eng') or next(iter(value.values()), ''))
    return str(value or '')

def ascii_text(value, length=54):
    value = unicodedata.normalize('NFKD', label(value)).encode('ascii', 'ignore').decode().upper()
    return ''.join(c if c.isalnum() or c in " .,:!?/-+()'" else ' ' for c in value)[:length]

def local_zone():
    """Use the installation's IANA timezone, independent of the vendor chroot."""
    from zoneinfo import ZoneInfo
    name = os.environ.get('PHOENIX_TIMEZONE')
    if not name:
        try:
            name = Path('/mnt/recording-disk/phoenix-pvr/settings/timezone').read_text().strip()
        except OSError:
            name = 'UTC'
    return ZoneInfo(name)


def when(timestamp):
    return dt.datetime.fromtimestamp(int(timestamp), local_zone()).strftime('%a %d %H:%M')

class API:
    def __init__(self, config):
        self.base = config['url'].rstrip('/')
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme != 'http' or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Expected http://device-address:9981')
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base, config['username'], config['password'])
        # Do not send local credentials through inherited HTTP proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPDigestAuthHandler(manager))
        self.open_lock = threading.Lock()

    def open(self, path, data=None, headers=None):
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        with self.open_lock:
            return self.opener.open(request, timeout=10)

    def call(self, path, params=None, post=False):
        encoded = urllib.parse.urlencode(params or {}).encode()
        with self.open('/api/' + path + ('' if post else '?' + encoded.decode()),
                       encoded if post else None) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('API response exceeds limit')
        return json.loads(raw)

    def rows(self, path, **params):
        # Paginate rather than silently dropping channels/recordings after 50.
        rows = []
        for start in range(0, 10000, 200):
            page = self.call(path, dict(params, start=start, limit=200))
            entries = page.get('entries', [])
            rows.extend(entries)
            if len(entries) < 200 or len(rows) >= int(page.get('total', page.get('totalCount', 10000))):
                return rows
        raise ValueError('API list exceeds 10000 rows')

class Menu:
    def __init__(self, api, config_uuid=''):
        self.api, self.config_uuid = api, config_uuid
        self.page, self.selection = 'home', 0
        self.items, self.title = [], 'PHOENIX'
        self.stack = []
        self.message = ''
        self.pending = None
        self.booked = set()
        self.playback_active = False
        self.epg_settings = EPGSettings()
        self.wifi_settings = WifiSettings()
        self.power_off = system_power_off   # replaced by the HTML controller (LED) and tests
        self.refresh()

    def refresh(self):
        if self.page == 'home':
            self.title = 'PHOENIX'
            self.items = [('Live TV', 'live'), ('Programme guide', 'guide'),
                          ('Recordings', 'recordings'), ('Scheduled recordings', 'scheduled'),
                          ('Settings', 'settings')]
        elif self.page == 'settings':
            self.title = 'SETTINGS'
            self.items = [('Device info', 'device-info'), ('EPG settings', 'epg-settings'), ('Wi-Fi', 'wifi'),
                          ('Power off', 'power-off')]
        elif self.page == 'power-off':
            self.title = 'POWER OFF'
            self.items = [('Back', 'back'), ('Power off now', 'confirm')]
            self.message = ('Recording and the guide stop until the box is switched on again at the wall. '
                            'For standby with recordings carrying on, use the Power button on the remote.')
        elif self.page == 'wifi':
            self.wifi_settings.refresh(self)
        elif self.page.startswith('epg-'):
            self.epg_settings.refresh(self)
        elif self.page == 'device-info':
            self.title = 'DEVICE INFO'
            self.items = device_info()
        elif self.page in ('live', 'guide'):
            self.title = 'LIVE TV' if self.page == 'live' else 'GUIDE - SELECT CHANNEL'
            channels = self.api.rows('channel/grid')
            channels = [c for c in channels if c.get('enabled', True)]
            def order(c):
                try: return (float(c.get('number', 0) or 0), label(c.get('name')))
                except (ValueError, TypeError): return (0, label(c.get('name')))
            self.items = [(f"{c.get('number', '')} {label(c.get('name'))}", c) for c in sorted(channels, key=order)]
        elif self.page == 'events':
            channel = self.pending
            self.title = label(channel['name'])
            events = self.api.rows('epg/events/grid', channel=identifier(channel['uuid']), sort='start', dir='asc')
            self.items = [(f"{when(e['start'])} {label(e.get('title'))}", e) for e in events if int(e['stop']) > time.time()]
        elif self.page in ('recordings', 'scheduled'):
            finished = self.page == 'recordings'
            self.title = 'RECORDINGS' if finished else 'SCHEDULED RECORDINGS'
            endpoint = 'dvr/entry/grid_finished' if finished else 'dvr/entry/grid_upcoming'
            rows = self.api.rows(endpoint, sort='start', dir='desc' if finished else 'asc')
            if finished:
                active = [r for r in self.api.rows('dvr/entry/grid_upcoming')
                          if r.get('sched_status') == 'recording']
                active_ids = {r['uuid'] for r in active}
                rows = sorted(active, key=lambda r: int(r['start']), reverse=True) + [
                    r for r in rows if r['uuid'] not in active_ids]
            self.items = [(('RECORDING NOW - ' if r.get('sched_status') == 'recording' else '') +
                           f"{when(r['start'])} {label(r.get('disp_title') or r.get('title'))}", r) for r in rows]
        self.selection = min(self.selection, max(0, len(self.items)-1))

    def enter(self, page):
        self.stack.append((self.page, self.selection, self.items, self.title, self.pending))
        self.page, self.selection, self.message = page, 0, ''
        self.refresh()

    def back(self):
        if self.stack:
            self.page, self.selection, self.items, self.title, self.pending = self.stack.pop()
        self.message = ''

    def command(self, cmd):
        previous = (self.page,self.selection,self.items,self.title,self.pending,list(self.stack))
        try:
            return self._command(cmd)
        except Exception:
            self.page,self.selection,self.items,self.title,self.pending,self.stack=previous
            raise

    def _command(self, cmd):
        self.message = ''
        if cmd == 'BACK': self.back(); return
        if cmd in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            step = {'UP':-1,'DOWN':1,'LEFT':-6,'RIGHT':6}[cmd]
            self.selection = max(0, min(len(self.items)-1, self.selection+step))
            return
        if cmd != 'OK' or not self.items: return
        if self.page.startswith('epg-') and self.epg_settings.command(self, cmd):
            return
        if self.page == 'wifi' and self.wifi_settings.command(self, cmd):
            return
        item = self.items[self.selection][1]
        if self.page in ('home', 'settings'):
            self.enter(item)
        elif self.page == 'guide':
            self.stack.append((self.page,self.selection,self.items,self.title,self.pending))
            self.pending = item
            self.page, self.selection = 'events', 0
            self.refresh()
        elif self.page == 'live':
            self.playback_active = False
            return '/stream/channel/' + identifier(item['uuid']) + '?profile=pass'
        elif self.page == 'recordings':
            # Recheck at selection time: the menu may have been open when the
            # recording finished. Future scheduled entries are never playable.
            active_ids = {r['uuid'] for r in self.api.rows('dvr/entry/grid_upcoming')
                          if r.get('sched_status') == 'recording'}
            self.playback_active = item['uuid'] in active_ids
            return '/dvrfile/' + identifier(item['uuid'])
        elif self.page == 'events':
            self.stack.append((self.page,self.selection,self.items,self.title,self.pending))
            self.pending = item
            self.page, self.selection = 'confirm', 0
            self.title = label(item.get('title'))
            self.items = [('Back', 'back'), ('Schedule this programme', 'record')]
            self.message = when(item['start']) + ' - ' + when(item['stop'])
        elif self.page == 'confirm':
            if item == 'back': self.back(); return
            self.message = self.book(self.pending)
            if self.message == 'RECORDING SCHEDULED':
                self.items = [('Back', 'back')]
                self.selection = 0
        elif self.page == 'scheduled':
            self.message = label(item.get('status') or item.get('sched_status') or 'Scheduled')
        elif self.page == 'power-off':
            if item == 'back': self.back(); return
            self.items = []
            self.message = 'Powering off. To turn the box on again, switch it off and on at the wall.'
            self.power_off()

    def book(self, event):
        """Schedule a Tvheadend recording of an EPG event (the menu, the guide's Record key
        and Record while watching live TV). Returns the message to show."""
        eid = int(event['eventId'])
        if eid <= 0 or int(event['stop']) <= time.time(): raise ValueError('Programme has ended')
        if event.get('dvrUuid') or eid in self.booked:
            return 'ALREADY SCHEDULED'
        # Recheck server to avoid duplicate bookings after reopening the UI.
        existing = self.api.rows('dvr/entry/grid_upcoming')
        if any(int(r.get('start',0)) == int(event['start']) and r.get('channel') == event.get('channelUuid') for r in existing):
            return 'ALREADY SCHEDULED'
        result = self.api.call('dvr/entry/create_by_event',
            {'event_id':eid, 'config_uuid':self.config_uuid}, post=True)
        if result.get('error'): raise ValueError('Tvheadend rejected recording')
        self.booked.add(eid)
        return 'RECORDING SCHEDULED'

    def screen(self, revision):
        start = self.selection // 6 * 6
        rows = [ascii_text(x[0], 51) for x in self.items[start:start+6]]
        rows += ['']*(6-len(rows))
        status = self.message or (f'{self.selection+1} OF {len(self.items)}' if self.items else 'NO ITEMS - BACK TO RETURN')
        return '\n'.join([str(revision), str(self.selection % 6), ascii_text(self.title, 40),
            *rows, ascii_text(status), 'UP DOWN SELECT  OK OPEN  BACK RETURN']) + '\n'

class Proxy(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, api):
        self.api, self.tokens = api, {}
        super().__init__(('127.0.0.1', 9992), ProxyHandler)

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # URLs may contain short-lived playback tokens.
    def do_HEAD(self): self.transfer(False)
    def do_GET(self): self.transfer(True)
    def transfer(self, body):
        path = self.server.tokens.get(self.path)
        if not path:
            print('PLAYBACK_PROXY_UNKNOWN_REQUEST METHOD='+self.command,flush=True)
            self.send_error(404); return
        print('PLAYBACK_PROXY_REQUEST', 'GET' if body else 'HEAD', 'RANGE='+str(self.headers.get('Range','none')),flush=True)
        sent=0
        headers = {}
        if self.headers.get('Range'):
            if not re.fullmatch(r'bytes=\d*-\d*', self.headers['Range']): self.send_error(400); return
            headers['Range'] = self.headers['Range']
        try:
            with self.server.api.open(path, headers=headers) as upstream:
                print('PLAYBACK_UPSTREAM',upstream.status,'TYPE='+str(upstream.headers.get('Content-Type')),
                      'LENGTH='+str(upstream.headers.get('Content-Length')),flush=True)
                self.send_response(upstream.status)
                for name in ('Content-Type','Content-Length','Content-Range','Accept-Ranges'):
                    if name in upstream.headers: self.send_header(name, upstream.headers[name])
                self.send_header('Connection','close'); self.end_headers()
                if body:
                    while self.path in self.server.tokens:
                        data = upstream.read(188*128)
                        if not data: break
                        self.wfile.write(data)
                        sent += len(data)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as exc:
            print('PLAYBACK_PROXY_ERROR',type(exc).__name__,'HTTP='+str(getattr(exc,'code','none')),flush=True)
            self.close_connection = True
        finally:
            print('PLAYBACK_PROXY_CLOSED BYTES='+str(sent),flush=True)

def renderer():
    env = dict(os.environ, LD_PRELOAD='/opt/frontend/frontend.so')
    return subprocess.Popen(['chroot',str(ROOT),'/lib/ld-linux-armhf.so.3',
        '--library-path','/opt/blit/lib:/lib','/opt/screen-test/screen-client','-timeout','86400'],
        env=env,stdin=subprocess.DEVNULL)

def terminate(child):
    if child and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(timeout=5)

def player_options(active):
    return ['-once', '-gui', 'off'] + (['-timeshift'] if active else [])

def serve(config):
    api = API(config)
    menu = Menu(api, config.get('dvr_config_uuid',''))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    fifo = STATE/'commands'
    if fifo.exists():
        import stat
        if not stat.S_ISFIFO(fifo.stat().st_mode): raise ValueError('Unexpected commands file')
        fifo.unlink()
    os.mkfifo(fifo,0o600)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    proxy = Proxy(api)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    child = None
    stop = threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    revision = 0
    def publish():
        nonlocal revision
        revision += 1
        temp = STATE/'view.new'
        temp.write_text(menu.screen(revision),encoding='ascii')
        temp.replace(STATE/'view')
    publish()
    child = renderer()
    buffer = b''
    try:
        while not stop.is_set():
            if child.poll() is not None:
                raise RuntimeError('Native renderer exited; inspect frontend log')
            try: buffer += os.read(fd,4096)
            except BlockingIOError: pass
            if len(buffer) > 8192: buffer = b''
            while b'\n' in buffer:
                line,buffer = buffer.split(b'\n',1)
                parts = line.decode('ascii',errors='ignore').split()
                if len(parts)!=2 or not parts[0].isdigit() or int(parts[0])!=revision: continue
                try:
                    stream = menu.command(parts[1])
                    if stream:
                        print('PLAYBACK_START KIND='+('LIVE' if stream.startswith('/stream/') else 'RECORDING'),flush=True)
                        terminate(child)
                        print('MENU_HANDOVER_EXIT STATUS='+str(child.returncode),flush=True)
                        # Vendor player may select its input handler from the URL suffix.
                        token = '/'+secrets.token_hex(24)+'.ts'
                        proxy.tokens[token] = stream
                        child = subprocess.Popen(['chroot',str(ROOT),'/lib/ld-linux-armhf.so.3',
                            '--library-path','/opt/playback-test/lib:/lib','/opt/playback-test/play',
                            *player_options(menu.playback_active),'http://127.0.0.1:9992'+token],stdin=subprocess.DEVNULL,
                            env=dict({k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_LIBRARY_PATH')},
                                     LD_PRELOAD='/opt/frontend/player-controls.so'))
                        print('PLAYBACK_GROWING_RECORDING='+str(int(menu.playback_active)),flush=True)
                        while child.poll() is None and not stop.wait(0.2): pass
                        terminate(child)
                        print('PLAYBACK_EXIT STATUS='+str(child.returncode),flush=True)
                        proxy.tokens.pop(token,None)
                        if stop.is_set(): break
                        # Discard held-key input from the previous menu.
                        try:
                            while os.read(fd,4096): pass
                        except BlockingIOError: pass
                        buffer=b''
                        menu.message='PLAYBACK FINISHED'
                        publish()
                        child=renderer()
                    else: publish()
                except Exception as exc:
                    menu.message = 'ERROR '+type(exc).__name__+' - CHECK LOG'
                    print('FRONTEND_ACTION_ERROR',type(exc).__name__,str(exc),flush=True)
                    publish()
            stop.wait(0.1)
    finally:
        terminate(child)
        proxy.tokens.clear(); proxy.shutdown(); proxy.server_close()
        os.close(fd); fifo.unlink(missing_ok=True)

def configure(path):
    url=input('Tvheadend URL [http://127.0.0.1:9981]: ').strip() or 'http://127.0.0.1:9981'
    username=input('Tvheadend username [admin]: ').strip() or 'admin'
    config={'url':url,'username':username,'password':getpass.getpass('Tvheadend password: ')}
    api=API(config)
    channels=api.rows('channel/grid')
    profiles=api.rows('dvr/config/grid')
    defaults=[p for p in profiles if p.get('name','')=='']
    if len(defaults)!=1: raise ValueError('Expected one default DVR configuration')
    config['dvr_config_uuid']=identifier(defaults[0]['uuid'])
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd,'w') as f: json.dump(config,f)
    print('AUTHENTICATION_OK CHANNELS='+str(len(channels)))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--configure',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--config',type=Path,default=Path('/mnt/recording-disk/phoenix-pvr/settings/frontend.json'))
    args=parser.parse_args()
    if args.configure: configure(args.config)
    else:
        config=json.loads(args.config.read_text())
        if args.check:
            menu=Menu(API(config),config.get('dvr_config_uuid',''))
            for page in ('live','guide','recordings','scheduled'):
                menu.page=page;menu.refresh();print(page,len(menu.items))
        else: serve(config)
