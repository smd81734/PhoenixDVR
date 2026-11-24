# Boot and storage design

The source archive contains the Windows installer, target bootstrap, build tools
and tests. Runtime images and compiled applications are supplied separately.
Project paths in this document are relative to the source archive root.

SSH host discovery uses Windows ssh.exe with Curve25519 key exchange and an
Ed25519 host key. Windows ssh-keyscan builds can advertise an unsupported hybrid
key exchange and fail before authentication. The probe disables credential
authentication, agents, forwarding and remote sessions, and records only the
public host key in a disposable known-hosts file. That discovery file is not a
trusted device identity: updates still compare the fingerprint with the saved
identity or request explicit first-use trust, then connect with strict host-key
checking. Recovery compares the saved fingerprint. Probe files are removed on
success, failure and cancellation; user SSH configuration and known_hosts stay
unchanged.

- Import hash-pinned runtime assets from `inputs/installer.zip` into
  `inputs/runtime` with `tools/import-runtime.py`.
- Preserve the DVR's selected kernel and root slots throughout installer boot,
  dependency harvesting and normal Phoenix boot. Support slots 0 and 1 while
  retaining kernel-version, memory-layout and module-version validation.
- Resolve the selected stock root using verified GPT labels and kernel partition
  geometry. Mount eMMC filesystems read-only without journal replay.
- Use the prepared first FAT32 USB partition for BOLT entry scripts. Run the
  initial Linux environment from RAM. Preserve the original kernel/root selectors
  and verify normal RUNLEVEL before enabling the Phoenix USB boot route.
- Review the internal HDD read-only. Refuse an existing installation and never
  format or repartition the HDD. New installation requires configuration approval.
- Keep TV preferences and recordings on the HDD independently of core upgrades.
- Provide UART terminal handoff and direct Ethernet factory-terminal access.
  Exiting factory mode changes RUNLEVEL only and verifies both environment copies.
- Existing-installation SSH access uses the bundled server in RAM with key-only
  authentication. Private keys and per-device logs stay in the operator's local
  session directory and must never enter release archives.
- Save direct-link addresses, device identity, firmware review and a session token
  locally before changing the boot hook. Reconnect restores the PC address and
  authenticates against the same device. The RAM bridge owns one installer child,
  keeps its input open across disconnects and replays status without retaining
  credential input. It remains available while the RAM environment is powered on.
- Reopening an existing-installation SSH session uses its original key and checks
  the saved host fingerprint. Existing PC addresses are reused only on the saved
  interface and subnet; borrowed addresses are not removed by cleanup.
- Copy dependency trees and manifest entries generically. No individual remote
  map filename is special-cased by the Windows source.
- Required target configuration uses presence-only manifest entries. Its actual
  source/copy hashes are verified locally; firmware and libraries retain their
  reference-hash checks.
- Validate generated payload checksums using LF-only checksum files. Build with
  debug generation disabled and audit new binaries before distribution.
- Repair checkpoints retry only a named operation. USB, network, free-space and
  busy-mount failures pause for a nonce-bound user decision over UART or Ethernet.
  The bridge retains pending repair prompts across reconnects. Existing folders
  must pass the same read-only guard after operator maintenance; never bypass it.
- Once cutover commits the core, repairs only finish boot files, receipts and
  unmounts. Never retry the complete installer after a write-phase failure.
  UART entries explicitly select UART regardless of old network metadata.

Removing USB provides stock fallback only if the original firmware selection is
usable and normal RUNLEVEL is selected. Discovery retries and terminal utilities
are recovery aids; they are not a universal recovery guarantee. Slot-1 installation
and the newest terminal handoffs still require hardware validation.
