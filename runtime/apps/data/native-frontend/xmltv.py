#!/usr/bin/env python3
"""Persistent XMLTV feed selection and bounded imports through Tvheadend's socket."""
import argparse
import gzip
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = Path('/mnt/recording-disk/phoenix-pvr/settings')
INDEX = 'https://i.mjh.nz/au/'
REGIONS = ('Adelaide', 'Brisbane', 'Canberra', 'Darwin', 'Hobart', 'Melbourne', 'Perth', 'Sydney', 'all')
MODES = ('off', 'supplement', 'only')
MODE_NAMES = {'off': 'Aerial EPG only', 'supplement': 'XMLTV + aerial fallback', 'only': 'XMLTV only'}
MAX_XML = 32 * 1024 * 1024


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=True) + '\n')
    temporary.replace(path)


def url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or any(ord(c) <= 32 for c in value)):
        raise ValueError('Expected an HTTP or HTTPS XMLTV URL')
    return value


def download(address, limit):
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            url(newurl)
            return super().redirect_request(request, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
    with opener.open(urllib.request.Request(url(address), headers={
            'User-Agent': 'Phoenix-DVR-XMLTV/1', 'Accept-Encoding': 'identity'}), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Feed exceeds size limit')
    return data


class RegionIndex(HTMLParser):
    def __init__(self):
        super().__init__()
        self.regions = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get('href', '')
        # Only direct child directories; never follow arbitrary index links.
        if tag == 'a' and re.fullmatch(r'[A-Za-z0-9_-]+/', href):
            self.regions.add(href[:-1])


def discover():
    parser = RegionIndex()
    parser.feed(download(INDEX, 256 * 1024).decode('utf-8'))
    if not parser.regions:
        raise ValueError('No regions found in feed index')
    return sorted(parser.regions, key=str.casefold)


def parse_feed(data):
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            data = compressed.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise ValueError('Expanded XMLTV exceeds size limit')
    # XMLTV's external DTD declaration is common; ElementTree does not load it.
    # Reject entity declarations, including UTF-16 representations.
    if b'<!ENTITY' in data.replace(b'\x00', b'').upper():
        raise ValueError('XML entities are not supported')
    tree = ET.fromstring(data)
    if tree.tag != 'tv':
        raise ValueError('Expected an XMLTV tv document')
    channels = {}
    for element in tree.findall('channel'):
        key = element.get('id', '')
        if not key or key in channels:
            raise ValueError('Missing or duplicate XMLTV channel ID')
        channels[key] = [n.text.strip() for n in element.findall('display-name') if n.text and n.text.strip()]
    programmes = tree.findall('programme')
    if not channels or not programmes:
        raise ValueError('Feed has no channels or programmes')
    if any(p.get('channel') not in channels or not p.get('start') or not p.get('stop') for p in programmes):
        raise ValueError('Programme is missing its channel or times')
    return tree, channels


def send_xml(tree, path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(30)
        stream.connect(str(path))
        stream.sendall(ET.tostring(tree, encoding='utf-8', xml_declaration=True))
        stream.shutdown(socket.SHUT_WR)
        # EOF means the external parser has finished handling this connection.
        while stream.recv(1024):
            pass


class Store:
    def __init__(self, base=BASE):
        self.base = Path(base)

    def config(self):
        value = read(self.base/'xmltv.json', {'mode': 'off', 'url': '', 'hours': 12, 'mapping': {}})
        if value.get('mode') not in MODES or value.get('hours') not in (6, 12, 24):
            raise ValueError('Invalid XMLTV settings')
        if value.get('url'):
            url(value['url'])
        if not isinstance(value.get('mapping'), dict):
            raise ValueError('Invalid channel mapping')
        return value

    def save(self, value):
        if value['mode'] != 'off' and not value.get('url'):
            raise ValueError('Select a feed first')
        write(self.base/'xmltv.json', value)
        self.refresh()

    def refresh(self):
        write(self.base/'xmltv-request.json', time.time_ns())

    def regions(self):
        return read(self.base/'xmltv-regions.json', {}).get('regions', list(REGIONS))

    def status(self):
        return read(self.base/'xmltv-status.json', {})


class Importer:
    def __init__(self, api, store, socket_path=None, retrieve=download, sender=send_xml):
        self.api, self.store, self.retrieve, self.sender = api, store, retrieve, sender
        self.socket_path = socket_path or store.base.parent/'tvheadend/config/epggrab/xmltv.sock'
        self.journal = store.base/'xmltv-managed.json'

    def save_node(self, **node):
        result = self.api.call('idnode/save', {'node': json.dumps(node)}, post=True)
        if result.get('error'):
            raise RuntimeError('Tvheadend rejected EPG configuration')

    def modules(self):
        result = []
        for module in self.api.call('epggrab/module/list')['entries']:
            entry = self.api.call('idnode/load', {'uuid': module['uuid']})['entries'][0]
            values = {p['id']: p.get('value', p.get('default')) for p in entry['params']}
            result.append(dict(values, uuid=module['uuid']))
        return result

    def restore(self, state, channels=True):
        for node in state.get('ota', []):
            self.save_node(**node)
        if channels:
            current = {c['uuid'] for c in self.api.rows('channel/grid')}
            for node in state.get('channels', []):
                if node['uuid'] in current:
                    self.save_node(**node)
            sources = {c['uuid'] for c in self.api.rows('epggrab/channel/grid')}
            for node in state.get('sources', []):
                if node['uuid'] in sources:
                    self.save_node(**node)
            if state.get('xmltv'):
                self.save_node(**state['xmltv'])

    def run_once(self):
        config = self.store.config()
        state = read(self.journal, {})
        if config['mode'] == 'off':
            if state:
                self.restore(state)
                self.journal.unlink(missing_ok=True)
            return {'message': 'Aerial EPG only', 'mapped': 0}
        if config['mode'] == 'supplement' and state:
            self.restore(state, channels=False)
        # Validate the complete response before changing the working EPG configuration.
        tree, catalogue = parse_feed(self.retrieve(config['url'], MAX_XML))
        modules = self.modules()
        external = next((m for m in modules if m.get('type') == 'External' and m.get('name') == 'XMLTV'), None)
        if not external:
            raise RuntimeError('External XMLTV grabber is unavailable')
        if not state:
            state = {'xmltv': {k: external[k] for k in ('uuid', 'enabled', 'priority')},
                     'ota': [{k: m[k] for k in ('uuid', 'enabled')} for m in modules
                             if m.get('type') == 'Over-the-air'], 'channels': [], 'sources': []}
            write(self.journal, state)  # Persist restoration data before any mutation.
        priority = max([int(m.get('priority', 0)) for m in modules if m.get('type') == 'Over-the-air'] + [0]) + 1
        self.save_node(uuid=external['uuid'], enabled=True, priority=priority)
        channels = self.api.rows('channel/grid')
        # Exact, case-insensitive names only; ambiguous matches need explicit selection.
        lookup = {}
        for key, names in catalogue.items():
            for name in set(n.casefold() for n in names):
                lookup.setdefault(name, set()).add(key)
        chosen = {}
        for channel in channels:
            if not channel.get('enabled', True):
                continue
            key = config['mapping'].get(channel['uuid'])
            if key is None:
                matches = lookup.get(channel.get('name', '').casefold(), set())
                key = next(iter(matches)) if len(matches) == 1 else ''
            if key in catalogue:
                chosen[channel['uuid']] = key
        selected = set(chosen.values())
        has_programmes = any(p.get('channel') in selected for p in tree.findall('programme'))
        if not chosen or not has_programmes:
            self.restore(state)
            self.journal.unlink(missing_ok=True)
            write(self.store.base/'xmltv-catalogue.json', catalogue)
            raise ValueError('No matching channels. Open Channel mapping.')
        # Bootstrap source IDs with private labels to avoid automatic name matching.
        bootstrap = ET.Element('tv')
        for key in set(chosen.values()):
            element = ET.SubElement(bootstrap, 'channel', id=key)
            ET.SubElement(element, 'display-name').text = 'Phoenix XMLTV ' + key
        self.sender(bootstrap, self.socket_path)
        sources = {c['id']: c for c in self.api.rows('epggrab/channel/grid') if c.get('modid') == 'xmltv'}
        if any(key.replace('/', '#') not in sources for key in chosen.values()):
            raise RuntimeError('Tvheadend did not register XMLTV channels')
        saved = {c['uuid'] for c in state['channels']}
        # Restore channels no longer selected (changed feed, mapping, or a new scan).
        current = {c['uuid'] for c in channels}
        for node in state['channels']:
            if node['uuid'] not in chosen and node['uuid'] in current:
                self.save_node(**node)
        for channel in channels:
            key = chosen.get(channel['uuid'])
            if key is None:
                continue
            if channel['uuid'] not in saved:
                state['channels'].append({k: channel.get(k, True if k == 'epgauto' else [])
                                          for k in ('uuid', 'epgauto', 'epggrab')})
                write(self.journal, state)
            source = sources[key.replace('/', '#')]
            if source['uuid'] not in {s['uuid'] for s in state.get('sources', [])}:
                state.setdefault('sources', []).append({k: source.get(k, [] if k == 'update' else True)
                                                       for k in ('uuid', 'enabled', 'update')})
                write(self.journal, state)
            self.save_node(uuid=source['uuid'], enabled=True, update=[])
            original = next(c for c in state['channels'] if c['uuid'] == channel['uuid'])
            xmltv_ids = {c['uuid'] for c in sources.values()}
            links = [v for v in original['epggrab'] if v not in xmltv_ids]
            self.save_node(uuid=channel['uuid'], epgauto=False, epggrab=links + [source['uuid']])
        # Import only selected sources and programmes. Do not import provider icons or LCNs.
        filtered = ET.Element('tv')
        for key in selected:
            element = ET.SubElement(filtered, 'channel', id=key)
            ET.SubElement(element, 'display-name').text = 'Phoenix XMLTV ' + key
        for programme in tree.findall('programme'):
            if programme.get('channel') in selected:
                filtered.append(programme)
        self.sender(filtered, self.socket_path)
        if config['mode'] == 'only':
            if not state['ota']:
                raise RuntimeError('Cannot identify aerial EPG modules')
            for node in state['ota']:
                self.save_node(uuid=node['uuid'], enabled=False)
        else:
            self.restore(state, channels=False)
        write(self.store.base/'xmltv-catalogue.json', catalogue)
        return {'message': 'XMLTV refreshed', 'mapped': len(chosen), 'feed_channels': len(catalogue),
                'last_success': time.time(), 'url': config['url']}

    def loop(self, stop):
        last, request, next_run, next_index = None, None, 0, 0
        while not stop.is_set():
            now = time.time()
            if now >= next_index:
                try:
                    write(self.store.base/'xmltv-regions.json', {'regions': discover(), 'updated': now})
                except Exception:
                    pass  # Keep cached/bundled choices; importing can still work.
                next_index = now + 86400
            try:
                config = self.store.config()
                wanted = read(self.store.base/'xmltv-request.json', None)
                if config != last or wanted != request or now >= next_run:
                    previous = self.store.status()
                    write(self.store.base/'xmltv-status.json', dict(previous, message='Refreshing EPG...'))
                    try:
                        result = self.run_once()
                        write(self.store.base/'xmltv-status.json', result)
                        next_run = now + config['hours'] * 3600
                    except Exception as exc:
                        # Never log URLs (which may include private feed tokens).
                        message = str(exc) if isinstance(exc, ValueError) else 'Feed refresh failed; retrying in 5 minutes'
                        write(self.store.base/'xmltv-status.json', dict(previous, message=message[:160], error=True))
                        next_run = now + 300
                    last, request = config, wanted
            except Exception:
                next_run = now + 300
            stop.wait(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    # Import by path because a native renderer preload can also be called frontend.so.
    import importlib.util
    spec = importlib.util.spec_from_file_location('xmltv_frontend', Path(__file__).with_name('frontend.py'))
    frontend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frontend)
    api = frontend.API(read(BASE/'frontend.json', {}))
    worker = Importer(api, Store())
    if args.once:
        worker.run_once()
    else:
        stop = threading.Event()
        worker.loop(stop)


if __name__ == '__main__':
    main()
