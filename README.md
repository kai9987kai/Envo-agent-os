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
- Experiment interface: release 1.2.0, model ABI 3, `EV` telemetry version 2
- Maturity: experimental; compatibility and scientific validation are evolving

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

For a headless, machine-checked experiment:

```text
python run_experiment.py build/os.iso --experiment build/experiment.json --output build/telemetry.bin --frames 2
```

To boot the floppy image instead:

```text
qemu-system-i386 -fda build/floppy.img
```

The canonical command-line form is:

```text
python create_iso.py [--output-dir DIR] [--config FILE] [--seed N]
                     [--telemetry-interval N] [--check]
```

- `--output-dir DIR` keeps generated files together. The default is the
  repository directory containing `create_iso.py`.
- `--seed N` selects the deterministic simulation seed. Reusing a seed with the
  same source revision and model configuration is intended to reproduce the
  same initial world and artifacts. Decimal and `0x`-prefixed hexadecimal values
  from 1 through 65535 are accepted; the default is `0xACE1`.
- `--config FILE` accepts a complete model-configuration object or rebuilds from
  a generated `experiment.json`. `--seed` and `--telemetry-interval` can
  explicitly override those two fields.
- `--telemetry-interval N` emits one observation every N ticks. N must be a
  power of two from 1 through 32768; the default is 1. It changes the
  observation schedule and experiment identity, not the modeled trajectory.
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
| `os.iso` | El Torito CD-ROM image containing the floppy and experiment identity |
| `experiment.json` | Canonical model parameters, memory layout, telemetry ABI, and configuration ID |
| `build-manifest.json` | Version, configuration identity, sizes, and SHA-256 artifact digests |

Generated artifacts should be treated as a set. The manifest makes it possible
to detect stale or mixed binaries rather than assuming checked-in media matches
the current source. The same experiment document is available inside `os.iso`
as `/EXPERIMENT.JSON;1`.

## Reproducible experiments

Every build hashes the canonical model parameters, runtime layout, and telemetry
descriptor into a full SHA-256 configuration identity and a compact 16-bit
`config_id`. The exact experiment document is emitted beside the images and
embedded in the ISO. The kernel includes that ID in every telemetry record so
routine configuration mismatches are detected before analysis. The full hash
remains authoritative: the short ID is only the little-endian interpretation of
the digest's first two bytes and can collide. Require equal full hashes and
artifact provenance before comparing experiments, then use the short ID to
detect an accidentally mismatched trace.

The easiest capture path boots QEMU without a display, waits for checksummed
frames, verifies their configuration ID, and prints a JSON summary:

```text
python run_experiment.py build/os.iso --frames 10 --timeout 60 --output build/telemetry.bin --json-out build/summary.json
```

Existing traces can be inspected or compared independently:

```text
python envo_telemetry.py build/telemetry.bin --strict --experiment build/experiment.json
python envo_telemetry.py build/telemetry.bin --format jsonl
```

The dependency-free reference model mirrors the guest's instruction order and
emits the same wire records without booting an emulator:

```text
python envo_reference.py --config build/experiment.json --ticks 10
```

CI differentially compares live floppy and ISO frames against this reference,
in addition to checking parity between both boot paths.

The reference is a verification oracle for the current mechanics, not an
independently validated ecological model. Multi-seed statistical validation and
MAP-Elites experiments remain roadmap work.

Version 2 frames contain the tick and PRNG state, lineage replacements by
cause, prey energy and sensing sums, the complete speed histogram, maximum
generation, and a checksum of canonical guest state. Framing, checksums, and
stream resynchronization, host comparison, identity rules, and limitations are
specified in [TELEMETRY.md](TELEMETRY.md). The schema remains experimental
rather than a stable external API.

## Repository map

| Path | Description |
| --- | --- |
| `create_iso.py` | Canonical assembler, kernel generator, media builder, CLI, and checks |
| `envo_config.py` | Validated model configuration, memory layout, and experiment identity |
| `envo_reference.py` | Exact host-side mirror for deterministic differential testing |
| `envo_telemetry.py` | Streaming frame parser, trace comparison, summaries, and CLI |
| `run_experiment.py` | Headless QEMU runner with frame and configuration validation |
| `boot.asm` | Readable NASM reference for the Python-generated stage-1 loader |
| `MODEL.md` | ODD-style model description, assumptions, and research roadmap |
| `TELEMETRY.md` | Version 2 binary telemetry protocol |
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
- Emulator-oriented timing and an experimental telemetry ABI with wrapping
  16-bit counters

## Research and standards

The roadmap is grounded in primary research and current platform specifications:

- Grimm et al.,
  [Using the ODD protocol and NetLogo to replicate agent-based models
  (Ecological Modelling
  2025)](https://doi.org/10.1016/j.ecolmodel.2024.110967)
- Fachada et al.,
  [Can large language models implement agent-based models? An ODD-based
  replication study (Ecological Modelling
  2026)](https://doi.org/10.1016/j.ecolmodel.2026.111624)
- Bagic et al.,
  [AEGIS: Individual-based modeling of life history evolution
  (PLOS Computational Biology
  2026)](https://doi.org/10.1371/journal.pcbi.1014109)
- Moreno, Rodriguez-Papa, and Dolson,
  [Ecology, Spatial Structure, and Selection Pressure Induce Strong Signatures
  in Phylogenetic Structure (Artificial Life
  2025)](https://doi.org/10.1162/artl_a_00470)
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

The replication papers motivate the implemented description and observation
surfaces. The life-history, phylogeny, diversity, and quality-diversity papers
motivate future experiments. None implies that Envo Agent OS reproduces their
models or results.

## License and conduct

The project is licensed under [GPL-3.0](LICENSE). Participation is governed by
the [Code of Conduct](CODE_OF_CONDUCT.md).
