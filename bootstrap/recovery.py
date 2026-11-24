"""Key-only SSH for an existing-install stop; all changes stay in the RAM root."""
import base64,ipaddress,json,os,re,secrets,socket,subprocess,sys,time
from pathlib import Path

def receive_key():
 print('PHOENIX_EVENT '+json.dumps({'event':'need-recovery-key','protocol_version':2}),flush=True)
 line=sys.stdin.readline(4096)
 if not line.startswith('PHOENIX_RECOVERY_KEY '):raise ValueError('Recovery key was not supplied')
 key=base64.b64decode(line[21:].strip(),validate=True).decode('ascii').strip()
 if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?',key):raise ValueError('Invalid recovery public key')
 return key

def start(ethernet):
 key=receive_key()
 subprocess.run(['ip','link','set','lo','up'],check=True)
 subprocess.run(['ip','link','set','eth0','up'],check=True)
 if not ethernet:subprocess.run(['udhcpc','-i','eth0','-q','-n','-f','-t','5','-T','3'],check=True,timeout=40,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 result=subprocess.run(['ip','-4','-o','addr','show','dev','eth0'],check=True,capture_output=True,text=True)
 addresses=re.findall(r'\binet\s+([0-9.]+)/',result.stdout)
 if len(addresses)!=1:raise ValueError('No unambiguous IPv4 address on eth0')
 address=str(ipaddress.IPv4Address(addresses[0]))
 ram=Path('/run/phoenix-recovery');ram.mkdir(parents=True,exist_ok=True,mode=0o700)
 authorized=Path('/root/.ssh');authorized.mkdir(mode=0o700,exist_ok=True);authorized.chmod(0o700)
 (authorized/'authorized_keys').write_bytes((key+'\n').encode('ascii'));(authorized/'authorized_keys').chmod(0o600)
 # Unlock the RAM-only account with an unknown random password; password SSH
 # remains disabled. No password is exposed to the host or saved in logs.
 subprocess.run(['chpasswd'],input='root:'+secrets.token_urlsafe(48)+'\n',text=True,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 hostkey=ram/'host_ed25519'
 if hostkey.is_symlink():raise ValueError('Unsafe recovery host key')
 if not hostkey.exists():subprocess.run(['dropbearkey','-t','ed25519','-f',str(hostkey)],check=True,capture_output=True)
 public=subprocess.run(['dropbearkey','-y','-f',str(hostkey)],check=True,capture_output=True,text=True)
 fingerprint=re.search(r'SHA256:[A-Za-z0-9+/]+={0,2}',public.stdout+'\n'+public.stderr)
 if not fingerprint:raise ValueError('Cannot verify recovery host key')
 pidfile=ram/'dropbear.pid';running=False
 if pidfile.is_file():
  pid=pidfile.read_text().strip()
  if pid.isdigit():
   process=Path('/proc')/pid/'cmdline'
   if process.is_file():
    argv=process.read_bytes().split(b'\0')
    running=any(b'dropbear' in value for value in argv) and str(hostkey).encode() in argv
 if not running:subprocess.run(['dropbear','-E','-s','-j','-k','-p',address+':22','-r',str(hostkey),'-P',str(pidfile)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 for attempt in range(30):
  try:
   with socket.create_connection((address,22),timeout=.5):break
  except OSError:
   if attempt==29:raise ValueError('Recovery SSH did not start')
   time.sleep(.1)
 return {'ready':True,'address':address,'port':22,'host_fingerprint':fingerprint.group(0)}
