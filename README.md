# Phoenix Windows Device Setup

A Windows application for preparing a Phoenix USB stick and installing Phoenix
on a supported DVR. Direct Ethernet access is the default connection method;
UART/BOLT is available under Advanced. The Utilities tab provides terminal
access and diagnostic boot without installing software.

## Build from source

See [BUILDING.md](BUILDING.md) for prerequisites, runtime input import, build,
test and packaging commands. Images and compiled runtime dependencies are
supplied in a separate installer archive. Editable runtime source is under
`runtime/`; imported dependencies are under `inputs/runtime`.

## Installation

Extract the whole Phoenix Setup ZIP into one folder and run `PhoenixSetup.exe`
from there. The helper scripts in `scripts\` (USB formatting and the DVR-side
update worker) must stay next to the EXE; the EXE checks their SHA-256 and
refuses a changed or missing copy.

Phoenix Setup opens on one question: **Set up a new DVR**, **Update my DVR** or
**Fix a problem**. Each path uses plain language and one main button per screen.
Switch on **Advanced view** (top right) to show the technical controls on every
screen: USB payload folder, UART/BOLT connection, discovery options, separate
root and Tvheadend passwords, band plan, SHA override, SSH keys, publisher keys,
rollback, Utilities and the technical log.

1. **Get ready**: a USB stick up to the 64 GB class, a network cable, the remote.
2. **USB stick**: choose the stick and prepare it. Erasing asks for confirmation.
   The list refreshes when a stick is plugged in.
3. **Connect**: DVR off, HDMI to the TV (the aerial can wait), USB stick in the
   rear port, cable from the DVR straight to this PC, DVR on.
4. **Find DVR**: press Up, Down, Left, Right, 6, 9, 1, 5, Red within 14 seconds.
   On compact remotes, cover the infrared transmitter for every 123 press.
   Factory-mode HDMI output may remain blank. Ethernet discovery requires Npcap;
   the page offers its download link when it is missing. The DVR restarts after
   the sequence; wait at least 2 minutes before searching.
5. **Install**: the DVR is checked, then Install Phoenix asks for a DVR name and
   one password (used for both root and Tvheadend unless "Use a different
   password for Tvheadend" is ticked; Advanced view always asks for both). The time zone comes from Windows. The DVR restarts during this
   step. If the prepared stick isn't found in the DVR's rear USB port, Setup
   pauses so it can be plugged in, then rescans and carries on (nothing has been
   written at that point).
6. **Done**: leave the USB stick in, power off, move the cable to the household
   router, plug in the TV and aerial, power on and finish channel setup on the TV
   with the remote.

The application saves per-device logs and maintenance keys in its local session
folder. Existing installations are not overwritten; the connection panel offers
SSH details when its temporary maintenance server is available.

## Reconnect to temporary Alpine

Choose **Fix a problem**, then **Setup stopped partway** to resume the newest saved session. In Advanced view the same page offers both actions below with a file picker. The Ethernet port selected on the Find box page is used. Keep the DVR powered on, its USB
inserted and the direct Ethernet cable connected. Do not repeat the remote sequence.

- **Reconnect to temporary Alpine** opens the original session's
  `ethernet-device/resume.json`, restores the PC address and resumes monitoring
  the same installation process. A pending configuration prompt is shown again;
  accepted passwords are not requested again. Completed or failed sessions report
  their saved status without starting a second installation.
- **Restore temporary Alpine SSH access** opens the original session's
  `recovery-connection.txt`, restores the PC address and verifies the SSH host
  fingerprint. This also supports older sessions that stopped at an existing
  installation and started the recovery SSH server.

Keep the original setup log folder: it contains the private reconnect token and
maintenance key. Those files must not be published. The reconnectable bridge
requires newly prepared USB media. Older environments whose bridge has stopped
and which have no running SSH service require the existing recovery methods.
Reconnect does not restart a failed installer or make a power interruption safe.

## Repair and retry

After Alpine starts, setup pauses with a repair dialog for missing USB media,
USB verification problems, HDD availability or mount errors, insufficient free
space, Ethernet/DHCP problems and busy disk/USB mounts. Correct the issue and
press **Retry** to repeat that check. Rejected configuration can be entered again.

Existing installation folders remain protected. A paused check offers maintenance
SSH details when available; resolve the folders yourself, return the HDD mount to
read-only and Retry. No existing files are removed automatically.

Retries after HDD commit finish the verified USB boot files, receipts and clean
unmounts. They never rerun installation cutover. Unsupported hardware, changed
disk identity and firmware integrity failures remain blocked. Ethernet reconnect
preserves pending repair prompts. Use the complete current USB payload.

UART boot uses an explicit transport flag, so stale Ethernet setup metadata on
the USB cannot select Ethernet mode.

## Terminal utilities

- Capture BOLT captures the bootloader, then releases the serial port for a
  terminal application.
- Boot diagnostic Linux starts the USB environment in RAM without installing
  software or mounting internal disks. UART uses 115200 8N1 without flow control.
- Factory terminal discovers the direct-link address and hands off Telnet
  access. Keep the application open to retain the temporary PC address.
- Exit factory mode resets RUNLEVEL to 1, verifies both environment copies and
  preserves STARTUP. Reboot is optional.

## Compatibility and recovery

Supported firmware selectors are KERNEL and ROOTFS slots 0 or 1. Installation
preserves those selectors and checks the signed kernel, version `4.1.20-1.9`,
ARMv7 architecture, memory layout and Nexus module compatibility. The selected
stock root is resolved through verified GPT data and mounted read-only.

The HDD must use the supported non-removable SATA/XFS layout with sufficient
free space. Setup never formats the HDD. Preferences and recordings remain on
the HDD independently of core software upgrades.

The USB stick is required for Phoenix boot. Removing it provides stock fallback
only when the original firmware selection is usable and RUNLEVEL is normal.
Ethernet discovery and terminal utilities are recovery aids, not a guaranteed
replacement for UART recovery. Use UART/BOLT for installation until Ethernet
recovery is validated for the target device. Slot-1 installation, the integrated
Ethernet installation flow and the latest terminal handoffs require further
hardware validation. Offline tests do not establish device compatibility.

See [docs/DESIGN.md](docs/DESIGN.md) for boot and storage constraints.
