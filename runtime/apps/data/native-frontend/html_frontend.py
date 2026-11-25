#!/usr/bin/env python3
"""Chromium HTML menu controller for the STB.

Reuses frontend.py for the Tvheadend API, menu model, token relay (127.0.0.1:9992)
and vendor player options. The HTML page is served on 127.0.0.1:9993 only.
Chromium renders the menu and playback overlay above the vendor video plane.
The controller also owns recording indexes, timeshift and the dedicated live tuner.
"""
import argparse
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.parse


def startup_notice():
    try:
        service = Path('/run/tvheadend-status').read_text().strip()
    except OSError:
        service = ''
    messages = {
        'TUNER_START_FAILED': 'Tuner startup failed. Restart the box; see Device info for its IP.',
        'START_FAILED': 'Tvheadend startup failed. See Settings for device info.',
        'START_RETRY': 'Tvheadend is retrying startup.',
        'RESTART_LIMIT_REACHED': 'Tvheadend stopped after repeated failures. Restart the box.',
    }
    if service in messages:
        return messages[service]
    if time.time() < 1767225600:
        return 'Clock not set: trying network or aerial time. Check Settings for device info.'
    try:
        source = Path('/run/phoenix-time-status').read_text().strip()
    except OSError:
        source = ''
    if source in ('RESTORED_APPROXIMATE', 'APPROXIMATE'):
        return 'Clock restored approximately; waiting for network or aerial time.'
    return ''


