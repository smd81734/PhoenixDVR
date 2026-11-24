"""Remote-only EPG settings: provider feed picker, modes and channel assignments."""
from xmltv import Store, INDEX, MODES, MODE_NAMES


class EPGSettings:
    def __init__(self, store=None):
        self.store = store or Store()
        self.channel = None

    def refresh(self, menu):
        config = self.store.config()
        menu.title = 'EPG SETTINGS'
        if menu.page == 'epg-settings':
            region = config['url'].removeprefix(INDEX).removesuffix('/epg.xml') or 'Not selected'
            menu.items = [('Feed: ' + region, 'feed'),
                          ('Mode: ' + MODE_NAMES[config['mode']], 'mode'),
                          ('Refresh every ' + str(config['hours']) + ' hours', 'interval'),
                          ('Refresh now', 'refresh'), ('Channel mapping', 'mapping')]
            menu.message = self.store.status().get('message', '')
        elif menu.page == 'epg-feeds':
            menu.title = 'SELECT EPG FEED'
            menu.items = [(('All regions' if name == 'all' else name), name) for name in self.store.regions()]
            menu.message = 'Select your region. Feed selection keeps the current EPG mode.'
        elif menu.page == 'epg-mode':
            menu.title = 'EPG SOURCE'
            menu.items = [(MODE_NAMES[mode], mode) for mode in MODES]
            menu.message = 'XMLTV only disables aerial guide updates for all channels.'
        elif menu.page == 'epg-mapping':
            menu.title = 'EPG CHANNEL MAPPING'
            channels = menu.api.rows('channel/grid')
            menu.items = [(c.get('name', '') + ' - ' + (
                'Automatic' if c['uuid'] not in config['mapping'] else
                config['mapping'][c['uuid']] or 'No XMLTV'), c)
                for c in sorted(channels, key=lambda c: str(c.get('name', '')).casefold())
                if c.get('enabled', True)]
            menu.message = 'Match channel names automatically, or select a feed channel.'
        elif menu.page == 'epg-source':
            from xmltv import read
            catalogue = read(self.store.base/'xmltv-catalogue.json', {})
            menu.title = self.channel.get('name', 'CHANNEL')
            menu.items = [('Automatic name match', None), ('No XMLTV for this channel', '')]
            menu.items += [((' / '.join(names) or key) + ' (' + key + ')', key)
                           for key, names in sorted(catalogue.items(), key=lambda kv: (' '.join(kv[1]) or kv[0]).casefold())]
            menu.message = 'Refresh the selected feed first to load its channels.' if not catalogue else 'Select the matching XMLTV channel.'
        menu.selection = min(menu.selection, max(0, len(menu.items) - 1))

    def command(self, menu, cmd):
        if cmd != 'OK' or not menu.items:
            return False
        item = menu.items[menu.selection][1]
        config = self.store.config()
        if menu.page == 'epg-settings':
            if item in ('feed', 'mode', 'mapping'):
                menu.enter({'feed': 'epg-feeds', 'mode': 'epg-mode', 'mapping': 'epg-mapping'}[item])
            elif item == 'interval':
                hours = (6, 12, 24)
                config['hours'] = hours[(hours.index(config['hours']) + 1) % len(hours)]
                self.store.save(config)
                self.refresh(menu)
            else:
                self.store.refresh()
                menu.message = 'EPG refresh requested.'
        elif menu.page == 'epg-feeds':
            config.update(url=INDEX + item + '/epg.xml', mapping={})
            self.store.save(config)
            # Do not offer a previous region's source IDs while the new feed downloads.
            (self.store.base/'xmltv-catalogue.json').unlink(missing_ok=True)
            menu.back()
            self.refresh(menu)
        elif menu.page == 'epg-mode':
            if item != 'off' and not config['url']:
                menu.message = 'Select a feed first.'
                return True
            config['mode'] = item
            self.store.save(config)
            menu.back()
            self.refresh(menu)
        elif menu.page == 'epg-mapping':
            self.channel = item
            menu.enter('epg-source')
        elif menu.page == 'epg-source':
            key = self.channel['uuid']
            if item is None:
                config['mapping'].pop(key, None)
            else:
                config['mapping'][key] = item
            self.store.save(config)
            menu.back()
            self.refresh(menu)
        else:
            return False
        return True
