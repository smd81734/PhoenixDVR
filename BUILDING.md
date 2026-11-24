# Building Phoenix Device Setup

Run commands from the repository root. All project paths below are relative to it.

## Requirements

- 64-bit Windows with Windows PowerShell and .NET Framework 4.x build tools.
- Python 3.12 or later, available as `python`.
- Windows OpenSSH Client for key-generation tests.
- Git Bash for shell integration tests. It is discovered through PATH or Git.

## Runtime inputs

Place a compatible installer archive at `inputs/installer.zip`, then import its
runtime dependencies:

```powershell
python tools/import-runtime.py --archive inputs/installer.zip
```

The importer verifies the asset hashes in `BASELINE.json` and writes them to
`inputs/runtime`. The `runtime/` directory contains the editable application and
provisioning source used when assembling those dependencies.

## Build

```powershell
python tools/build-from-baseline.py --output build/usb-payload
powershell -NoProfile -File tools/build.ps1 -Output build -Python python
```

The Windows executable is `build/PhoenixSetup.exe`. Its USB payload is under
`build/usb-payload`. The build script uses an optimized release configuration and
runs the C#, GUI and USB-formatting checks.

C# builds use `/optimize+ /debug-`. Native builds use optimization, `-g0` and
compiler prefix maps for reproducible source paths; retain symbols required by
runtime loaders. Distribute release binaries without separate debug files.

## Test

```powershell
python -B -m unittest discover -s tests
```

The tests cover boot selection, dependency discovery, storage guards, transport
protocols and package validation. Hardware-dependent installation paths require
separate device testing.

## Package

```powershell
python tools/package-distribution.py --exe build/PhoenixSetup.exe --payload build/usb-payload --source . --upstream inputs/installer.zip --destination dist --name phoenix-setup
python tools/package-source.py --output dist/phoenix-source.zip
```

The distribution tool produces an installer ZIP and a separate source ZIP. Binary
dependencies are supplied separately from source. Package checks verify file
hashes and executable format metadata. Upstream artifact records are in
`tools/baseline-upstream-exceptions.json`.
Use `--update path/to/phoenix-update-VERSION.tar.gz` to include a signed update
beside the EXE for the guided update flow. The packager verifies its signature
against the project's public key before including it. Helper scripts in `scripts/`
are required application files and are included automatically.

The Phoenix DVR native and application sources are provided in the separate
`phoenix-dvr-source` archive. Its `BUILDING.md` covers the Linux cross-compilation
environment and rebuilding the runtime. This Windows project assembles runtime
inputs; it does not compile the DVR's native components.

## Apply updates to an installed device

The **Apply update** tab uses the household LAN and the device's current IPv4
address. It looks for prior installation/update logs to recover the original SSH
key and host fingerprint. Installation transport addresses are not assumed to be
current LAN addresses. Users can enter an address and a root password/key
passphrase, or choose an OpenSSH private key. Passwords remain in memory and are
passed to OpenSSH through a private current-user pipe; they are not stored in
logs, command lines or session JSON.

Choose a Phoenix update `.tar.gz`, then **Connect and stage update**. The GUI pins
the SSH host identity before authentication. A custom publisher public key can be
selected explicitly; unsigned packages require a separate risk acknowledgement.
A failed signature is rejected. After staging, **Apply staged update** checks
Tvheadend and asks for confirmation. The device rechecks active/imminent
recordings immediately before shutdown and blocks activation if status is unknown.

The worker is shipped in `scripts/update.py` beside the EXE and checked against
its compiled SHA-256 before use; an existing installation does not need a
previously installed updater. Keep the complete extracted ZIP together, including
both helper scripts. SSH transfer uses standard input, so the box does
not need an SFTP server. The signed package is built using the separate DVR source
project's `tools/package-update.py`. Base-image/firmware updates and settings-schema
migrations are outside update format 1.

Keep the device powered and its normal boot USB connected. Detached staging and
activation continue after PC disconnects. Reopen the saved session using **Load
previous installation details**, then **Check update status**. Settings snapshots
and the previous core stay privately on the HDD; rollback retains current
recordings, settings and credentials. Rollback requires SSH access; an early boot
failure or unreachable device still needs recovery. No hardware-proof or automatic
watchdog recovery is implied by host tests.

## Runtime file formats

Device scripts use UTF-8 and LF line endings. `SHA256SUMS` uses ASCII, LF endings
and no byte-order mark. The build tools generate these files automatically.
Linux runtime paths are absolute where required by the DVR's operating system.
