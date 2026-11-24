"""Persistent first-run gate; RF work starts only after an explicit user action."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

class InitialSetup:
    def __init__(self, base=Path('/mnt/recording-disk/phoenix-pvr/settings'), launcher=None):
        self.base = Path(base)
        self.status = self.base/'setup-status.json'
        self.pending = self.base/'firstboot-pending'
        self.accepted = self.base/'setup-accepted'
        self.launcher = launcher or self._launch
        self.lock = threading.Lock()
        self.process = None

    def _read(self):
        try:
            value = json.loads(self.status.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _alive(self, state):
        if self.process is not None and self.process.poll() is None:
            return True
        try:
            pid = int(state.get('pid', 0))
            return pid > 1 and b'/data/phoenix/tvheadend/setup.py' in (Path('/proc')/str(pid)/'cmdline').read_bytes().split(bytes([0]))
        except (OSError, ValueError, TypeError):
            return False

    def view(self):
        state = self._read()
        if not self.pending.exists() and (not state or self.accepted.exists()):
            return None
        if state.get('state') == 'complete' and not self.pending.exists():
            return dict(state, button='OK', heading='Configuration is complete !', message='Press OK to open the main menu.')
        if state.get('state') == 'running':
            if self._alive(state):
                return dict(state, button='', heading='Setting up Phoenix')
            # A reboot or failed process must never leave a permanent spinner.
            state = dict(state, state='error', message='Setup was interrupted. Check your antenna connection and press Retry.', error='Setup was interrupted.')
        if state.get('state') == 'error':
            return dict(state, button='Retry', heading='Setup needs attention')
        return {'state':'waiting', 'stage':'welcome', 'heading':'Welcome to Phoenix',
                'message':'Press Start to begin scanning for channels.',
                'detail':'Connect your antenna before you start. You can leave this screen open until you are ready.', 'button':'Start', 'current':0, 'total':0}

    def _launch(self):
        log = (self.base/'firstboot.log').open('a')
        try:
            return subprocess.Popen(['/bin/sh', '/usr/local/sbin/channel-setup', 'firstboot'],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        finally:
            log.close()

    def action(self):
        with self.lock:
            state = self.view()
            if state is None or state.get('state') == 'running':
                return
            if state.get('state') == 'complete':
                self.accepted.touch()
                return
            # The setup shell has a separate exclusive lock as well.
            self.base.mkdir(parents=True, exist_ok=True)
            self.accepted.unlink(missing_ok=True)
            initial = {'state':'running', 'stage':'configuring', 'message':'Configuring TV Headend…',
                       'current':0, 'total':0, 'updated':time.time()}
            tmp = self.status.with_suffix('.new')
            tmp.write_text(json.dumps(initial)); tmp.replace(self.status)
            try:
                self.process = self.launcher()
            except Exception:
                initial.update(state='error', message='Could not start setup. Press Retry to try again.')
                tmp.write_text(json.dumps(initial)); tmp.replace(self.status)
