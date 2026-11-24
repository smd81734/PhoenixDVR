#!/bin/sh
# Control for the primary HTML menu; invoked by UI startup and storage shutdown.
set -eu
cd /
base=/data/native-frontend
runtime=/data/nexus-loader-test-v1
pidfile=/run/html-frontend.pid
case "${1:-status}" in
start)
 [ "$(cat /etc/storage-policy)" = HDD_SYSTEM_V1 ]
 [ "$(cat /run/alpine-data-status)" = DATA_MOUNTED_RW ]
 [ -S "$runtime/tmp/nxserver_ipc" ]
 [ -f /mnt/recording-disk/phoenix-pvr/settings/frontend.json ] || { echo 'Run frontend.py --configure first'; exit 1; }
 [ ! -e "$pidfile" ] || { echo 'HTML frontend PID file already exists'; exit 1; }
 if [ -e /run/native-frontend.pid ]; then echo 'Stop the native frontend first'; exit 1; fi
 command -v python3 >/dev/null
 probe=${2:-}
 if [ -z "$probe" ]; then
  probe=/opt/chromium
  [ -d "$runtime$probe/bin" ] || { echo 'Stock browser directory missing'; exit 1; }
 fi
 [ ! -f "$base/html-frontend.log" ] || mv "$base/html-frontend.log" "$base/html-frontend.previous.log"
 [ ! -f "$base/chromium.log" ] || mv "$base/chromium.log" "$base/chromium.previous.log"
 echo "Using browser $probe"
 python3 "$base/html_frontend.py" --browser-dir "$probe" </dev/null > "$base/html-frontend.log" 2>&1 &
 echo $! > "$pidfile"
 sleep 3
 if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  rm "$pidfile"; tail -n 30 "$base/html-frontend.log"; exit 1
 fi
 echo HTML_FRONTEND_RUNNING
 ;;
stop)
 if [ -f "$pidfile" ]; then
  p=$(cat "$pidfile")
  case "$p" in ''|*[!0-9]*) exit 1;; esac
  if [ -r "/proc/$p/cmdline" ] && tr '\000' '\n' < "/proc/$p/cmdline" | grep -Fx "$base/html_frontend.py" >/dev/null; then
   kill -TERM "$p"
   n=0
   while kill -0 "$p" 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 40 ] || { echo HTML_FRONTEND_STOP_TIMEOUT; exit 1; }; sleep 1
   done
  fi
  rm "$pidfile"
 fi
 if grep -F " $runtime/proc " /proc/mounts >/dev/null || grep -F " $runtime/dev/shm " /proc/mounts >/dev/null; then
  echo "NOTE: $runtime/proc or dev/shm is still mounted; check html-frontend.log before shutdown"
 fi
 # Recording binds must be gone before the HDD is unmounted at shutdown.
 for m in "$runtime/media/recordings" "$runtime/media/index" "$runtime/media/timeshift"; do
  if grep -F " $m " /proc/mounts >/dev/null; then
   umount "$m" && echo "Unmounted leftover $m" || echo "WARNING: $m is still mounted; unmount it before shutdown"
  fi
 done
 echo HTML_FRONTEND_STOPPED
 ;;
status)
 [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" && echo RUNNING
 ;;
*) exit 1;;
esac
