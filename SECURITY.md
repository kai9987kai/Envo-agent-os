# Security Policy

## Project maturity

Envo Agent OS is an experimental bootable simulation. The repository has a
`v1` lineage, but its compatibility guarantees, release process, and security
support should still be treated as pre-1.0 maturity.

It is not a secure operating system or isolation boundary. It has no user/kernel
separation, memory protection, privilege model, verified boot chain, or network
security surface. Run generated media in an emulator rather than on physical
hardware.

## Supported versions

| Version | Security support |
| --- | --- |
| Current `main` and latest v1 release | Best-effort fixes |
| Older v1 snapshots | Upgrade to current |
| Pre-v1 snapshots and unversioned artifacts | Not supported |

There is no long-term-support branch or guaranteed response SLA.

## Reporting a vulnerability

Please avoid publishing exploit details before the maintainer has had a chance
to assess them.

1. Use GitHub's private **Report a vulnerability** flow on the repository's
   Security tab when it is available.
2. If private reporting is unavailable, open a minimal public issue asking for
   a private contact channel. Do not include proof-of-concept code, secrets, or
   sensitive crash data in that issue.
3. Include the affected commit or tag, host platform, Python and QEMU versions,
   build command, seed, artifact digest from `build-manifest.json`, observed
   behavior, and the smallest safe reproduction.

Useful reports include:

- memory corruption or unintended code execution in a generated guest image;
- malformed artifacts produced from a clean, trusted checkout;
- build or manifest validation bypasses;
- unsafe handling of untrusted paths or build inputs; and
- a discrepancy between documented and actual security boundaries.

Expected emulator hangs, denial of service inside the guest, missing
general-purpose OS security features, physical-hardware incompatibility, and
ecological-model accuracy are normally out of scope unless they cross the host
boundary or contradict an explicit project guarantee.

## Safe-use guidance

- Build from a trusted checkout, then run the same command with `--check`.
- Compare artifact hashes with `build-manifest.json`.
- Prefer QEMU software emulation with networking disabled.
- Do not attach sensitive disks, host directories, credentials, or devices to
  the virtual machine.
- Do not boot the images on production or safety-critical hardware.
- Treat debug-port telemetry as untrusted diagnostic bytes when consuming it in
  host-side tools.
