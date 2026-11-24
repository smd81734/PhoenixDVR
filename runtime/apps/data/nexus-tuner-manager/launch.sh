#!/bin/sh
set -eu
runtime=/data/nexus-loader-test-v1
[ -x "$runtime/opt/tuner-manager/tuner-manager" ] || exit 1
for name in libzebra.so libfreetype.so.6 libpng16.so.16 libz.so.1 libadpcm.so libpolar_scbt.so libpopt.so.0; do
 [ -f "$runtime/opt/tuner-libs/$name" ] || exit 1
done
cd /
unset LD_PRELOAD
exec chroot "$runtime" /lib/ld-linux-armhf.so.3 --library-path /opt/tuner-libs:/opt/blit/lib:/lib /opt/tuner-manager/tuner-manager "$@"