def load_native():
    # Load frontend.py explicitly: the ARM renderer preload is also named frontend.so.
    existing = sys.modules.get('frontend')
    if existing is not None and hasattr(existing, 'Menu'):
        return existing
    spec = importlib.util.spec_from_file_location('frontend', Path(__file__).with_name('frontend.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules['frontend'] = module
    spec.loader.exec_module(module)
    return module


native = load_native()
sys.path.insert(0, str(Path(__file__).parent))
from initial_setup import InitialSetup
from browser import select as select_browser
import guide as epg  # noqa: E402  (grid guide data; needs 'frontend' registered first)

ROOT = native.ROOT
BASE = native.BASE
PORT = 9993
ROWS = 6
COMMANDS = ('UP', 'DOWN', 'LEFT', 'RIGHT', 'OK', 'BACK')
# Remote keys handled while something plays (the page sends them to /api/live-key).
LIVE_KEYS = ('CH_UP', 'CH_DOWN', 'RECORD', 'INFO') + tuple('D' + str(n) for n in range(10))
DIGIT_WAIT = 2.0          # seconds after the last number key before changing channel
CHANNEL_WAIT = 1.5        # seconds after the last CH+/CH- before tuning (lets the viewer step through)
NOTICE_SECONDS = 4        # overlay notices ("Recording ...", "No channel 47")
PROGRAMME_SECONDS = 300   # now/next cache per channel (refreshed sooner when a programme ends)
STANDBY_FILE = Path('/mnt/recording-disk/phoenix-pvr/settings/standby')
CHANNEL_PATH = re.compile(r'^/stream/channel/([0-9a-f]{32})')
# Texta fonts copied from the stock UI release; served read-only to the menu page.
FONTS = {'/fonts/' + n: n for n in ('Texta-Medium.ttf', 'Texta-Light.ttf', 'Texta-Bold.ttf')}
FONT_DIR = Path(__file__).with_name('fonts')
# Grid guide script and styles (guide.js / guide.css beside menu.html).
ASSETS = {'/guide.js': ('guide.js', 'application/javascript; charset=utf-8'),
          '/guide.css': ('guide.css', 'text/css; charset=utf-8'),
          '/phoenix.png': ('phoenix.png', 'image/png')}   # header logo
IMG_PATH = re.compile(r'^/img/(\d{1,9})$')
# Finished recordings play from local disk with a Broadcom NAV index (made by mkindex), which
# gives the vendor player real FF/RW/seek. Both folders are bound into the vendor chroot.
RECORDINGS = Path('/mnt/recording-disk/phoenix-pvr/recordings')
INDEX_DIR = Path('/mnt/recording-disk/phoenix-pvr/index')
CHROOT_RECORDINGS = '/media/recordings'
CHROOT_INDEX = '/media/index'
MKINDEX = '/opt/frontend/mkindex'
STATUS_PRELOAD = '/opt/frontend/player-status.so'
PLAYER_STATUS = ROOT/'tmp/player-status'   # written once a second by player-status.so
# Keep Chromium on screen (transparent) while the vendor player runs, so the page can draw
# the playback bar over the video. Verified on the DVR, 11 Nov 2026.
OVERLAY = True
PROBE_DIR = re.compile(r'^/opt/chromium$')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def text(value, length=80):
    return CONTROL.sub(' ', native.label(value))[:length]


def clock():
    # Configured local time, as in frontend.py; the chroot browser has no tz data.
    # A missing zone must not break the menu, so the clock is simply left blank.
    import datetime as dt
    try:
        return dt.datetime.now(native.local_zone()).strftime('%a %d %b  %H:%M')
    except Exception:
        return ''


def hhmm(when):
    """Local HH:MM for an epoch time ('' when the zone is missing)."""
    import datetime as dt
    try:
        return dt.datetime.fromtimestamp(int(when), native.local_zone()).strftime('%H:%M')
    except Exception:
        return ''


def nav_name(ts):
    """Index file name for a recording path relative to RECORDINGS."""
    rel = ts.relative_to(RECORDINGS).as_posix()
    return re.sub(r'[^A-Za-z0-9._-]', '_', rel.replace('/', '__')) + '.nav'


def local_recording(item):
    """Chroot paths (ts, nav) for a finished Tvheadend entry with a ready index, else None."""
    try:
        ts = Path(str(item.get('filename') or ''))
        if not ts.is_absolute() or ts.suffix != '.ts':
            return None
        ts = ts.resolve()
        rel = ts.relative_to(RECORDINGS)  # ValueError outside the recordings folder
        nav = INDEX_DIR/nav_name(ts)
        if not ts.is_file() or not nav.is_file() or nav.stat().st_mtime < ts.stat().st_mtime:
            return None
        return (CHROOT_RECORDINGS + '/' + rel.as_posix(), CHROOT_INDEX + '/' + nav.name)
    except (OSError, ValueError):
        return None


def growing_recording(item):
    """Chroot paths (ts, nav, 'follow') for a recording still being written, so it can be
    indexed while it grows (mkindex -follow) and played with trick modes; else None."""
    try:
        ts = Path(str(item.get('filename') or ''))
        if not ts.is_absolute() or ts.suffix != '.ts':
            return None
        ts = ts.resolve()
        rel = ts.relative_to(RECORDINGS)
        if not ts.is_file():
            return None
        return (CHROOT_RECORDINGS + '/' + rel.as_posix(), CHROOT_INDEX + '/' + nav_name(ts) + '.growing', 'follow')
    except (OSError, ValueError):
        return None


def read_player_status(path=PLAYER_STATUS, max_age=5):
    """Latest line from player-status.so, as ints: pos (ms), state (1 play, 2 pause), rate,
    and when present scrub (paused scrub target in ms, -1 none), vol (0-20, -1 unknown),
    mute and vtick (changes on each volume key)."""
    try:
        if time.time() - path.stat().st_mtime > max_age:
            return None
        fields = dict(x.split('=', 1) for x in path.read_text().split() if '=' in x)
        return {k.lower(): int(fields[k]) for k in ('POS', 'STATE', 'RATE', 'SCRUB', 'VOL', 'MUTE', 'VTICK')
                if k in fields}
    except (OSError, ValueError):
        return None


# ---- Resume positions and extra player settings ----
# Where finished, locally indexed recordings were stopped: {uuid: {'pos': ms, 'at': unix time}}.
POSITIONS = Path('/mnt/recording-disk/phoenix-pvr/settings/positions.json')
POSITIONS_KEEP = 300
RESUME_MIN_MS = 30000          # closer to the start than this: start from the beginning
RESUME_END_MS = 60000          # closer to the end than this: treat as watched
# Remote volume key NEC words, "up,down,mute" in hex as logged by PLAYER_IR CODE=.
VOLUME_KEYS = Path('/mnt/recording-disk/phoenix-pvr/settings/volume-keys')
VOLUME_KEYS_FORMAT = re.compile(r'^[0-9A-Fa-f]{0,8},[0-9A-Fa-f]{0,8},[0-9A-Fa-f]{0,8}$')
# Settings for the next player launch (resume point, duration), set by the Controller.
PLAYER_EXTRA_ENV = {}
# Audio track override for the next player launch (set by Runner.play from the PMT).
PLAYER_AUDIO = []


def hms_text(ms):
    seconds = max(0, int(ms) // 1000)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def load_positions(path=None):
    try:
        data = json.loads((path or POSITIONS).read_text())
        return {k: v for k, v in data.items() if native.UUID.fullmatch(str(k)) and isinstance(v, dict)
                and isinstance(v.get('pos'), int)} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_positions(data, path=None):
    path = path or POSITIONS
    keep = dict(sorted(data.items(), key=lambda kv: kv[1].get('at', 0))[-POSITIONS_KEEP:])
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(json.dumps(keep))
        os.replace(tmp, path)
    except OSError as exc:
        print('POSITION_SAVE_FAILED', type(exc).__name__, flush=True)


def volume_keys(path=None):
    try:
        value = (path or VOLUME_KEYS).read_text().strip()
    except OSError:
        return ''
    return value if VOLUME_KEYS_FORMAT.fullmatch(value) else ''


class Controller:
    """Thread-safe bridge between HTTP requests, the menu model and the runner."""

    def __init__(self, menu):
        self.menu = menu
        self.setup = None
        self.lock = threading.Lock()
        self.revision = 1
        self.token = secrets.token_hex(16)
        self.pending = None
        self.wake = threading.Event()
        self.key_logs = 0
        self.playing = None   # title/duration of the item being played, for the overlay
        self.timeshift = None  # Timeshift, when live TV is buffered to disk (set by serve)
        # Instant live TV: 'relay' (direct live feed) or 'buffer' (playing the pause buffer).
        self.live_mode = None
        self.live_switch = threading.Event()   # set by the page on Pause/Left during 'relay'
        self.live_switch_at = None
        self.live_source = None                 # 'tuner' (dedicated live tuner) or 'relay'
        self.live_stop = threading.Event()      # Back pressed while no player owns the keys
        self.guide = epg.Guide(menu.api)
        self.positions_file = None   # resume positions (None: POSITIONS)
        self.resume_ms = 0           # start point chosen on the resume page
        self.last_status = None      # last player status seen, saved as the resume point
        # Remote keys during playback, channel changes and standby.
        self.interrupt = threading.Event()  # ends the current playback (channel change, standby)
        self.next_play = None        # (stream, item) to start as soon as the current one ends
        self.digits, self.digits_at, self.digit_timer = '', 0.0, None
        self.channel_pick, self.channel_pick_at, self.channel_timer = None, 0.0, None
        self.after_play = None       # 'guide': open the guide when playback ends (Guide key)
        self.notice, self.notice_until = '', 0.0
        self.osd_seq = 0             # bumped when the overlay should show (Info, channel change)
        self.programmes = {}         # channel uuid -> (loaded_at, [now, next] events)
        self.programmes_loading = set()
        self.standby_file = STANDBY_FILE
        self.standby = False
        self.led = manager_led       # front-panel LED setter (replaced in tests)
        menu.power_off = self._power_off

    def restore_standby(self):
        """Come back in standby after a restart if the box was in standby (as the stock UI)."""
        try:
            self.standby = self.standby_file.read_text().strip() == '1'
        except OSError:
            self.standby = False
        self._set_led('power', 'standby' if self.standby else 'on')
        if self.standby:
            print('STANDBY_RESTORED', flush=True)

    def _power_off(self):
        """Settings > Power off (lock held): LEDs off, then a full shutdown."""
        print('POWER_OFF_REQUESTED', flush=True)
        for name in ('recording', 'internet', 'power'):
            self.led(name, 'off')
        native.system_power_off()

    def _set_led(self, name, state):
        threading.Thread(target=self.led, args=(name, state), daemon=True).start()

    # ---- Power ----
    def power(self):
        """Power key: standby stops playback, blanks the screen and sets the power LED to its
        standby colour; recordings carry on. Power again wakes to the menu."""
        with self.lock:
            self.standby = not self.standby
            self.next_play, self.after_play = None, None
            self._clear_digits()
            self._clear_pick()
            if self.standby and self.pending is not None:
                self.interrupt.set()
            if not self.standby:
                self.menu.message = ''
            try:
                if self.standby:
                    self.standby_file.write_text('1\n')
                else:
                    self.standby_file.unlink(missing_ok=True)
            except OSError as exc:
                print('STANDBY_SAVE_FAILED', type(exc).__name__, flush=True)
            self._set_led('power', 'standby' if self.standby else 'on')
            print('STANDBY ' + ('ENTER' if self.standby else 'EXIT'), flush=True)
            self.revision += 1
            return self._view()

    # ---- Remote keys while watching ----
    def live_key(self, key):
        """CH+/CH-, number keys, Record and Info while something plays. Channel keys and
        Record act on live TV only; Info shows the overlay for anything."""
        if key not in LIVE_KEYS:
            raise ValueError('Unknown key')
        with self.lock:
            if self.standby or self.pending is None:
                return self._view()
            playing = self.playing or {}
            channel = playing.get('channel') if playing.get('live') else None
            if key == 'INFO':
                self.osd_seq += 1
            elif channel is None:
                pass
            elif key in ('CH_UP', 'CH_DOWN'):
                self._clear_digits()
                self._pick_channel(channel, 1 if key == 'CH_UP' else -1)
            elif key == 'RECORD':
                self._clear_digits()
                self._clear_pick()
                self._record_live(channel)
            else:
                self._clear_pick()
                self._digit(key[1])
            return self._view()

    def _pick_channel(self, current, step):
        """CH+/CH- step through channels on the overlay; tune CHANNEL_WAIT s after the last press."""
        base = self.channel_pick['uuid'] if self.channel_pick else current
        target = self._step_channel(base, step)
        if target is None:
            return
        self.channel_pick, self.channel_pick_at = target, time.monotonic()
        self.notice = (self._number(target.get('number')) + '  ' + str(target.get('name', ''))).strip()
        self.notice_until = time.time() + CHANNEL_WAIT + 1
        self.osd_seq += 1
        if self.channel_timer is not None:
            self.channel_timer.cancel()
        self.channel_timer = threading.Timer(CHANNEL_WAIT, self._channel_due)
        self.channel_timer.daemon = True
        self.channel_timer.start()

    def _clear_pick(self):
        self.channel_pick = None
        if self.channel_timer is not None:
            self.channel_timer.cancel()
            self.channel_timer = None

    def _channel_due(self, force=False):
        with self.lock:
            target = self.channel_pick
            if target is None or (not force and time.monotonic() - self.channel_pick_at < CHANNEL_WAIT - 0.1):
                return
            self._clear_pick()
            if self.pending is not None and (self.playing or {}).get('live') and not self.standby:
                self.notice_until = 0
                self._zap(target)
                self.revision += 1

    def guide_key(self):
        """Guide button: open the programme guide, ending any playback first."""
        with self.lock:
            if self.standby or self.setup is not None and self.setup.view() is not None:
                return self._view()
            self._clear_digits(); self._clear_pick()
            if self.pending is not None:
                self.next_play, self.after_play = None, 'guide'
                self.interrupt.set()
            else:
                self._open_guide()
            self.revision += 1
            return self._view()

    def _open_guide(self):
        m = self.menu
        m.stack, m.page, m.pending, m.message = [], 'home', None, ''
        m.refresh()
        m.selection = next((i for i, row in enumerate(m.items) if row[1] == 'guide'), 0)
        m.enter('guide')

    def _channels(self):
        try:
            return self.guide.channel_list()
        except Exception as exc:
            print('CHANNEL_LIST_ERROR', type(exc).__name__, flush=True)
            return []

    def _step_channel(self, uuid, step):
        channels = self._channels()
        if not channels:
            return None
        at = next((i for i, c in enumerate(channels) if c['uuid'] == uuid), -1)
        return channels[(at + step) % len(channels)] if at >= 0 else channels[0]

    @staticmethod
    def _number(value):
        """Channel number as typed on the remote: '7', '20' ('7.0' -> '7'), or ''."""
        text_value = str(value or '').strip()
        if text_value.endswith('.0'):
            text_value = text_value[:-2]
        return text_value if text_value.isdigit() else ''

    def _clear_digits(self):
        self.digits = ''
        if self.digit_timer is not None:
            self.digit_timer.cancel()
            self.digit_timer = None

    def _digit(self, d):
        self.digits = (self.digits + d)[-3:]
        self.digits_at = time.monotonic()
        self.osd_seq += 1
        typed = str(int(self.digits))
        numbers = [self._number(c.get('number')) for c in self._channels()]
        longer = [n for n in numbers if n.startswith(typed) and n != typed]
        if len(self.digits) >= 3 or not longer:
            self._commit_digits()
            return
        if self.digit_timer is not None:
            self.digit_timer.cancel()
        self.digit_timer = threading.Timer(DIGIT_WAIT, self._digits_due)
        self.digit_timer.daemon = True
        self.digit_timer.start()

    def _digits_due(self):
        with self.lock:
            if self.digits and time.monotonic() - self.digits_at >= DIGIT_WAIT - 0.1:
                self._commit_digits()
                self.revision += 1

    def _commit_digits(self):
        typed = str(int(self.digits)) if self.digits else ''
        self._clear_digits()
        if not typed or self.pending is None or not (self.playing or {}).get('live'):
            return
        target = next((c for c in self._channels() if self._number(c.get('number')) == typed), None)
        if target is None:
            self._notify('No channel ' + typed)
        else:
            self._zap(target)

    def _notify(self, message):
        self.notice, self.notice_until = message, time.time() + NOTICE_SECONDS
        self.osd_seq += 1

    def _zap(self, channel):
        """Change live channel without going back to the menu: the runner ends the current
        session (and its pause buffer) and starts the new channel on the live tuner."""
        if channel['uuid'] == (self.playing or {}).get('channel') and self.next_play is None:
            return
        self.next_play = ('/stream/channel/' + channel['uuid'] + '?profile=pass',
                          {'name': channel.get('name', ''), 'channelUuid': channel['uuid']})
        self.interrupt.set()
        self.osd_seq += 1
        print('LIVE_ZAP NUMBER=' + re.sub(r'[^0-9.]', '', str(channel.get('number', ''))), flush=True)

    def _record_live(self, uuid):
        """Record the programme on air now on the channel being watched."""
        try:
            now = time.time()
            event = next((e for e in self._load_programmes(uuid)
                          if int(e['start']) <= now < int(e['stop'])), None)
            if event is None:
                self._notify('No programme information to record')
                return
            message = self.menu.book(dict(event, channelUuid=uuid))
            self.programmes.pop(uuid, None)   # pick up the new recording state
            self._notify(('Recording ' + text(event.get('title'), 60)) if message == 'RECORDING SCHEDULED'
                         else 'Already recording')
        except Exception as exc:
            print('LIVE_RECORD_ERROR', type(exc).__name__, str(exc)[:120], flush=True)
            self._notify('Could not schedule the recording')

    def _load_programmes(self, uuid):
        """Now and next on a channel straight from Tvheadend (blocking)."""
        now = time.time()
        flt = json.dumps([{'field': 'stop', 'type': 'numeric', 'value': int(now), 'comparison': 'gt'}])
        rows = self.menu.api.call('epg/events/grid', {'channel': uuid, 'filter': flt, 'sort': 'start',
                                                      'dir': 'asc', 'start': 0, 'limit': 3}).get('entries', [])
        out = []
        for e in rows:
            try:
                if e.get('channelUuid', uuid) == uuid and int(e['stop']) > now:
                    int(e['start'])
                    out.append(e)
            except (KeyError, TypeError, ValueError):
                continue
        out = sorted(out, key=lambda e: int(e['start']))[:2]
        self.programmes[uuid] = (time.time(), out)
        return out

    def _programmes_async(self, uuid):
        """Cached now/next for the overlay; refreshed in the background, never in the view."""
        hit = self.programmes.get(uuid)
        now = time.time()
        stale = hit is None or now - hit[0] > PROGRAMME_SECONDS or bool(hit[1] and int(hit[1][0]['stop']) <= now)
        if stale and uuid not in self.programmes_loading:
            self.programmes_loading.add(uuid)

            def load():
                try:
                    self._load_programmes(uuid)
                except Exception as exc:
                    print('PROGRAMME_LOAD_ERROR', type(exc).__name__, flush=True)
                    self.programmes[uuid] = (time.time(), hit[1] if hit else [])
                finally:
                    self.programmes_loading.discard(uuid)
            threading.Thread(target=load, daemon=True).start()
        return [e for e in (hit[1] if hit else []) if int(e['stop']) > now]

    def _live_details(self, playing):
        """Channel number/name and the programme now and next, for the live overlay."""
        uuid = playing.get('channel')
        out = {}
        channel = next((c for c in (self.guide.channels or ()) if c['uuid'] == uuid), None)
        if channel:
            out['number'], out['channel_name'] = channel['number'], channel['name']
        events = self._programmes_async(uuid) if uuid else []
        now = time.time()
        shown = [{'title': text(e.get('title'), 80) or 'Untitled', 'start': int(e['start']),
                  'stop': int(e['stop']), 'start_time': hhmm(e['start']),
                  'times': hhmm(e['start']) + ' – ' + hhmm(e['stop']),
                  'rec': bool(e.get('dvrUuid')) and str(e.get('dvrState', '')) not in ('', 'missed')}
                 for e in events]
        if shown and shown[0]['start'] <= now:
            out['now'] = shown.pop(0)
        if shown:
            out['next'] = shown[0]
        return out

    def guide_select(self, revision, channel, event_id):
        """OK on a grid cell: open the programme's options (watch if on now, schedule)."""
        channel = native.identifier(channel)
        event = self.guide.event(channel, int(event_id))   # outside the lock: may call Tvheadend
        with self.lock:
            m = self.menu
            if self.pending or revision != self.revision or m.page != 'guide' or event is None:
                return self._view()
            now = time.time()
            airing = int(event['start']) <= now < int(event['stop'])
            m.stack.append((m.page, m.selection, m.items, m.title, m.pending))
            m.pending = dict(event, channelUuid=channel)
            m.page, m.selection, m.message = 'confirm', 0, ''
            m.title = native.label(event.get('title'))
            m.items = ([('Watch now', 'watch')] if airing else []) + [
                ('Schedule this programme', 'record'), ('Back', 'back')]
            m.message = native.when(event['start']) + ' - ' + native.when(event['stop'])
            self.revision += 1
            return self._view()

    def view(self):
        with self.lock:
            return self._view()

    def _view(self):
        if self.setup is not None:
            setup = self.setup.view()
            if setup is not None:
                return {'revision':self.revision, 'page':'setup', 'title':'Welcome', 'rows':[],
                        'selected':0, 'status':setup.get('message', ''), 'playback':False,
                        'clock':clock(), 'setup':setup}
        m = self.menu
        if m.page in ('device-info', 'epg-settings', 'wifi'):
            m.refresh()
        start = m.selection // ROWS * ROWS
        if self.pending:
            status = 'Starting playback...'
        elif m.message:
            status = m.message
        elif m.page == 'device-info':
            status = 'Press Back to return to Settings.'
        elif m.page == 'home' and (notice := startup_notice()):
            status = notice
        elif m.items:
            status = f'{m.selection + 1} of {len(m.items)}'
        else:
            status = 'No items. Press Back to return.'
        view = {'revision': self.revision, 'page': m.page, 'title': text(m.title, 60),
                'rows': [text(x[0]) for x in m.items[start:start + ROWS]],
                'selected': m.selection - start, 'status': text(status),
                'playback': self.pending is not None, 'clock': clock()}
        if m.page == 'live':
            # Channel logos for the visible rows ('' = show the number), via /img/<n>.
            view['icons'] = [epg.image(x[1].get('icon_public_url') or x[1].get('icon'))
                             if isinstance(x[1], dict) else '' for x in m.items[start:start + ROWS]]
        if self.pending is not None:
            status = read_player_status()
            if status and status.get('state') in (1, 2):
                self.last_status = status
            view['playing'] = dict(self.playing or {}, player=status,
                                   indexed=self.pending[2] is not None)
            if self.timeshift is not None and view['playing'].get('live'):
                # Seconds of live TV in the pause buffer (None until it has started).
                view['playing']['buffer'] = self.timeshift.buffered()
                view['playing']['buffer_from'] = self.timeshift.buffer_from()
                view['playing']['starting'] = bool(getattr(self.timeshift, 'buffering', False))
                view['playing']['mode'] = self.live_mode
                view['playing']['source'] = self.live_source
            if view['playing'].get('live') and view['playing'].get('channel'):
                view['playing'].update(self._live_details(view['playing']))
            view['playing']['digits'] = self.digits
            view['playing']['notice'] = self.notice if time.time() < self.notice_until else ''
            view['playing']['osd'] = self.osd_seq
        if self.standby:
            view['standby'] = True
        return view

    def command(self, revision, cmd):
        if cmd not in COMMANDS:
            raise ValueError('Unknown command')
        with self.lock:
            # Ignore stale keys from an old view and any input during handover.
            if self.pending or self.standby or revision != self.revision:
                return self._view()
            if self.setup is not None and self.setup.view() is not None:
                if cmd == 'OK':
                    self.setup.action()
                    self.revision += 1
                return self._view()
            watch = None
            try:
                watch = self._guide_watch(cmd) or self._resume(cmd)
                stream = watch[0] if watch else self.menu.command(cmd)
            except Exception as exc:
                self.menu.message = (str(exc) if self.menu.page.startswith('epg-') and isinstance(exc, ValueError)
                                     else 'Error ' + type(exc).__name__ + ' - check log')
                print('FRONTEND_ACTION_ERROR', type(exc).__name__, str(exc), flush=True)
            else:
                if stream:
                    item = watch[1] if watch else (
                        self.menu.items[self.menu.selection][1] if self.menu.items else {})
                    item = item if isinstance(item, dict) else {}
                    self._begin(stream, item, bool(self.menu.playback_active))
            self._player_env()
            self.revision += 1
            return self._view()

    def _begin(self, stream, item, active):
        """Hand a stream to the runner (lock held)."""
        local = None if not stream.startswith('/dvrfile/') else (
            growing_recording(item) if active else local_recording(item))
        self.pending = (stream, active, local)
        title = item.get('disp_title') or item.get('title') or item.get('name') or ''
        try:
            duration = max(0, int(item['stop']) - int(item['start']))
        except (KeyError, TypeError, ValueError):
            duration = 0
        self.playing = {'title': text(title, 80), 'duration': duration,
                        'live': stream.startswith('/stream/')}
        if stream.startswith('/stream/'):
            icon = epg.image(item.get('icon_public_url') or item.get('icon'))
            match = CHANNEL_PATH.match(stream)
            if match:
                self.playing['channel'] = match.group(1)
                self._channels()   # numbers and names for the overlay (cached list)
            if not icon and (item.get('channelUuid') or match):
                icon = self.guide.icon(item.get('channelUuid') or match.group(1))
            self.playing['icon'] = icon
        print('PLAYBACK_INDEXED=' + str(int(local is not None)), flush=True)
        print('PLAYBACK_START KIND=' + ('LIVE' if stream.startswith('/stream/') else 'RECORDING'),
              flush=True)
        self.wake.set()

    def guide_record(self, revision, channel, event_id):
        """Record key on a guide cell: book that programme straight away."""
        channel = native.identifier(channel)
        event = self.guide.event(channel, int(event_id))   # outside the lock: may call Tvheadend
        with self.lock:
            if self.pending or self.standby or revision != self.revision or self.menu.page != 'guide':
                return {'ok': False, 'message': ''}
            if event is None:
                return {'ok': False, 'message': 'Programme not found'}
            try:
                message = self.menu.book(dict(event, channelUuid=channel))
            except ValueError as exc:
                return {'ok': False, 'message': str(exc)}
            except Exception as exc:
                print('GUIDE_RECORD_ERROR', type(exc).__name__, flush=True)
                return {'ok': False, 'message': 'Could not schedule the recording'}
            with self.guide.lock:
                self.guide.windows.clear()     # the next window shows the recording badge
            ok = message == 'RECORDING SCHEDULED'
            return {'ok': True, 'scheduled': ok,
                    'message': 'Recording scheduled' if ok else 'Already scheduled'}

    # ---- Resume ----
    def _resume(self, cmd):
        """OK on a finished, indexed recording with a saved position opens a resume page
        ('Resume from H:MM:SS' / 'Start from beginning'); OK there starts playback."""
        m = self.menu
        if cmd != 'OK' or not m.items:
            return None
        if m.page == 'resume':
            choice, info = m.items[m.selection][1], m.pending
            m.back()
            self.resume_ms = info['pos'] if choice == 'resume' else 0
            m.playback_active = False
            return '/dvrfile/' + native.identifier(info['item']['uuid']), info['item']
        item = m.items[m.selection][1]
        if m.page != 'recordings' or not isinstance(item, dict) or item.get('sched_status') == 'recording':
            return None
        uuid = item.get('uuid')
        saved = load_positions(self.positions_file).get(uuid) if isinstance(uuid, str) else None
        if not saved or saved['pos'] < RESUME_MIN_MS or local_recording(item) is None:
            return None
        m.stack.append((m.page, m.selection, m.items, m.title, m.pending))
        m.pending = {'item': item, 'pos': saved['pos']}
        m.page, m.selection = 'resume', 0
        m.title = native.label(item.get('disp_title') or item.get('title'))
        m.items = [('Resume from ' + hms_text(saved['pos']), 'resume'), ('Start from beginning', 'start')]
        m.message = 'Stopped at ' + hms_text(saved['pos'])
        return '', None   # page changed, no playback

    def _player_env(self):
        """Resume point and duration for the player launch that is about to happen."""
        PLAYER_EXTRA_ENV.clear()
        if self.pending is None:
            return
        if self.resume_ms > 0:
            PLAYER_EXTRA_ENV['PLAYER_START_MS'] = str(int(self.resume_ms))
        duration = (self.playing or {}).get('duration') or 0
        if duration and self.pending[2] is not None and len(self.pending[2]) == 2:
            PLAYER_EXTRA_ENV['PLAYER_DURATION_MS'] = str(int(duration) * 1000)
        self.resume_ms = 0
        self.last_status = None

    def _save_position(self):
        """Remember where a finished, locally indexed recording was stopped."""
        pending, playing = self.pending, self.playing or {}
        if not pending or not pending[0].startswith('/dvrfile/') or not pending[2] or len(pending[2]) != 2:
            return
        uuid = pending[0][len('/dvrfile/'):]
        status = read_player_status(max_age=30) or self.last_status
        if not native.UUID.fullmatch(uuid) or not status or 'pos' not in status:
            return
        pos, duration = status['pos'], int(playing.get('duration') or 0) * 1000
        data = load_positions(self.positions_file)
        if pos < RESUME_MIN_MS or (duration and pos > duration - RESUME_END_MS):
            if data.pop(uuid, None) is None:
                return
        else:
            data[uuid] = {'pos': int(pos), 'at': int(time.time())}
        save_positions(data, self.positions_file)
        print('POSITION_SAVED MS=' + str(int(pos)) + ' KEPT=' + str(int(uuid in data)), flush=True)

    def _guide_watch(self, cmd):
        """'Watch now' on a guide programme: live stream of its channel, back to the grid after."""
        m = self.menu
        if cmd != 'OK' or m.page != 'confirm' or not m.items or m.items[m.selection][1] != 'watch':
            return None
        event = m.pending
        stream = '/stream/channel/' + native.identifier(event['channelUuid']) + '?profile=pass'
        m.playback_active = False
        m.back()
        return stream, {'title': event.get('title'), 'start': event.get('start'), 'stop': event.get('stop'),
                        'channelUuid': event['channelUuid']}

    def take_playback(self):
        with self.lock:
            return self.pending

    def live_pause(self):
        """Pause (or Left) pressed while watching the direct live feed: ask the runner to
        switch to the pause buffer at this moment. True when the request was taken."""
        with self.lock:
            if self.pending is None or self.live_mode != 'relay':
                return False
            if not self.live_switch.is_set():
                self.live_switch_at = time.time()
                self.live_switch.set()
            return True

    def live_exit(self):
        """Back/Stop while the live tuner decodes (no player process has the remote)."""
        with self.lock:
            if self.pending is None or self.live_source != 'tuner' or self.live_mode != 'relay':
                return False
            self.live_stop.set()
            return True

    def finish_playback(self):
        with self.lock:
            try:
                self._save_position()
            except Exception as exc:   # never block the return to the menu
                print('POSITION_SAVE_ERROR', type(exc).__name__, flush=True)
            PLAYER_EXTRA_ENV.clear()
            self.pending = None
            self.playing = None
            self.interrupt.clear()
            self.revision += 1
            if self.next_play is not None and not self.standby:
                stream, item = self.next_play
                self.next_play = None
                self._begin(stream, item, False)   # channel change: straight to the next one
                self._player_env()
                return
            self.next_play = None
            self.menu.message = '' if self.standby else 'Playback finished'
            if self.after_play == 'guide' and not self.standby:
                try:
                    self._open_guide()
                except Exception as exc:
                    print('GUIDE_OPEN_ERROR', type(exc).__name__, flush=True)
            self.after_play = None
            self.wake.clear()

    def log_key(self, code, key):
        # Records the JavaScript key values the STB remote produces (first-run discovery).
        with self.lock:
            if self.key_logs >= 200:
                return
            self.key_logs += 1
        print('HTML_KEY CODE=' + str(int(code)) + ' KEY=' + re.sub(r'[^A-Za-z0-9_]', '', str(key))[:24],
              flush=True)


class MenuServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, controller, page, port=PORT):
        self.controller, self.page = controller, page
        super().__init__(('127.0.0.1', port), MenuHandler)
        self.hosts = {f'127.0.0.1:{self.server_address[1]}', f'localhost:{self.server_address[1]}'}


class MenuHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'StbMenu'
    sys_version = ''

    def log_message(self, *args):
        pass

    def send(self, status, body, kind='application/json', cache=False):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'max-age=3600' if cache else 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if kind.startswith('text/html'):
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; "
                             "connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, post):
        # Host check defeats DNS rebinding; the custom header forces a CORS preflight
        # that this server never answers, so other origins cannot issue commands.
        if self.headers.get('Host') not in self.server.hosts:
            self.send(403, {'error': 'host'}); return False
        if post and not secrets.compare_digest(self.headers.get('X-Menu-Token', ''),
                                               self.server.controller.token):
            self.send(403, {'error': 'token'}); return False
        return True

    def do_GET(self):
        if not self.allowed(False):
            return
        if self.path == '/':
            page = self.server.page.replace('__MENU_TOKEN__', self.server.controller.token)
            self.send(200, page.encode(), 'text/html; charset=utf-8')
        elif self.path == '/api/view':
            self.send(200, self.server.controller.view())
        elif self.path.startswith('/api/epg?'):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                start = int(query.get('start', ['0'])[0])
            except ValueError:
                self.send(400, {'error': 'bad request'}); return
            try:
                self.send(200, self.server.controller.guide.window(start))
            except Exception as exc:
                print('GUIDE_ERROR', type(exc).__name__, flush=True)
                self.send(502, {'error': 'guide unavailable'})
        elif self.path in ASSETS:
            name, kind = ASSETS[self.path]
            try:
                self.send(200, Path(__file__).with_name(name).read_bytes(), kind)
            except OSError:
                self.send(404, {'error': 'missing'})
        elif IMG_PATH.match(self.path):
            try:
                data, kind = self.server.controller.guide.image_bytes(IMG_PATH.match(self.path).group(1))
            except Exception:
                self.send(404, {'error': 'no image'})   # page falls back to text
            else:
                self.send(200, data, kind, cache=True)
        elif self.path in FONTS:
            try:
                data = (FONT_DIR/FONTS[self.path]).read_bytes()
            except OSError:
                self.send(404, {'error': 'font missing'})  # page falls back to system fonts
            else:
                self.send(200, data, 'font/ttf')
        else:
            self.send(404, {'error': 'not found'})

    def do_POST(self):
        if not self.allowed(True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError('length')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/command':
                self.send(200, self.server.controller.command(int(body['revision']), str(body['command'])))
            elif self.path == '/api/guide':
                self.send(200, self.server.controller.guide_select(
                    int(body['revision']), str(body['channel']), int(body['event'])))
            elif self.path == '/api/key':
                self.server.controller.log_key(body.get('code', 0), body.get('key', ''))
                self.send(200, {'ok': True})
            elif self.path == '/api/live-pause':
                self.send(200, {'ok': self.server.controller.live_pause()})
            elif self.path == '/api/live-stop':
                self.send(200, {'ok': self.server.controller.live_exit()})
            elif self.path == '/api/live-key':
                self.send(200, self.server.controller.live_key(str(body['key'])))
            elif self.path == '/api/guide-key':
                self.send(200, self.server.controller.guide_key())
            elif self.path == '/api/power':
                self.send(200, self.server.controller.power())
            elif self.path == '/api/guide-record':
                self.send(200, self.server.controller.guide_record(
                    int(body['revision']), str(body['channel']), int(body['event'])))
            else:
                self.send(404, {'error': 'not found'})
        except (ValueError, KeyError, TypeError):
            self.send(400, {'error': 'bad request'})


def stop_group(process, grace=5):
    """Stop a process started with start_new_session=True, including its children."""
    if process is None:
        return None
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
    return process.wait(timeout=5)


class Runner:
    """Owns the screen. With overlay=True Chromium stays up (transparent) above the vendor
    player; otherwise Chromium is stopped for playback and relaunched afterwards."""

    def __init__(self, controller, launch_browser, launch_player, stop, proxy_tokens,
                 stop_browser=stop_group, stop_player=native.terminate, settle=1.0, overlay=False,
                 status_file=None, timeshift=None, launch_index=None, index_dir=None):
        self.c, self.launch_browser, self.launch_player = controller, launch_browser, launch_player
        self.stop, self.tokens = stop, proxy_tokens
        self.stop_browser, self.stop_player, self.settle = stop_browser, stop_player, settle
        self.overlay, self.status_file = overlay, status_file
        self.timeshift = timeshift                     # live TV pause buffer, or None
        self.launch_index = launch_index or follow_index
        self.index_dir = index_dir
        self.browser = None

    def play(self, stream, active, local=None):
        if not self.overlay:
            status = self.stop_browser(self.browser)
            self.browser = None
            print('MENU_HANDOVER_EXIT STATUS=' + str(status), flush=True)
            self.stop.wait(self.settle)  # let nxserver release the browser's graphics/IR clients
        if self.status_file is not None:
            try:
                self.status_file.unlink()   # never show the previous programme's position
            except OSError:
                pass
        token = '/' + secrets.token_hex(24) + '.ts'  # player may pick its input handler by suffix
        player = growing = grown_nav = None
        live = False
        PLAYER_AUDIO[:] = []
        try:
            if local:
                PLAYER_AUDIO[:] = file_audio_args(RECORDINGS/local[0][len(CHROOT_RECORDINGS) + 1:])
            if local and len(local) > 2:
                # Recording in progress: index it as it grows, play it with -timeshift.
                grown_nav = Path(self.index_dir or INDEX_DIR)/local[1].rsplit('/', 1)[-1]
                try:
                    grown_nav.unlink()
                except OSError:
                    pass
                growing = self.launch_index(local[0], local[1])
                ok = wait_for_data(grown_nav, growing, self.stop, 10)
                print('PLAYBACK_GROWING_INDEX=' + str(int(ok)), flush=True)
                player = self.launch_player(local[0], True, local[1] if ok else None)
            elif local:
                player = self.launch_player(local[0], False, local[1])
            else:
                buffered = None
                mode = tuning('live-start', 'tuner')
                if self.timeshift is not None and stream.startswith('/stream/') and mode == 'tuner':
                    player = self._live_tuner(stream)
                    live = player is not None
                if live:
                    pass
                elif (self.timeshift is not None and stream.startswith('/stream/') and LIVE_DIRECT
                        and mode == 'relay'):
                    player = self._live_direct(stream, token)
                    live = player is not None
                if live:
                    pass
                elif self.timeshift is not None and stream.startswith('/stream/'):
                    try:
                        self.timeshift.lead_seconds = float(tuning('live-lead', str(TIMESHIFT_LEAD)))
                    except ValueError:
                        self.timeshift.lead_seconds = TIMESHIFT_LEAD
                    buffered = self.timeshift.start(stream, self.stop)
                if live:
                    pass
                elif buffered:
                    # Live TV from the on-disk pause buffer: pause/rewind/FF via its growing index.
                    live = True
                    PLAYER_AUDIO[:] = audio_args(getattr(self.timeshift, 'streams', []))
                    player = self.launch_player(buffered[0], True, buffered[1])
                elif not self.stop.is_set():
                    self.tokens[token] = stream
                    player = self.launch_player('http://127.0.0.1:9992' + token, active)
            print('PLAYBACK_GROWING_RECORDING=' + str(int(active)) + ' LOCAL_INDEX=' + str(int(bool(local))) +
                  ' TIMESHIFT=' + str(int(live)) + ' AUDIO=' + (' '.join(PLAYER_AUDIO) or 'probe'), flush=True)
            while (player is not None and player.poll() is None and not self.stop.wait(0.2)
                   and not self.c.interrupt.is_set()):
                pass
        finally:
            self.stop_player(player)
            if getattr(self, '_current', None) is not None and self._current is not player:
                self.stop_player(self._current)
            self._current = None
            self.c.live_mode = None
            self.c.live_source = None
            self.c.live_switch.clear()
            self.c.live_stop.clear()
            self.tokens.pop(token, None)
            PLAYER_AUDIO[:] = []
            if growing is not None:
                stop_group(growing, grace=2)
            if grown_nav is not None:
                try:
                    grown_nav.unlink()
                except OSError:
                    pass
            if self.timeshift is not None and stream.startswith("/stream/"):
                self.timeshift.stop()
            print('PLAYBACK_EXIT STATUS=' + str(player.returncode if player else 'none'), flush=True)
            self.c.finish_playback()

    def _launch(self, *args):
        self._current = self.launch_player(*args)
        return self._current

    def _relay(self, stream, token):
        self.tokens[token] = stream
        self.c.live_mode = 'relay'
        return self._launch('http://127.0.0.1:9992' + token, False)

    def _live_tuner(self, stream):
        """Live TV on the dedicated live tuner: the tuner manager decodes the channel straight
        to the screen (tuner 4) and streams the same service, which becomes the pause buffer.
        Pause/Left switches to the buffer (manager decoders off, vendor player on, paused);
        playing back within LIVE_RETURN_MS of live switches back to the tuner. Returns the
        last player (or a finished stand-in), or None when the live tuner is unavailable."""
        ts, c = self.timeshift, self.c
        source = live_tuner_url(ts.api, stream)
        if source is None or not ts.begin(stream, source=source):
            return None
        deadline = time.monotonic() + 8
        while ts.written < 188 * 64 and time.monotonic() < deadline and not self.stop.is_set():
            if ts.writer is None or not ts.writer.is_alive():
                break
            self.stop.wait(0.1)
        if ts.written < 188 * 64:
            print('LIVE_TUNER_UNAVAILABLE BYTES=' + str(ts.written), flush=True)
            ts.stop()
            return None
        full = audio_args(ts.wait_streams(self.stop, 3))
        c.live_switch.clear()
        c.live_stop.clear()
        c.live_mode, c.live_source = 'relay', 'tuner'
        print('LIVE_TUNER SOURCE=' + source.rsplit('/live/', 1)[-1], flush=True)
        player, near, was_paused, resumed = None, 0, False, False
        while not self.stop.wait(0.2):
            if ts.writer is None or not ts.writer.is_alive():
                print('LIVE_TUNER_STREAM_ENDED', flush=True)
                break
            if c.interrupt.is_set():
                break   # channel change or standby
            if c.live_mode == 'relay':
                if c.live_stop.is_set():
                    break
                if not c.live_switch.is_set():
                    continue
                start = ts.position_ms(c.live_switch_at) - LIVE_SWITCH_BACK_MS
                if not ts.index_ready() or start <= 0:
                    continue
                c.live_switch.clear()
                manager_decode(False)
                if self.status_file is not None:
                    try:
                        self.status_file.unlink()
                    except OSError:
                        pass
                PLAYER_EXTRA_ENV['PLAYER_START_MS'] = str(start)
                PLAYER_EXTRA_ENV['PLAYER_START_PAUSED'] = '1'
                # Play, then pause once a picture is up (-startPaused showed black).
                PLAYER_EXTRA_ENV['PLAYER_PAUSE_AFTER_START'] = '1'
                PLAYER_AUDIO[:] = full
                try:
                    player = self._launch(ts.chroot_dir + '/live.ts', True, ts.chroot_dir + '/live.nav')
                finally:
                    PLAYER_AUDIO[:] = full
                    PLAYER_EXTRA_ENV.pop('PLAYER_START_MS', None)
                    PLAYER_EXTRA_ENV.pop('PLAYER_START_PAUSED', None)
                    PLAYER_EXTRA_ENV.pop('PLAYER_PAUSE_AFTER_START', None)
                c.live_mode, near, was_paused = 'buffer', 0, False
                print('LIVE_SWITCH TO=BUFFER START_MS=' + str(start), flush=True)
            else:
                if player.poll() is not None:
                    break   # Back/Stop in the player ends live TV
                st = read_player_status(self.status_file) if self.status_file is not None else None
                lag = ts.position_ms() - st.get('pos', 0) if st else None
                go_live = False
                if st and st.get('state') == 2:
                    was_paused, resumed = True, False
                elif st and was_paused and not resumed and st.get('state') == 1:
                    # Play after a pause. The buffer only plays smoothly about 10 s or more
                    # behind live (13 Nov 2026), so a short pause resumes at live instead.
                    resumed = True
                    go_live = lag is not None and lag < LIVE_MIN_LAG_MS
                fast = bool(st) and st.get('rate', 1000) > 1000
                # Otherwise back to live only when FF reaches the live edge (or playback
                # drifts within 2.5 s of it), and never before the viewer has paused.
                close = lag is not None and lag < (LIVE_RETURN_MS if fast else 2500)
                near = near + 1 if was_paused and st and st.get('state') == 1 and close else 0
                if go_live or near >= 3:
                    self.stop_player(player)
                    player = None
                    manager_decode(True)
                    c.live_mode, near = 'relay', 0
                    print('LIVE_SWITCH TO=TUNER', flush=True)
        if player is None:
            player = _Finished()
        return player

    def _live_direct(self, stream, token):
        """Instant live TV, as the stock UI: watch the live feed through the relay while
        the pause buffer records alongside. Pause/Left (reported by the page) switches to
        the buffer, paused where it was pressed; catching up to within LIVE_RETURN_MS of
        live while playing switches back to the live feed. Returns the last player, or None
        when the buffer could not start (the caller then uses the old paths)."""
        ts, c = self.timeshift, self.c
        if not ts.begin(stream):
            return None
        full = audio_args(ts.wait_streams(self.stop, 3))
        relay_audio = relay_audio_args(full) + relay_options()
        PLAYER_AUDIO[:] = relay_audio
        c.live_switch.clear()
        player = self._relay(stream, token)
        print('LIVE_DIRECT AUDIO=' + (' '.join(PLAYER_AUDIO) or 'probe'), flush=True)
        near, was_paused = 0, False
        while player.poll() is None and not self.stop.wait(0.2) and not c.interrupt.is_set():
            if c.live_mode == 'relay' and c.live_switch.is_set():
                start = ts.position_ms(c.live_switch_at) - LIVE_SWITCH_BACK_MS
                if not ts.index_ready() or start <= 0:
                    continue   # buffer index not there yet: switch as soon as it is
                c.live_switch.clear()
                self.stop_player(player)
                self.tokens.pop(token, None)
                if self.status_file is not None:
                    try:
                        self.status_file.unlink()
                    except OSError:
                        pass
                PLAYER_EXTRA_ENV['PLAYER_START_MS'] = str(start)
                PLAYER_EXTRA_ENV['PLAYER_START_PAUSED'] = '1'
                # Play, then pause once a picture is up (-startPaused showed black).
                PLAYER_EXTRA_ENV['PLAYER_PAUSE_AFTER_START'] = '1'
                PLAYER_AUDIO[:] = full
                try:
                    player = self._launch(ts.chroot_dir + '/live.ts', True, ts.chroot_dir + '/live.nav')
                finally:
                    PLAYER_AUDIO[:] = full
                    PLAYER_EXTRA_ENV.pop('PLAYER_START_MS', None)
                    PLAYER_EXTRA_ENV.pop('PLAYER_START_PAUSED', None)
                    PLAYER_EXTRA_ENV.pop('PLAYER_PAUSE_AFTER_START', None)
                c.live_mode, near, was_paused = 'buffer', 0, False
                print('LIVE_SWITCH TO=BUFFER START_MS=' + str(start), flush=True)
            elif c.live_mode == 'buffer':
                st = read_player_status(self.status_file) if self.status_file is not None else None
                playing = st and st.get('state') == 1 and st.get('rate') == 1000
                if st and st.get('state') == 2:
                    was_paused = True
                # Only after the viewer has really paused: a buffer that failed to pause must not
                # bounce straight back to live (14 Nov 2026).
                near = near + 1 if was_paused and playing and ts.position_ms() - st.get('pos', 0) < LIVE_RETURN_MS else 0
                if near >= 5:   # about a second at the live edge: back to the live feed
                    self.stop_player(player)
                    PLAYER_AUDIO[:] = relay_audio
                    player = self._relay(stream, token)
                    near = 0
                    print('LIVE_SWITCH TO=LIVE', flush=True)
        return player

    def run(self):
        crashes = []
        try:
            self.browser = self.launch_browser()
            print('BROWSER_STARTED', flush=True)
            while not self.stop.is_set():
                self.c.wake.wait(0.2)
                pending = self.c.take_playback()
                if pending:
                    self.play(*pending)
                    if self.stop.is_set():
                        break
                    if self.browser is None:
                        self.browser = self.launch_browser()
                        print('BROWSER_STARTED AFTER_PLAYBACK', flush=True)
                elif self.browser.poll() is not None:
                    print('BROWSER_EXIT STATUS=' + str(self.browser.returncode), flush=True)
                    now = time.monotonic()
                    crashes = [t for t in crashes if now - t < 120] + [now]
                    if len(crashes) > 3:
                        raise RuntimeError('Chromium exited repeatedly; inspect log')
                    if self.stop.wait(2):
                        break
                    self.browser = self.launch_browser()
                    print('BROWSER_STARTED AFTER_EXIT', flush=True)
        finally:
            self.stop_browser(self.browser)
            self.browser = None


def default_route(path='/proc/net/route'):
    """True when some interface has a default route (Ethernet or Wi-Fi)."""
    try:
        lines = Path(path).read_text().splitlines()[1:]
    except OSError:
        return False
    return any(len(f) > 3 and f[1] == '00000000' and int(f[3], 16) & 1 for f in (x.split() for x in lines))


class LedMonitor:
    """Recording LED while Tvheadend records, Internet LED while there is a default route."""

    def __init__(self, stop, api, led, interval=20, route=default_route):
        self.stop, self.api, self.led, self.interval, self.route = stop, api, led, interval, route
        self.state = {}

    def check(self):
        try:
            recording = any(r.get('sched_status') == 'recording' for r in self.api.rows('dvr/entry/grid_upcoming'))
        except Exception:
            recording = self.state.get('recording') == 'on'
        for name, value in (('recording', 'on' if recording else 'off'),
                            ('internet', 'on' if self.route() else 'off')):
            if self.state.get(name) != value:
                self.state[name] = value
                self.led(name, value)

    def run(self):
        self.check()
        while not self.stop.wait(self.interval):
            self.check()


class ChrootMounts:
    """The same /dev/null, /proc and /dev/shm provisioning the Chromium probe used."""

    def __init__(self, root=ROOT, recordings=RECORDINGS, index=INDEX_DIR, timeshift=None):
        self.root, self.undo = Path(root), []
        self.recordings, self.index = recordings, index
        self.timeshift = TIMESHIFT_DIR if timeshift is None else timeshift

    def setup(self):
        null = self.root/'dev/null'
        if not null.exists() and not null.is_symlink():
            os.mknod(null, stat.S_IFCHR | 0o666, os.stat('/dev/null').st_rdev)
            os.chmod(null, 0o666)
            self.undo.append(('rm', null))
        info = os.lstat(null)
        if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.stat('/dev/null').st_rdev:
            raise RuntimeError('Unexpected device at ' + str(null))
        for path in (self.root/'proc', self.root/'dev/shm'):
            path.mkdir(parents=True, exist_ok=True)
        if not (self.root/'proc/self/status').exists():
            subprocess.run(['mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc',
                            str(self.root/'proc')], check=True)
            self.undo.append(('umount', self.root/'proc'))
        fs = subprocess.run(['stat', '-f', '-c', '%T', str(self.root/'dev/shm')],
                            capture_output=True, text=True, check=True).stdout.strip()
        if fs != 'tmpfs':
            subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=64m,mode=1777,nosuid,nodev,noexec', 'tmpfs',
                            str(self.root/'dev/shm')], check=True)
            self.undo.append(('umount', self.root/'dev/shm'))
        # Recordings (read-only) and their NAV indexes, for indexed local playback.
        if self.recordings is not None:
            self.index.mkdir(parents=True, exist_ok=True)
            for source, inside, readonly in ((self.recordings, CHROOT_RECORDINGS, True),
                                             (self.index, CHROOT_INDEX, False)):
                target = self.root/inside.lstrip('/')
                target.mkdir(parents=True, exist_ok=True)
                if not source.is_dir() or os.path.ismount(target):
                    continue
                subprocess.run(['mount', '--bind', str(source), str(target)], check=True)
                self.undo.append(('umount', target))
                if readonly:
                    subprocess.run(['mount', '-o', 'remount,bind,ro', str(target)], check=True)
        # Live TV pause buffer (read-write), only when the recording disk is mounted.
        if self.timeshift and self.timeshift.parent.is_dir():
            self.timeshift.mkdir(exist_ok=True)
            target = self.root/CHROOT_TIMESHIFT.lstrip('/')
            target.mkdir(parents=True, exist_ok=True)
            if not os.path.ismount(target):
                subprocess.run(['mount', '--bind', str(self.timeshift), str(target)], check=True)
                self.undo.append(('umount', target))

    def cleanup(self):
        while self.undo:
            action, path = self.undo.pop()
            if action == 'rm':
                path.unlink(missing_ok=True)
            elif subprocess.run(['umount', str(path)]).returncode:
                print('CHROOT_UMOUNT_FAILED ' + str(path), flush=True)


class Indexer:
    """Builds NAV indexes for finished recordings in the background with the vendor
    indexer (mkindex), one at a time at low priority, and never during playback."""

    def __init__(self, stop, controller=None, run_index=None, settle_seconds=180, interval=60):
        self.stop, self.c = stop, controller
        self.run_index = run_index or self.mkindex
        self.settle_seconds, self.interval = settle_seconds, interval
        self.failed = set()

    @staticmethod
    def mkindex(ts, nav_tmp):
        rel = ts.relative_to(RECORDINGS).as_posix()
        return subprocess.run(['nice', '-n', '10', 'chroot', str(ROOT), '/lib/ld-linux-armhf.so.3',
            '--library-path', '/opt/playback-test/lib:/lib', MKINDEX,
            CHROOT_RECORDINGS + '/' + rel, CHROOT_INDEX + '/' + nav_tmp.name],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3600,
            env={k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')})

    def busy(self):
        return self.c is not None and self.c.take_playback() is not None

    def pending(self):
        now = time.time()
        todo = []
        for ts in sorted(RECORDINGS.rglob('*.ts')):
            try:
                st = ts.stat()
                nav = INDEX_DIR/nav_name(ts)
                if now - st.st_mtime < self.settle_seconds or st.st_size < 1 << 20:
                    continue   # probably still recording, or too small to matter
                if (str(ts), st.st_mtime) in self.failed:
                    continue
                if nav.is_file() and nav.stat().st_mtime >= st.st_mtime:
                    continue
                todo.append(ts)
            except (OSError, ValueError):
                continue
        return todo

    def index_one(self, ts):
        nav = INDEX_DIR/nav_name(ts)
        tmp = nav.with_name(nav.name + '.tmp')
        started = time.monotonic()
        try:
            result = self.run_index(ts, tmp)
            ok = result.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
        except (OSError, subprocess.SubprocessError) as exc:
            ok, result = False, exc
        if ok:
            os.replace(tmp, nav)
            print(f'INDEX_DONE SECONDS={time.monotonic() - started:.0f} NAV={nav.name}', flush=True)
        else:
            tmp.unlink(missing_ok=True)
            self.failed.add((str(ts), ts.stat().st_mtime))
            detail = getattr(result, 'stdout', '') or str(result)
            print('INDEX_FAILED NAV=' + nav.name + ' ' + CONTROL.sub(' ', str(detail))[:200], flush=True)
        return ok

    def run(self):
        if not os.access(str(ROOT) + MKINDEX, os.X_OK):
            print('INDEX_DISABLED mkindex not installed', flush=True)
            return
        while not self.stop.wait(15):
            try:
                for ts in self.pending():
                    if self.stop.is_set():
                        return
                    while self.busy() and not self.stop.wait(5):
                        pass
                    self.index_one(ts)
            except Exception as exc:
                print('INDEX_ERROR', type(exc).__name__, flush=True)
            if self.stop.wait(self.interval):
                return


# ---- Live TV timeshift (pause / rewind / FF) ----
# Live TV is written to disk from Tvheadend and indexed as it grows by `mkindex -follow`;
# the vendor player then plays the local file with its NAV index and -timeshift ("wait at
# end of file for more data"), exactly as indexed recordings play. Nothing is kept: the
# buffer is deleted when playback ends or the channel changes.
TIMESHIFT_DIR = Path('/mnt/recording-disk/phoenix-pvr/timeshift')
CHROOT_TIMESHIFT = '/media/timeshift'
TIMESHIFT_LEAD = 10                # seconds behind live when live TV starts (LIVE_DIRECT off)
# Instant live TV (stock UI behaviour): watch the live feed, use the buffer only after Pause.
LIVE_DIRECT = True   # allowed, but only used when /data/native-frontend/live-start says 'relay':
# the direct feed's sound comes and goes (vendor player network mode, 13 Nov 2026), so live
# TV starts on the buffer TIMESHIFT_LEAD s behind live by default.
LIVE_SWITCH_BACK_MS = 1500         # paused picture this far before the moment Pause was pressed
LIVE_RETURN_MS = 5000              # back to the live feed when playing this close to live
LIVE_MIN_LAG_MS = 10000            # Play after a shorter pause than this resumes at live (tuner)
TIMESHIFT_MAX_BYTES = 24 << 30    # safety stop, only if the disk cannot free old data
# Rolling window, as the stock UI (pltv BUFFER_LENGTH = 4 h): data older than this is freed
# with fallocate(PUNCH_HOLE) so disk use stays at about 4 h of TV (~8 GB HD) however long the
# box is left on. File offsets do not change, so the player and its index keep working.
TIMESHIFT_KEEP_SECONDS = 4 * 3600
TIMESHIFT_PUNCH_STEP = 64 << 20   # free old data in steps of this size


def punch_hole(fd, length):
    """Free the first `length` bytes of a file without changing its size or offsets.
    Returns True on success (XFS supports it; tmpfs/test systems may not)."""
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fallocate = libc.fallocate
    except Exception:
        return False
    fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong]
    return fallocate(fd, 0x03, 0, length) == 0   # FALLOC_FL_KEEP_SIZE | FALLOC_FL_PUNCH_HOLE
STREAM_TYPES = {0x02: 'mpeg2', 0x1b: 'h264', 0x24: 'h265', 0x03: 'mpeg', 0x04: 'mpeg', 0x0f: 'aac',
                0x11: 'aacplus', 0x81: 'ac3', 0x87: 'ac3plus'}
AUDIO_DESCRIPTORS = {0x6a: 'ac3', 0x7a: 'ac3plus', 0x7c: 'aacplus'}   # DVB private (type 0x06) audio


def stream_pids(data):
    """Elementary streams [(pid, kind)] from the first PAT/PMT in TS bytes, for the log."""
    pmt, found = None, []
    for i in range(0, len(data) - 187, 188):
        p = data[i:i + 188]
        if p[0] != 0x47 or not p[1] & 0x40:
            continue
        pid = (p[1] & 0x1f) << 8 | p[2]
        off = 4 + (1 + p[4] if p[3] & 0x20 else 0)
        if off >= 188:
            continue
        off += 1 + p[off]
        if off + 12 > 188:
            continue
        end = min(188, off + 3 + ((p[off + 1] & 0x0f) << 8 | p[off + 2]) - 4)
        if pid == 0 and p[off] == 0 and pmt is None:
            for j in range(off + 8, end - 3, 4):
                if p[j] << 8 | p[j + 1]:
                    pmt = (p[j + 2] & 0x1f) << 8 | p[j + 3]
                    break
        elif pmt is not None and pid == pmt and p[off] == 2:
            j = off + 12 + ((p[off + 10] & 0x0f) << 8 | p[off + 11])
            while j + 5 <= end:
                kind, es = STREAM_TYPES.get(p[j], 'type%02x' % p[j]), (p[j + 1] & 0x1f) << 8 | p[j + 2]
                info = (p[j + 3] & 0x0f) << 8 | p[j + 4]
                described = False
                d = j + 5
                while d + 2 <= min(end, j + 5 + info):
                    if p[j] == 0x06 and p[d] in AUDIO_DESCRIPTORS:
                        kind = AUDIO_DESCRIPTORS[p[d]]
                    elif p[j] == 0x06 and p[d] == 0x56:
                        kind = 'teletext'
                    elif p[d] == 0x0a and p[d + 1] >= 4 and d + 6 <= end and p[d + 5] in (2, 3):
                        described = True   # ISO 639 audio_type: audio description track
                    d += 2 + p[d + 1]
                if described:
                    kind += '-ad'
                found.append((es, kind))
                j += 5 + info
            return found
    return found


# The box's HDMI audio plays AC3 (passed to the TV) and AAC/HE-AAC, but MPEG Layer 2 gave
# silence (ABC, verified 12 Nov 2026), and the player's probe picks the first track, which on
# ABC is MPEG. So name the track explicitly: first match in this order.
AUDIO_PREFERENCE = ('ac3plus', 'ac3', 'aacplus', 'aac', 'mpeg')   # '-ad' tracks never chosen


def audio_args(streams):
    """['-audio', pid, '-audio_type', name] for the preferred audio track, or [] to let the
    player's probe decide (no preferred track found)."""
    for kind in AUDIO_PREFERENCE:
        for pid, found in streams:
            if found == kind:
                return ['-audio', str(pid), '-audio_type', kind]
    return []


# Track choice on the direct live feed (13 Nov 2026: every channel was silent there with
# -audio/-audio_type, while the same choice works on the buffer). The file
# /data/native-frontend/live-audio may say 'full' (default), 'pid' (-audio only) or 'off'
# (player's own probe), so each can be tried on the box without a reinstall.
def tuning(name, default):
    """Box-side test switch: the first word of /data/native-frontend/<name>, else default.
    live-start: tuner (default) | buffer | relay   live-lead: seconds   eos-gap: ms (see player-status.c)"""
    try:
        return ((BASE/name).read_text().split() or [default])[0].lower()
    except OSError:
        return default


RELAY_OPTION = re.compile(r'^-[a-z_]+$|^[a-z0-9_.,]+$')


def relay_options():
    """Extra player options for the direct live feed from /data/native-frontend/relay-opts
    (box-side test switch, e.g. '-pacing'); only simple words are passed through."""
    try:
        words = (BASE/'relay-opts').read_text().split()
    except OSError:
        return []
    return [w for w in words[:8] if RELAY_OPTION.match(w)]


MANAGER = 'http://127.0.0.1:9990'
MANAGER_URL = re.compile(r':9990/stream/(\d{6,10})/(\d{1,5})$')
# Region-free muxes carry a whole multiplex; the service ID then comes from Tvheadend.
MANAGER_MUX_URL = re.compile(r':9990/mux/(\d{6,10})(?:/([678]))?(?:\?.*)?$')
_TUNER_MAP = {'at': 0, 'map': {}}


class _Finished:
    """Stands in for a player when live TV ended without one (live tuner only)."""
    returncode = 0

    def poll(self):
        return 0


def _manager_open(url, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=timeout)


def manager_led(name, state):
    """Set a front-panel LED through the tuner manager (it owns the vendor GPIO helper)."""
    try:
        with _manager_open(MANAGER + '/led/' + name + '/' + state, 4) as r:
            print('LED ' + name.upper() + '=' + state.upper() + ' ' +
                  r.read(32).decode('ascii', 'replace').strip(), flush=True)
    except Exception as exc:
        print('LED_ERROR ' + name.upper() + ' ' + type(exc).__name__, flush=True)


def manager_decode(on):
    """Tell the tuner manager to start/stop decoding the live tuner; waits for it."""
    try:
        with _manager_open(MANAGER + ('/decode/on' if on else '/decode/off'), 6) as r:
            print('LIVE_DECODE ' + ('ON ' if on else 'OFF ') + r.read(64).decode('ascii', 'replace').strip(),
                  flush=True)
    except Exception as exc:
        print('LIVE_DECODE_ERROR', type(exc).__name__, flush=True)


def live_tuner_url(api, stream):
    """Manager /live URL for a Tvheadend /stream/channel/<uuid> path, from the channel's
    service -> mux IPTV URL (http://127.0.0.1:9990/stream/<frequency>/<sid>). None when the
    channel is not on the tuner manager. The mapping is cached for 10 minutes."""
    match = re.match(r'^/stream/channel/([0-9a-f]{32})', stream)
    if not match:
        return None
    if time.time() - _TUNER_MAP['at'] > 600 or match.group(1) not in _TUNER_MAP['map']:
        try:
            muxes = {}
            for mux in api.rows('mpegts/mux/grid'):
                for value in mux.values():
                    if isinstance(value, str):
                        m = MANAGER_URL.search(value)
                        if m:
                            muxes[mux.get('uuid')] = (m.group(1), m.group(2), None)
                            break
                        m = MANAGER_MUX_URL.search(value)
                        if m:
                            muxes[mux.get('uuid')] = (m.group(1), None, m.group(2) or '7')
                            break
            services = {}
            for svc in api.rows('mpegts/service/grid'):
                mux = muxes.get(svc.get('multiplex_uuid') or svc.get('mux_uuid') or svc.get('multiplex'))
                if mux and mux[1] is None:
                    sid = svc.get('sid')
                    mux = (mux[0], str(sid), mux[2]) if isinstance(sid, int) and 0 < sid < 65536 else None
                if mux:
                    services[svc.get('uuid')] = mux
            mapping = {}
            for ch in api.rows('channel/grid'):
                for svc in ch.get('services') or []:
                    if svc in services:
                        mapping[ch.get('uuid')] = services[svc]
                        break
            _TUNER_MAP.update(at=time.time(), map=mapping)
            print('LIVE_TUNER_MAP CHANNELS=' + str(len(mapping)), flush=True)
        except Exception as exc:
            print('LIVE_TUNER_MAP_ERROR', type(exc).__name__, flush=True)
            return None
    found = _TUNER_MAP['map'].get(match.group(1))
    return MANAGER + '/live/' + found[0] + '/' + found[1] + ('/' + found[2] if found[2] else '') if found else None


def relay_audio_args(full, path=None):
    try:
        mode = (path or BASE/'live-audio').read_text().strip().lower()
    except OSError:
        mode = 'full'
    if mode == 'off':
        return []
    if mode == 'pid':
        return full[:2]
    return list(full)


def file_audio_args(path, size=4 << 20):
    try:
        with open(path, 'rb') as f:
            return audio_args(stream_pids(f.read(size)))
    except OSError:
        return []


def follow_index(ts, nav):
    """Start `mkindex -follow ts nav` (chroot paths) in its own process group."""
    log = open(BASE/'mkindex-follow.log', 'ab')
    try:
        return subprocess.Popen(['nice', '-n', '5', 'chroot', str(ROOT), '/lib/ld-linux-armhf.so.3',
            '--library-path', '/opt/playback-test/lib:/lib', MKINDEX, '-follow', ts, nav],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
            env={k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')})
    finally:
        log.close()


def wait_for_data(path, process, stop, seconds):
    """Wait until path has data, the process exits or stop is set. True when data arrived."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stop.is_set():
        try:
            if path.stat().st_size > 0:
                return True
        except OSError:
            pass
        if process is not None and process.poll() is not None:
            return False
        stop.wait(0.1)
    return False


class Timeshift:
    """On-disk pause buffer for one live channel at a time."""

    def __init__(self, api, folder=TIMESHIFT_DIR, launch_index=None, ready_bytes=2 << 20, timeout=20,
                 max_bytes=TIMESHIFT_MAX_BYTES, chroot_dir=CHROOT_TIMESHIFT, lead_seconds=TIMESHIFT_LEAD,
                 keep_seconds=TIMESHIFT_KEEP_SECONDS, punch=punch_hole):
        self.api, self.folder, self.chroot_dir = api, Path(folder), chroot_dir
        self.launch_index = launch_index or follow_index
        self.ready_bytes, self.timeout, self.max_bytes = ready_bytes, timeout, max_bytes
        self.lead_seconds = lead_seconds
        self.keep_seconds, self.punch = keep_seconds, punch
        self.halt = threading.Event()
        self.writer = self.indexer = None
        self.buffering = False   # True while live TV waits for its lead (page shows Please wait)
        self.started_at = None
        self.freed = 0           # bytes at the start of the file already given back to the disk
        self.written = 0
        self.error = None

    def buffered(self):
        """Seconds of live TV since the buffer started, for the overlay; None when not running."""
        at = self.started_at
        return None if at is None else max(0, int(time.time() - at))

    def buffer_from(self):
        """Earliest second still held (rolling window), on the same scale as buffered()."""
        total = self.buffered()
        return None if total is None else max(0, total - self.keep_seconds)

    def _trim(self, out, marks):
        """Free data older than keep_seconds. marks: [(time, bytes written)], oldest first."""
        limit = time.time() - self.keep_seconds
        upto = 0
        while marks and marks[0][0] < limit:
            upto = marks.pop(0)[1]
        upto -= upto % TIMESHIFT_PUNCH_STEP
        if upto > self.freed:
            if self.punch(out.fileno(), upto):
                self.freed = upto
                print('TIMESHIFT_TRIMMED FREED=' + str(upto), flush=True)
            else:
                print('TIMESHIFT_TRIM_UNSUPPORTED', flush=True)
                self.punch = lambda fd, length: False

    def _record(self, stream, ts, halt, source=None):
        try:
            marks, mark_at = [], 0
            upstream_cm = _manager_open(source) if source else self.api.open(stream)
            with upstream_cm as upstream, open(ts, 'wb') as out:
                while not halt.is_set():
                    data = upstream.read(188 * 256)
                    if not data:
                        print('TIMESHIFT_UPSTREAM_ENDED', flush=True)
                        break
                    if self.started_at is None:
                        self.started_at = time.time()
                    out.write(data)
                    out.flush()
                    self.written += len(data)
                    if self.written - mark_at >= TIMESHIFT_PUNCH_STEP:
                        mark_at = self.written
                        marks.append((time.time(), self.written))
                        self._trim(out, marks)
                    if self.written - self.freed >= self.max_bytes:
                        print('TIMESHIFT_FULL BYTES=' + str(self.written), flush=True)
                        break
        except Exception as exc:
            self.error = exc
            print('TIMESHIFT_RECORD_ERROR', type(exc).__name__, 'HTTP=' + str(getattr(exc, 'code', 'none')),
                  flush=True)

    def start(self, stream, stop):
        """Start buffering a /stream/ path. Returns (ts, nav or None) chroot paths once enough
        is on disk, or None if the stream did not start (the caller falls back to the relay)."""
        self.stop()
        if not self.folder.is_dir():
            print('TIMESHIFT_UNAVAILABLE no buffer folder', flush=True)
            return None
        ts, nav = self.folder/'live.ts', self.folder/'live.nav'
        self._remove()
        self.halt = threading.Event()
        self.written, self.error, self.started_at, self.freed = 0, None, None, 0
        self.streams = []
        self.buffering = True
        self.writer = threading.Thread(target=self._record, args=(stream, ts, self.halt), daemon=True)
        self.writer.start()
        deadline = time.monotonic() + self.timeout
        while (self.written < self.ready_bytes and self.writer.is_alive() and not stop.is_set()
               and time.monotonic() < deadline):
            stop.wait(0.1)
        if self.written < self.ready_bytes:
            self.buffering = False
            print('TIMESHIFT_NOT_READY BYTES=' + str(self.written), flush=True)
            self.stop()
            return None
        try:
            with open(ts, 'rb') as f:
                streams = stream_pids(f.read(self.ready_bytes))
            self.streams = streams
            print('TIMESHIFT_STREAMS ' + ' '.join('0x%x:%s' % s for s in streams), flush=True)
        except OSError:
            pass
        index = None
        try:
            self.indexer = self.launch_index(self.chroot_dir + '/live.ts', self.chroot_dir + '/live.nav')
            if wait_for_data(nav, self.indexer, stop, 10):
                index = self.chroot_dir + '/live.nav'
        except OSError as exc:
            print('TIMESHIFT_INDEX_ERROR', type(exc).__name__, flush=True)
        # Start this far behind live. At the end of a growing file the vendor player only
        # retrieves again once about 4 s of new data has arrived (the DVR, 12 Nov 2026: fed
        # bytes rose in ~1.8 MB steps every 4-5 s); a start only ~5 s behind ran dry before
        # each retrieve and stuttered every ~4 s.
        until = (self.started_at or time.time()) + self.lead_seconds
        while time.time() < until and self.writer.is_alive() and not stop.is_set():
            stop.wait(0.1)
        self.buffering = False
        print('TIMESHIFT_READY BYTES=' + str(self.written) + ' INDEX=' + str(int(index is not None)) +
              ' LEAD=' + str(self.buffered()), flush=True)
        return self.chroot_dir + '/live.ts', index

    def begin(self, stream, source=None):
        """Start the buffer without waiting (instant live TV). The follow indexer starts at
        once and waits for the file itself. False when there is no buffer folder."""
        self.stop()
        if not self.folder.is_dir():
            print('TIMESHIFT_UNAVAILABLE no buffer folder', flush=True)
            return False
        self._remove()
        self.halt = threading.Event()
        self.written, self.error, self.started_at, self.freed = 0, None, None, 0
        self.streams, self.buffering = [], False
        self.writer = threading.Thread(target=self._record,
                                       args=(stream, self.folder/'live.ts', self.halt, source), daemon=True)
        self.writer.start()
        try:
            self.indexer = self.launch_index(self.chroot_dir + '/live.ts', self.chroot_dir + '/live.nav')
        except OSError as exc:
            print('TIMESHIFT_INDEX_ERROR', type(exc).__name__, flush=True)
        print('TIMESHIFT_BEGIN', flush=True)
        return True

    def wait_streams(self, stop, seconds):
        """Elementary streams from the buffer's first PMT, waiting up to `seconds`."""
        deadline = time.monotonic() + seconds
        while not stop.is_set() and time.monotonic() < deadline:
            if self.written >= 64 << 10:
                try:
                    with open(self.folder/'live.ts', 'rb') as f:
                        streams = stream_pids(f.read(min(self.written, 2 << 20)))
                except OSError:
                    streams = []
                if any(k in AUDIO_PREFERENCE or k == 'mpeg' for _, k in streams):
                    self.streams = streams
                    break
            if self.writer is None or not self.writer.is_alive():
                break
            stop.wait(0.1)
        print('TIMESHIFT_STREAMS ' + ' '.join('0x%x:%s' % x for x in self.streams), flush=True)
        return self.streams

    def position_ms(self, at=None):
        """Player position (ms from the start of the buffer) matching wall-clock time `at`."""
        if self.started_at is None:
            return 0
        return int(((at or time.time()) - self.started_at) * 1000)

    def index_ready(self):
        try:
            return (self.folder/'live.nav').stat().st_size > 0
        except OSError:
            return False

    def _remove(self):
        for name in ('live.ts', 'live.nav'):
            try:
                (self.folder/name).unlink()
            except OSError:
                pass

    def stop(self):
        self.halt.set()
        if self.indexer is not None:
            stop_group(self.indexer, grace=2)
            self.indexer = None
        if self.writer is not None:
            self.writer.join(15)
            self.writer = None
        if self.started_at is not None:
            print('TIMESHIFT_STOPPED BYTES=' + str(self.written), flush=True)
        self.started_at = None
        self._remove()


def browser_launcher(probe, port=PORT):
    if not PROBE_DIR.fullmatch(probe):
        raise ValueError('Expected /opt/chromium')
    binary = probe + '/bin/' + select_browser(Path(str(ROOT) + probe)).name
    if not os.access(str(ROOT) + binary, os.X_OK):
        raise ValueError('Chromium binary missing at ' + binary)
    inside = probe + '/html-menu'
    work = Path(str(ROOT) + inside)
    work.mkdir(mode=0o700, exist_ok=True)
    (work/'fonts.conf').write_text(
        f'<?xml version="1.0"?>\n<fontconfig>\n <dir>{probe}/share/fonts</dir>\n'
        f' <cachedir>{inside}/font-cache</cachedir>\n</fontconfig>\n')
    base_env = {k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')}
    env = dict(base_env, B_REFSW_BOXMODE='2', LD_LIBRARY_PATH=f'{probe}/lib:/lib:/opt/blit/lib:/opt/tuner-libs',
               LIBV3D_PATH=f'{probe}/lib/libv3ddriver.so', FONTCONFIG_FILE=f'{inside}/fonts.conf',
               XDG_CONFIG_HOME=f'{inside}/xdg', HOME=inside,
               WEBKITGL_KEYMAP_FILE_PATH=f'{probe}/bin/irkeymap/',
               # As the stock webkitGl39/start script: full 1280x720 window with no 30x20
               # safe-area margin, and transparent page backgrounds so the vendor player's
               # video can show through an overlay (verified on the DVR, 11 Nov 2026).
               WEBKITGL_TRANSPARENT_BACKGROUND='y', WEBKITGL_CHROME_MARGIN_X='0',
               WEBKITGL_CHROME_MARGIN_Y='0')

    def launch():
        # Fresh profile each launch: a killed browser must not offer session restore.
        shutil.rmtree(work/'profile', ignore_errors=True)
        # The vendor ChromeOS build maps login-profile=phoenix to u-phoenix.
        profile = work/'profile'/'u-phoenix'
        profile.mkdir(parents=True, mode=0o700)
        (work/'profile'/'Local State').write_text(json.dumps({'profile': {
            'last_used': 'u-phoenix', 'last_active_profiles': ['u-phoenix'],
            'info_cache': {'u-phoenix': {'name': 'phoenix', 'is_using_default_name': False}}
        }}))
        (profile/'Preferences').write_text(json.dumps({'profile': {'name': 'phoenix'}}))
        log = open(BASE/'chromium.log', 'ab')
        try:
            return subprocess.Popen(
                ['chroot', str(ROOT), binary, '--kiosk', '--no-sandbox', '--in-process-gpu',
                 '--disable-background-networking', '--no-first-run', '--noerrdialogs',
                 '--disable-session-crashed-bubble', '--disable-translate', '--display-size=1280x720',
                 f'--user-data-dir={inside}/profile', '--login-profile=phoenix',
                 f'http://127.0.0.1:{port}/'],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        finally:
            log.close()
    return launch


PHOENIX_PLAYER = '/opt/frontend/phoenix-player'


def player_program():
    """'phoenix' (our own player, default when installed) or 'vendor' (the vendor play app
    with its preloads). /data/native-frontend/player may say 'vendor' to switch back."""
    if tuning('player', 'phoenix') != 'vendor' and os.path.exists(str(ROOT) + PHOENIX_PLAYER):
        return 'phoenix'
    return 'vendor'


def player_launcher(target, active, index=None):
    # Same options as frontend.py's native handover (play OPTIONS stream_url [indexfile]).
    # Our own player understands the same options and environment, writes the overlay
    # status itself and reads the remote itself, so it needs no preloads.
    if player_program() == 'phoenix':
        return subprocess.Popen(['chroot', str(ROOT), '/lib/ld-linux-armhf.so.3',
            '--library-path', '/opt/playback-test/lib:/lib', PHOENIX_PLAYER,
            *native.player_options(active), *PLAYER_AUDIO, target, *([index] if index else [])],
            stdin=subprocess.DEVNULL,
            env=dict({k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')},
                     PLAYER_VOLUME_KEYS=volume_keys(), **PLAYER_EXTRA_ENV, **eos_gap_env(target)))
    preload = '/opt/frontend/player-controls.so'
    if os.path.exists(str(ROOT) + STATUS_PRELOAD):
        preload += ' ' + STATUS_PRELOAD
    if target.startswith(CHROOT_TIMESHIFT + '/') and os.path.exists(str(ROOT) + LIVEFILE_PRELOAD):
        preload += ' ' + LIVEFILE_PRELOAD
    return subprocess.Popen(['chroot', str(ROOT), '/lib/ld-linux-armhf.so.3',
        '--library-path', '/opt/playback-test/lib:/lib', '/opt/playback-test/play',
        *native.player_options(active), *PLAYER_AUDIO, target, *([index] if index else [])],
        stdin=subprocess.DEVNULL,
        env=dict({k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')},
                 LD_PRELOAD=preload, PLAYER_VOLUME_KEYS=volume_keys(), **PLAYER_EXTRA_ENV, **eos_gap_env(target)))


LIVEFILE_PRELOAD = '/opt/frontend/player-livefile.so'


def eos_gap_env(target):
    """Settings for live-buffer playback: player-livefile.so waits at the end of the growing
    file (live-block, default 0; live-wait ms; nav-extra bytes) and PLAYER_EOS_GAP_MS when
    /data/native-frontend/eos-gap is set."""
    if not target.startswith(CHROOT_TIMESHIFT + '/'):
        return {}
    # Waiting at end of file made it worse (the player reads the index in ~2 s blocks and
    # froze ~5 s per block, 14 Nov 2026), so it is off unless live-block says 1.
    env = {'PLAYER_LIVE_BLOCK': '1' if tuning('live-block', '0') == '1' else '0'}
    for name, key in (('eos-gap', 'PLAYER_EOS_GAP_MS'), ('live-wait', 'PLAYER_LIVE_WAIT_MS'),
                      ('nav-extra', 'PLAYER_LIVE_NAV_EXTRA')):
        value = tuning(name, '')
        if value.isdigit():
            env[key] = value
    return env


def serve(config, probe):
    api = native.API(config)
    controller = Controller(native.Menu(api, config.get('dvr_config_uuid', '')))
    controller.setup = InitialSetup()
    controller.restore_standby()
    page = Path(__file__).with_name('menu.html').read_text(encoding='utf-8-sig')
    launch_browser = browser_launcher(probe)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    mounts = ChrootMounts()
    timeshift = Timeshift(api)
    controller.timeshift = timeshift
    proxy = native.Proxy(api)  # binds 9992, so this cannot run beside the native frontend
    server = MenuServer(controller, page)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        mounts.setup()
        print('HTML_MENU_READY PORT=' + str(PORT), flush=True)
        indexer = Indexer(stop, controller)
        threading.Thread(target=indexer.run, daemon=True).start()
        threading.Thread(target=LedMonitor(stop, api, controller.led).run, daemon=True).start()
        Runner(controller, launch_browser, player_launcher, stop, proxy.tokens,
               overlay=OVERLAY, status_file=PLAYER_STATUS, timeshift=timeshift).run()
    finally:
        proxy.tokens.clear()
        for s in (server, proxy):
            s.shutdown(); s.server_close()
        mounts.cleanup()
        print('HTML_MENU_STOPPED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('/mnt/recording-disk/phoenix-pvr/settings/frontend.json'))
    parser.add_argument('--browser-dir', default='/opt/chromium', help='/opt/chromium inside the chroot')
    args = parser.parse_args()
    serve(json.loads(args.config.read_text()), args.browser_dir)
