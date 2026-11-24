#!/bin/bash
# PID1 bridge: stock signed kernel -> offline Alpine RAM root. No persistent writes.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin HOME=/root TERM=vt100
unset LD_PRELOAD LD_LIBRARY_PATH ENV BASH_ENV
BB=/bin/busybox
USB=/mnt/phoenix-usb
ROOT=/mnt/phoenix-ram
shopt -s execfail
fail() { echo "PHOENIX_STAGE1_STOPPED: $*"; if [ "${DIAGNOSTICS:-0}" = 1 ]; then echo 'Diagnostics stopped in RAM. Boot settings are unchanged.'; elif [ -f "$USB/phoenix/setup-network.json" ] || [ -f "$ROOT/var/lib/phoenix/setup-network.json" ]; then echo 'Installer stopped in RAM. USB installer boot is selected; remove USB for stock fallback.'; else echo 'Installer stopped in RAM. STARTUP is unchanged.'; fi; exec /bin/bash -i; }
[ "$$" = 1 ] || exit 1
"$BB" mount -t proc proc /proc || fail proc
"$BB" mount -t sysfs sysfs /sys || fail sysfs
DIAGNOSTICS=0
UART_MODE=${UART_MODE:-0}
for arg in $("$BB" cat /proc/cmdline); do [ "$arg" != phoenix.diagnostics=1 ] || DIAGNOSTICS=1; [ "$arg" != phoenix.transport=uart ] || UART_MODE=1; done
[ -c /dev/null ] || "$BB" mount -t devtmpfs devtmpfs /dev || fail devtmpfs
[ "$("$BB" cat "$USB/phoenix/usb-id.txt")" = PHOENIX_INSTALLER_USB_V2 ] || fail marker
[ "$("$BB" uname -r)" = 4.1.20-1.9 ] || fail kernel
# Stock BusyBox lacks SHA-256. Use the public Alpine loader/BusyBox shipped
# alongside this script, copied to RAM so no USB executable permission is needed.
CHECK=/phoenix-check
"$BB" mkdir -p "$CHECK" || fail verifier-directory
"$BB" cp "$USB/phoenix/check-busybox" "$CHECK/busybox" || fail verifier-copy
"$BB" cp "$USB/phoenix/check-ld-musl-armhf.so.1" "$CHECK/ld-musl-armhf.so.1" || fail verifier-copy
"$BB" chmod 755 "$CHECK/ld-musl-armhf.so.1" "$CHECK/busybox" || fail verifier-mode
"$BB" ln -sf ld-musl-armhf.so.1 "$CHECK/libc.musl-armv7.so.1" || fail verifier-link
SUMS="$USB/phoenix/SHA256SUMS"
if [ "$DIAGNOSTICS" = 1 ]; then
 # Successful installation replaces only the normal boot trigger. Verify every
 # diagnostic dependency while allowing that expected boot.txt transition.
 "$BB" grep -v -E '  phoenix/(boot|b[01][01])\.txt$' "$SUMS" > "$CHECK/diagnostic-sums" || fail diagnostic-checksums
 SUMS="$CHECK/diagnostic-sums"
fi
(cd "$USB" && "$CHECK/ld-musl-armhf.so.1" --library-path "$CHECK" "$CHECK/busybox" sha256sum -c "$SUMS") || fail checksums
"$BB" mkdir -p "$ROOT"
"$BB" mount -t tmpfs -o size=512m,mode=0755 tmpfs "$ROOT" || fail tmpfs
(cd "$ROOT" && "$BB" tar -xzf "$USB/phoenix/base-rootfs.tar.gz") || fail rootfs
"$BB" mkdir -p "$ROOT/var/lib/phoenix/initramfs" "$ROOT/mnt/phoenix-usb" "$ROOT/proc" "$ROOT/sys" "$ROOT/dev/pts"
for dir in lib usr/lib home/hybroad/lib home/hybroad/share; do
 [ -d "/$dir" ] || continue
 "$BB" mkdir -p "$ROOT/var/lib/phoenix/initramfs/$dir"
 "$BB" cp -a "/$dir/." "$ROOT/var/lib/phoenix/initramfs/$dir/" || fail vendor-libraries
done
"$BB" find / -xdev -name 'nexus*.ko' -type f -exec "$BB" cp {} "$ROOT/var/lib/phoenix/initramfs/" \; 2>/dev/null
"$BB" cp "$USB/phoenix/stage2.py" "$USB/phoenix/harvest.py" "$USB/phoenix/recovery.py" "$USB/phoenix/slots.py" "$ROOT/var/lib/phoenix/" || fail scripts
if [ "$DIAGNOSTICS" = 0 ] && [ "${UART_MODE:-0}" = 0 ] && [ -f "$USB/phoenix/setup-network.json" ]; then
 "$BB" cp "$USB/phoenix/setup-network.json" "$USB/phoenix/network-bridge.py" "$ROOT/var/lib/phoenix/" || fail network-scripts
fi
"$BB" cat > "$ROOT/etc/inittab" <<'INIT'
::sysinit:/bin/mount -t devpts devpts /dev/pts
ttyS0::wait:/usr/bin/python3 -I -u /var/lib/phoenix/stage2.py
ttyS0::respawn:/bin/sh -i
::ctrlaltdel:/sbin/reboot
INIT
if [ "$DIAGNOSTICS" = 0 ] && [ "${UART_MODE:-0}" = 0 ] && [ -f "$ROOT/var/lib/phoenix/setup-network.json" ]; then
 "$BB" cat > "$ROOT/etc/inittab" <<'NETWORK_INIT'
::sysinit:/bin/mount -t devpts devpts /dev/pts
::wait:/usr/bin/python3 -I -u /var/lib/phoenix/network-bridge.py
ttyS0::respawn:/bin/sh -i
::ctrlaltdel:/sbin/reboot
NETWORK_INIT
fi
if [ "$DIAGNOSTICS" = 1 ]; then
 "$BB" cp "$USB/phoenix/diagnostics-init.sh" "$ROOT/var/lib/phoenix/diagnostics-init.sh" || fail diagnostics-script
 "$BB" cat > "$ROOT/etc/inittab" <<'DIAGNOSTICS_INIT'
::sysinit:/bin/mount -t devpts devpts /dev/pts
ttyS0::wait:/bin/sh /var/lib/phoenix/diagnostics-init.sh
ttyS0::respawn:/bin/sh -i
::ctrlaltdel:/sbin/reboot
DIAGNOSTICS_INIT
fi
"$BB" umount "$USB" || fail usb-unmount
"$BB" mount -o move /sys "$ROOT/sys" || fail sys
"$BB" mount -o move /proc "$ROOT/proc" || fail proc
"$BB" mount -o move /dev "$ROOT/dev" || fail dev
# Moving devtmpfs replaces the earlier /dev tree; recreate pts afterwards.
"$BB" mkdir -p "$ROOT/dev/pts" || fail devpts-directory
cd / || fail cwd
exec "$ROOT/lib/ld-musl-armhf.so.1" --library-path "$ROOT/lib" "$ROOT/bin/busybox" switch_root -c /dev/console "$ROOT" /sbin/init
fail switch-root
