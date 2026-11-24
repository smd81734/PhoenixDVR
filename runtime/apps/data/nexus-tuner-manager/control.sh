#!/bin/sh
set -eu
base=/data/nexus-tuner-manager
pidfile=/run/nexus-tuner-manager.pid
alive() {
 [ -f "$pidfile" ] || return 1
 pid=$(cat "$pidfile")
 case "$pid" in ''|*[!0-9]*) return 1;; esac
 [ -r "/proc/$pid/cmdline" ] || return 1
 tr '\000' '\n' < "/proc/$pid/cmdline" | grep -Fx /opt/tuner-manager/tuner-manager >/dev/null
}
case "${1:-status}" in
start)
 [ ! -e /run/nexus-tuner-shutdown ] || { echo 'STOP: shutdown in progress'; exit 1; }
 [ "$(cat /run/alpine-data-status)" = DATA_MOUNTED_RW ] || exit 1
  if alive; then echo "ALREADY_RUNNING PID=$pid"; exit 0; fi
 [ ! -e /run/tuner-init-attempted ] || { echo 'STOP: fresh reboot required'; exit 1; }
 [ -S /data/nexus-loader-test-v1/tmp/nxserver_ipc ] || { echo 'STOP: NEXUS display not ready'; exit 1; }
 [ -x /data/nexus-loader-test-v1/opt/tuner-manager/tuner-manager ] || exit 1
 mkdir /run/nexus-tuner-start.lock || exit 1
 trap 'rmdir /run/nexus-tuner-start.lock' EXIT
 [ ! -e /run/tuner-init-attempted ] || exit 1
 touch /run/tuner-init-attempted
 [ ! -f "$base/service.log" ] || mv "$base/service.log" "$base/service.previous.log"
 /bin/busybox setsid /bin/sh "$base/launch.sh" </dev/null > "$base/service.log" 2>&1 &
 echo "$!" > "$pidfile"
 n=0
 while :; do
  n=$((n+1)); sleep 1
  alive || { echo 'START_FAILED: inspect service.log, reboot before retry'; exit 1; }
  if grep -F MANAGER_READY "$base/service.log" >/dev/null; then echo "MANAGER_RUNNING PID=$pid"; exit 0; fi
  [ "$n" -lt 75 ] || { echo 'START_TIMEOUT: inspect service.log'; exit 1; }
 done
 ;;
stop)
 if alive; then
  kill -TERM "$pid"
  n=0
  while alive; do
   n=$((n+1)); [ "$n" -lt 40 ] || { echo 'STOP_FAILED: do not unmount data'; exit 1; }
   sleep 1
  done
 fi
 rm -f "$pidfile"
 echo MANAGER_STOPPED_REBOOT_BEFORE_RESTART
 ;;
status)
 if alive; then
  echo "PROCESS_RUNNING PID=$pid"
  wget -q -T 5 -O - http://127.0.0.1:9990/status || exit 1
 else echo STOPPED; fi
 ;;
probe-frontends)
 # Read-only: lists tuners the NEXUS server exposes; does not start the vendor tuner library.
 [ -S /data/nexus-loader-test-v1/tmp/nxserver_ipc ] || { echo 'STOP: NEXUS display not ready'; exit 1; }
 timeout 30 /bin/sh "$base/launch.sh" --probe-frontends </dev/null
 ;;
*) echo 'Usage: control.sh start|status|stop|probe-frontends'; exit 1;;
esac
