#!/bin/sh
set -eu
cd /
base=/data/native-frontend
case "${1:-status}" in
start)
 [ "$(cat /run/alpine-data-status)" = DATA_MOUNTED_RW ]
 [ -S /data/nexus-loader-test-v1/tmp/nxserver_ipc ]
 [ -f /mnt/recording-disk/phoenix-pvr/settings/frontend.json ] || { echo 'Run frontend.py --configure first'; exit 1; }
 [ ! -e /run/native-frontend.pid ] || { echo 'Frontend PID file already exists'; exit 1; }
 command -v python3 >/dev/null
 [ ! -f "$base/frontend.log" ] || mv "$base/frontend.log" "$base/frontend.previous.log"
 python3 "$base/frontend.py" </dev/null > "$base/frontend.log" 2>&1 &
 echo $! > /run/native-frontend.pid
 sleep 3
 if ! kill -0 "$(cat /run/native-frontend.pid)" 2>/dev/null; then
  rm /run/native-frontend.pid; tail -n 30 "$base/frontend.log"; exit 1
 fi
 echo NATIVE_FRONTEND_RUNNING
 ;;
stop)
 if [ -f /run/native-frontend.pid ]; then
  p=$(cat /run/native-frontend.pid)
  case "$p" in ''|*[!0-9]*) exit 1;; esac
  if [ -r "/proc/$p/cmdline" ] && tr '\000' '\n' < "/proc/$p/cmdline" | grep -Fx "$base/frontend.py" >/dev/null; then
   kill -TERM "$p"
   n=0
   while kill -0 "$p" 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 30 ] || { echo FRONTEND_STOP_TIMEOUT; exit 1; }; sleep 1
   done
  fi
  rm /run/native-frontend.pid
 fi
 echo NATIVE_FRONTEND_STOPPED
 ;;
status)
 [ -f /run/native-frontend.pid ] && kill -0 "$(cat /run/native-frontend.pid)" && echo RUNNING
 ;;
*) exit 1;;
esac
