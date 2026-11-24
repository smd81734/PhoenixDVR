"""Grid programme guide data for the HTML menu (guide.js draws it).

The page asks for one 2-hour window at a time (GET /api/epg?start=T). Windows are
cached briefly so moving around the grid does not hammer Tvheadend. Channel and
programme images are served through /img/<n>, which only proxies Tvheadend's own
numeric imagecache entries.
"""
import json
import re
import threading
import time

import frontend as native

SLOT = 1800              # one time-header column
SPAN = 4 * SLOT          # visible window: two hours
AHEAD = 8 * 86400        # how far forward the guide may scroll
CACHE_SECONDS = 120
CHANNEL_SECONDS = 300
IMAGE = re.compile(r'^imagecache/(\d{1,9})$')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')
MAX_IMAGE = 2 << 20
IMAGE_CACHE = 4 << 20    # bytes of channel logos / stills kept in memory


def clean(value, length):
    return CONTROL.sub(' ', native.label(value)).strip()[:length]


def image(value):
    """Page-local URL for a Tvheadend imagecache reference, or '' for anything else."""
    m = IMAGE.match(str(value or '').lstrip('/'))
    return '/img/' + m.group(1) if m else ''


def sniff(data):
    """MIME type from a PNG/JPEG/GIF/WebP signature, or ''."""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return ''


def tz_offset(now=None):
    """Configured offset from UTC in seconds; the chroot browser has no tz data."""
    import datetime as dt
    try:
        when = dt.datetime.fromtimestamp(int(now or time.time()), native.local_zone())
        return int(when.utcoffset().total_seconds())
    except Exception:
        return 0


def slot_floor(t):
    return int(t) // SLOT * SLOT


class Guide:
    def __init__(self, api, clock=time.time, offset=tz_offset):
        self.api, self.clock, self.offset = api, clock, offset
        self.lock = threading.Lock()
        self.windows = {}        # start -> (loaded_at, data)
        self.events = {}         # (channel uuid, eventId) -> raw event, for guide_select
        self.channels, self.channels_at = None, 0
        self.images = {}         # imagecache number -> (data, type), oldest first

    def channel_list(self):
        now = self.clock()
        if self.channels is None or now - self.channels_at > CHANNEL_SECONDS:
            rows = [c for c in self.api.rows('channel/grid') if c.get('enabled', True)]

            def order(c):
                try:
                    return (float(c.get('number', 0) or 0), native.label(c.get('name')))
                except (ValueError, TypeError):
                    return (0, native.label(c.get('name')))
            out = []
            for c in sorted(rows, key=order):
                try:
                    uuid = native.identifier(c.get('uuid'))
                except ValueError:
                    continue
                out.append({'uuid': uuid, 'number': clean(c.get('number', ''), 8),
                            'name': clean(c.get('name'), 40),
                            'icon': image(c.get('icon_public_url') or c.get('icon'))})
            self.channels, self.channels_at = out, now
        return self.channels

    def load_window(self, start, stop):
        # Tvheadend's numeric filters keep the reply to the window; the overlap test below
        # still applies in case a server ignores them.
        flt = json.dumps([{'field': 'start', 'type': 'numeric', 'value': stop, 'comparison': 'lt'},
                          {'field': 'stop', 'type': 'numeric', 'value': start, 'comparison': 'gt'}])
        return self.api.rows('epg/events/grid', filter=flt, sort='start', dir='asc')

    def window(self, start):
        now = self.clock()
        start = max(slot_floor(now), min(slot_floor(now) + AHEAD, slot_floor(start)))
        with self.lock:
            hit = self.windows.get(start)
            if hit and now - hit[0] < CACHE_SECONDS:
                return dict(hit[1], now=int(now))
        stop = start + SPAN
        channels = self.channel_list()
        known = {c['uuid'] for c in channels}
        grouped, raw = {}, {}
        for e in self.load_window(start, stop):
            try:
                uuid, eid = e.get('channelUuid'), int(e['eventId'])
                s, t = int(e['start']), int(e['stop'])
            except (KeyError, TypeError, ValueError):
                continue
            if uuid not in known or t <= start or s >= stop or t <= s:
                continue
            raw[(uuid, eid)] = e
            episode = clean(e.get('episodeOnscreen'), 24)
            subtitle = clean(e.get('subtitle'), 80)
            grouped.setdefault(uuid, []).append({
                'id': eid, 'start': s, 'stop': t, 'title': clean(e.get('title'), 80) or 'Untitled',
                'episode': ' Â· '.join(x for x in (episode, subtitle) if x and x != clean(e.get('title'), 80)),
                'desc': clean(e.get('summary') or e.get('description'), 400),
                'image': image(e.get('image')),
                'rec': bool(e.get('dvrUuid')) and str(e.get('dvrState', '')) not in ('', 'missed')})
        for row in grouped.values():
            row.sort(key=lambda x: x['start'])
        data = {'start': start, 'span': SPAN, 'slot': SLOT, 'offset': self.offset(now),
                'channels': channels, 'events': grouped}
        with self.lock:
            for key in [k for k, v in self.windows.items() if now - v[0] >= CACHE_SECONDS]:
                del self.windows[key]
            self.windows[start] = (now, data)
            self.events.update(raw)
            if len(self.events) > 5000:
                self.events = dict(raw)
        return dict(data, now=int(now))

    def event(self, uuid, eid):
        """Raw Tvheadend event for a grid cell the page just showed, or None."""
        with self.lock:
            e = self.events.get((uuid, int(eid)))
        if e is None:
            try:
                entries = self.api.call('epg/events/load', {'eventId': int(eid)}).get('entries', [])
            except Exception:
                entries = []
            e = next((x for x in entries if x.get('channelUuid') == uuid), None)
        return e

    def icon(self, uuid):
        """Page-local logo URL of a channel already in the cached list, or '' (never retrieves)."""
        for c in self.channels or ():
            if c['uuid'] == uuid:
                return c['icon']
        return ''

    def image_bytes(self, number):
        number = int(number)
        with self.lock:
            hit = self.images.pop(number, None)
            if hit:
                self.images[number] = hit      # most recently used goes last
                return hit
        with self.api.open('/imagecache/' + str(number)) as response:
            kind = str(response.headers.get('Content-Type', ''))
            data = response.read(MAX_IMAGE + 1)
        # Tvheadend's imagecache does not always send an image Content-Type, so the
        # type is taken from the file's own signature; anything else is refused.
        if len(data) > MAX_IMAGE:
            raise ValueError('Image too large')
        kind = sniff(data)
        if not kind:
            raise ValueError('Not an image')
        with self.lock:
            self.images[number] = (data, kind)
            while sum(len(v[0]) for v in self.images.values()) > IMAGE_CACHE:
                del self.images[next(iter(self.images))]
        return data, kind
