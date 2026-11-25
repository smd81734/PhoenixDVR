"""Settings > Wi-Fi: status, use the network saved by the old (stock) firmware, on/off, forget.

Status comes straight from the files phoenix-wifi keeps (no process per refresh); actions run
/usr/local/sbin/phoenix-wifi. Network name and password are typed in the Windows installer
(PhoenixSetup), never on the TV; the password is never shown or logged here."""
import json
from pathlib import Path
import subprocess

TOOL = '/usr/local/sbin/phoenix-wifi'
STATUS = Path('/run/phoenix-wifi/status.json')
SETTINGS = Path('/mnt/recording-disk/phoenix-pvr/settings/wifi.json')
PCI_DEVICES = Path('/sys/bus/pci/devices')
STATES = {'connected': 'Connected', 'connecting': 'Connecting', 'starting': 'Starting',
          'error': 'Problem', 'off': 'Off', 'unavailable': 'Not available'}


def run_tool(*args, timeout=60):
    """Run phoenix-wifi; returns its JSON reply (or an error reply)."""
    try:
        result = subprocess.run([TOOL] + list(args), capture_output=True, text=True, timeout=timeout)
        lines = [x for x in result.stdout.splitlines() if x.startswith('{')]
        return json.loads(lines[-1]) if lines else {'ok': result.returncode == 0}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ok': False, 'message': 'Wi-Fi tool did not answer'}


class WifiSettings:
    def __init__(self, status=STATUS, settings=SETTINGS, devices=PCI_DEVICES, tool=run_tool):
        self.status_file, self.settings_file, self.devices, self.tool = status, settings, devices, tool
        self.note = ''

    def hardware(self):
        try:
            return any((d/'vendor').read_text().strip() == '0x14e4' and (d/'device').read_text().strip() == '0x43a0'
                       for d in self.devices.iterdir())
        except OSError:
            return False

    def state(self):
        try:
            settings = json.loads(self.settings_file.read_text())
        except (OSError, ValueError):
            settings = None
        try:
            status = json.loads(self.status_file.read_text())
        except (OSError, ValueError):
            status = {'state': 'off'}
        return {'configured': isinstance(settings, dict) and bool(settings.get('ssid')),
                'enabled': isinstance(settings, dict) and settings.get('enabled', True) is not False,
                'ssid': str((settings or {}).get('ssid', ''))[:32] if isinstance(settings, dict) else '',
                'state': str(status.get('state', 'off')), 'ip': str(status.get('ip', ''))[:15],
                'rssi': status.get('rssi'), 'message': str(status.get('message', ''))[:60]}

    def refresh(self, menu):
        menu.title = 'WI-FI'
        if not self.hardware():
            menu.items = [('This box has no Wi-Fi', None)]
            menu.message = ''
            menu.selection = 0
            return
        s = self.state()
        if s['configured']:
            line = 'Network: ' + s['ssid']
            state = STATES.get(s['state'], s['state'].title())
            if s['state'] == 'connected':
                state += ' (' + s['ip'] + (', signal %d dBm' % s['rssi'] if isinstance(s['rssi'], int) else '') + ')'
            elif s['message']:
                state += ' - ' + s['message']
            items = [(line, None), ('Status: ' + state, None)]
            items.append(('Turn Wi-Fi off', 'off') if s['enabled'] else ('Turn Wi-Fi on', 'on'))
            items += [('Use the Wi-Fi saved by the old firmware', 'import'), ('Forget this network', 'forget')]
        else:
            items = [('Status: Not set up', None), ('Use the Wi-Fi saved by the old firmware', 'import')]
        menu.items = items
        menu.message = self.note or ('Ethernet is used first whenever its cable is plugged in. '
                                     'To join another network, use PhoenixSetup on your PC.')
        menu.selection = min(menu.selection, len(items) - 1)

    def command(self, menu, cmd):
        if menu.page != 'wifi':
            return False
        if cmd != 'OK' or not menu.items:
            return cmd == 'OK'
        action = menu.items[menu.selection][1]
        if action == 'import':
            reply = self.tool('import-stock')
            if reply.get('ok'):
                self.tool('restart', timeout=60)
                self.note = 'Connecting to ' + str(reply.get('ssid', ''))[:32] + '...'
            else:
                self.note = str(reply.get('message') or 'No saved Wi-Fi found')[:80]
        elif action in ('on', 'off'):
            reply = self.tool('enable' if action == 'on' else 'disable')
            self.tool('restart' if action == 'on' else 'stop', timeout=60)
            self.note = 'Wi-Fi turned ' + action + '.' if reply.get('ok') else str(reply.get('message', ''))[:80]
        elif action == 'forget':
            reply = self.tool('forget', timeout=60)
            self.note = 'Wi-Fi network forgotten.' if reply.get('ok') else 'Could not forget the network.'
        else:
            return True
        self.refresh(menu)
        return True
