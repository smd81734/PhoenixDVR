"""Execute the real init-selection block without booting or mounting devices."""
import os,pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
def find_bash():
 found=shutil.which('bash')
 if found:return found
 git=shutil.which('git')
 if git:
  root=pathlib.Path(git).resolve().parent.parent
  for relative in ('bin/bash.exe','usr/bin/bash.exe'):
   candidate=root/relative
   if candidate.is_file():return str(candidate)
 return None
BASH=find_bash()

@unittest.skipUnless(BASH,'Bash required for boot-mode integration test')
class DiagnosticModeTests(unittest.TestCase):
 def select(self,diagnostic,network,uart=False):
  source=(ROOT/'bootstrap/stage1.sh').read_text()
  block=source[source.index('"$BB" cat > "$ROOT/etc/inittab"'):source.index('"$BB" umount "$USB"')]
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);ram=work/'ram';usb=work/'usb'
   (ram/'etc').mkdir(parents=True);(ram/'var/lib/phoenix').mkdir(parents=True);(usb/'phoenix').mkdir(parents=True)
   (usb/'phoenix/diagnostics-init.sh').write_text('diagnostic-only-marker')
   if network:(ram/'var/lib/phoenix/setup-network.json').write_text('{}')
   env=dict(os.environ,ROOT=ram.as_posix(),USB=usb.as_posix(),BB='/usr/bin/env',DIAGNOSTICS=str(diagnostic),UART_MODE='1' if uart else '0')
   subprocess.run([BASH,'-c',block],env=env,check=True,capture_output=True,timeout=10)
   return (ram/'etc/inittab').read_text(),(ram/'var/lib/phoenix/diagnostics-init.sh').exists()
 def test_diagnostics_bypasses_installer_even_with_stale_network_configuration(self):
  for network in (False,True):
   init,copied=self.select(1,network)
   self.assertTrue(copied);self.assertIn('diagnostics-init.sh',init);self.assertIn('respawn:/bin/sh -i',init)
   self.assertNotIn('stage2.py',init);self.assertNotIn('network-bridge.py',init)
 def test_existing_uart_and_ethernet_install_routes_are_preserved(self):
  self.assertIn('stage2.py',self.select(0,False)[0]);self.assertIn('network-bridge.py',self.select(0,True)[0])
 def test_uart_boot_ignores_stale_ethernet_settings(self):
  init,_=self.select(0,True,uart=True)
  self.assertIn('stage2.py',init);self.assertNotIn('network-bridge.py',init)
