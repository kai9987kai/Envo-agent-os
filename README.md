# Envo Agent OS

Envo Agent OS is a tiny, bootable artificial-life laboratory. A dependency-free
Python builder emits a 16-bit x86 boot sector, a real-mode graphical kernel, a
1.44 MB floppy image, and an El Torito bootable ISO. The kernel renders a
predator-prey simulation directly into VGA Mode 13h, so the complete experiment
boots without a host operating system.

This is an educational and experimental project. It is not a general-purpose
operating system, a secure sandbox, or a scientifically validated ecological
model.

## Project status

- Runtime: 16-bit x86 real mode using BIOS services
- Display: VGA Mode 13h, 320 x 200 pixels
- Recommended host: QEMU on Windows, Linux, or macOS
- Firmware support: legacy BIOS only; UEFI is future work
- Maturity: v1 lineage, but still pre-1.0 in compatibility and support maturity

Run the image in an emulator. Booting experimental media on physical hardware is
unsupported and may hang or behave differently across firmware implementations.

## Quick start

Install Python 3.10 or newer and
[QEMU](https://www.qemu.org/download/), then build into a separate directory:

```text
python create_iso.py --output-dir build --seed 44257
python create_iso.py --output-dir build --seed 44257 --check
qemu-system-i386 -cdrom build/os.iso
```

To boot the floppy image instead:

```text
qemu-system-i386 -fda build/floppy.img
```

The canonical command-line form is:

```text
python create_iso.py [--output-dir DIR] [--seed N] [--check]
```

- `--output-dir DIR` keeps generated files together. The default is the
  repository directory containing `create_iso.py`.
- `--seed N` selects the deterministic simulation seed. Reusing a seed with the
  same source revision and build settings is intended to reproduce the same
  initial world and artifacts. Decimal and `0x`-prefixed hexadecimal values from
  1 through 65535 are accepted; the default is `0xACE1`.
- `--check` is a read-only parity mode. It rebuilds the expected artifact set in
  memory and verifies that the selected output directory already contains
  matching files. Run the build command first; missing, stale, or mixed
  artifacts make the check fail without rewriting them.

## Runtime controls

| Key | Action |
| --- | --- |
| `R` | Restart the world with the configured seed |
| `P` | Pause; press `P` again to resume |

Keyboard handling is BIOS-based and intentionally minimal.

## Build outputs

| Artifact | Purpose |
| --- | --- |
| `boot.bin` | 512-byte BIOS boot sector used by the media images |
| `kernel.bin` | Generated 16-bit simulation kernel |
| `floppy.img` | Bootable 1.44 MB floppy image |
| `os.iso` | El Torito bootable CD-ROM image containing the floppy image |
| `build-manifest.json` | Version, seed, sizes, and SHA-256 digests for the build |

Generated artifacts should be treated as a set. The manifest makes it possible
to detect stale or mixed binaries rather than assuming checked-in media matches
the current source.

## Debug telemetry

The kernel exposes compact binary telemetry through x86 debug port `0xE9`.
QEMU can capture it without adding a serial driver to the guest:

```text
qemu-system-i386 -cdrom build/os.iso -debugcon file:build/telemetry.bin -global isa-debugcon.iobase=0xe9
```

The bootloader and kernel emit the ASCII stage markers `B` and `K`. Each
completed frame then emits four bytes:

```text
0x46 births-low-byte kills-low-byte 0x0A
```

`0x46` is ASCII `F`; the two counter fields are raw bytes, not decimal text.
The telemetry schema is experimental. It is intended for deterministic smoke
tests and host-side model analysis, not as a stable external API.

## Repository map

| Path | Description |
| --- | --- |
| `create_iso.py` | Canonical assembler, kernel generator, media builder, CLI, and checks |
| `boot.asm` | Readable NASM reference for the Python-generated stage-1 loader |
| `MODEL.md` | ODD-style model description, assumptions, and research roadmap |
| `SECURITY.md` | Supported versions, safe-use guidance, and reporting process |
| `boot.bin`, `floppy.img`, `os.iso` | Convenience artifacts; rebuild and verify before relying on them |

## Model scope

The current world contains food, prey, and predators. Agents sense nearby
entities, move, eat, and respond to a day/night cycle and a water region. Prey
have heritable speed and sensing traits. A successful forager carries its own
lineage into the next generation; starvation or predation refills the vacated
slot from an unbiased random prey donor. In both paths, traits have a bounded
chance to mutate and generation is incremented. Variable population size,
predator evolution, quality-diversity search, and habitat disturbances remain
future work. See [MODEL.md](MODEL.md) for the exact rules and roadmap.

## Current limitations

- No protected mode, processes, filesystem, networking, storage driver, or
  hardware abstraction layer
- No UEFI or Secure Boot path
- Fixed 320 x 200 indexed-color display
- Small, fixed-capacity populations and O(n^2) neighborhood scans
- Educational model rather than a calibrated ecological simulation
- Emulator-oriented debug output and timing

## Research and standards

The roadmap is grounded in primary research and current platform specifications:

- Faldor and Cully,
  [Toward Artificial Open-Ended Evolution within Lenia using Quality-Diversity
  (ALIFE 2024)](https://doi.org/10.1162/isal_a_00827)
- Chalumeau et al.,
  [QDax: A Library for Quality-Diversity and Population-based Algorithms with
  Hardware Acceleration (JMLR 2024)](https://www.jmlr.org/papers/v25/23-1027.html)
- Chaparro-Pedraza and Bank,
  [Evolving life-history traits promote biodiversity via eco-evolutionary
  feedback mechanisms (PLOS Biology 2025)](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.3003492)
- Bettini, Shankar, and Prorok,
  [System Neural Diversity (JMLR 2025)](https://www.jmlr.org/papers/v26/24-1477.html)
- [Intel 64 and IA-32 Software Developer's Manuals](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
- [ECMA-119: Volume and file structure of CD-ROM](https://ecma-international.org/publications-and-standards/standards/ecma-119/)
- [QEMU deterministic record/replay](https://qemu.readthedocs.io/en/master/devel/replay.html)
- [UEFI Specification 2.11](https://uefi.org/specifications)

The papers motivate future experiments; they do not imply that Envo Agent OS
reproduces their results.

## License and conduct

The project is licensed under [GPL-3.0](LICENSE). Participation is governed by
the [Code of Conduct](CODE_OF_CONDUCT.md).
