#!/bin/sh
set -eu
base=/data/nexus-tuner-manager
[ -e "$base/enable-autostart" ] || exit 0
n=0
while :; do
 [ ! -e /run/nexus-tuner-shutdown ] || exit 0
 state=$(cat /run/nexus-display-status 2>/dev/null || true)
 case "$state" in SERVER_RUNNING|DISPLAY_TEST_PASSED|DISPLAY_TEST_FAILED) break;; START_FAILED|DISABLED) exit 1;; esac
 n=$((n+1)); [ "$n" -lt 90 ] || exit 1
 sleep 1
done
/bin/sh "$base/control.sh" start
