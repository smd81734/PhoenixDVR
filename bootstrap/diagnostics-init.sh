#!/bin/sh
# Runs only in the temporary Alpine RAM root. No internal disk mounts or installer.
export HOME=/root TERM=vt100 PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
hostname phoenix-diagnostics
ip link set lo up
ip link set eth0 up
echo 'Welcome to Phoenix diagnostics.'
echo 'Temporary RAM environment; internal drives are not mounted.'
echo 'Connect using your UART terminal at 115200 8N1. No login is required.'
echo 'For DHCP on a normal LAN, run: udhcpc -i eth0'
echo 'To reboot without installing anything, run: reboot'
echo PHOENIX_DIAGNOSTICS_READY
