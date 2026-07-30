# Envo experiment and telemetry protocol

Envo Agent OS emits versioned experiment records through x86 debug port
`0xE9`. The protocol supports deterministic smoke tests, trace comparison, and
host-side analysis. Release 1.3.0 reports model ABI 4 and uses telemetry
protocol 3. The wire protocol is experimental and may change in a future model
ABI.

## Build and identify an experiment

Select a non-zero 16-bit seed and a telemetry interval:

```text
python create_iso.py --output-dir build --seed 44257 --telemetry-interval 8
```

`--telemetry-interval` must be a power of two from 1 through 32768 and defaults
to 1. It is part of the canonical configuration, so changing it changes the
configuration identity and generated artifacts. Emission preserves guest
registers and never advances the model PRNG, so the interval changes the
observation schedule, not the modeled trajectory. A generated experiment can
be reconstructed without re-entering its parameters:

```text
python create_iso.py --output-dir build-copy --config build/experiment.json
```

Each build emits `experiment.json` beside the media and embeds the same bytes in
`os.iso` as `/EXPERIMENT.JSON;1`. The document records:

- every validated model parameter, including the seed and observation interval;
- the guest memory layout and model ABI version;
- the telemetry version, frame layout, field order, and checksum rule;
- a 64-character `config_sha256`; and
- the corresponding numeric and hexadecimal 16-bit `config_id`.

`config_sha256` is SHA-256 over the canonical, sorted, compact JSON encoding of
the model ABI version, complete configuration, runtime layout, and telemetry
descriptor. It does not identify the source revision by itself; retain the
source revision and `build-manifest.json` artifact digests with published runs.

The full SHA-256 is the authoritative experiment identity. `config_id` is the
little-endian interpretation of the digest's first two bytes. It is deliberately
small enough for the guest frame, so unrelated configurations can share it.
Before comparing runs, require equal full hashes and artifact provenance. The
host tools then check the short ID in every frame as a fast mismatch guard;
`--experiment` does not prove full-hash equality on its own.

## Stream structure

The boot sector writes ASCII `B` when stage 1 starts. The kernel writes ASCII
`K` after establishing its real-mode execution environment. Binary version 3
frames follow completed simulation updates at the selected interval. Frame
construction is observational: it does not advance the PRNG or mutate the
canonical entity region.

Consumers should scan for the two-byte magic `EV`, validate the complete header
and checksum, and resume scanning one byte later if a candidate is corrupt.
`FrameStreamParser` in `envo_telemetry.py` implements this behavior for complete
files and arbitrarily split streaming input.

## Version 3 frame

Every frame is exactly 48 bytes: a five-byte header, a 42-byte payload, and a
one-byte checksum. Multibyte payload values are unsigned 16-bit integers in
little-endian order. The payload encoding is `<9H4B7H4BH`.

Version 3 preserves all 26 bytes of the version 2 payload at offsets 5 through
30, then appends predator fields. This is payload-prefix compatibility for
analysis and migration, not wire compatibility: the version and payload-length
header bytes changed, the checksum moved, and a version 2 decoder must reject a
version 3 frame.

| Offset | Size | Field | Meaning |
| ---: | ---: | --- | --- |
| 0 | 2 | magic | ASCII `EV` |
| 2 | 1 | version | Protocol version `3` |
| 3 | 1 | frame_type | Observation frame `1` |
| 4 | 1 | payload_length | `42` bytes |
| 5 | 2 | `config_id` | Little-endian 16-bit prefix of the configuration SHA-256 |
| 7 | 2 | tick | Completed simulation tick, wrapping at 65536 |
| 9 | 2 | `rng_state` | xorshift16 state after the completed update |
| 11 | 2 | replacements | All prey-slot lineage replacements |
| 13 | 2 | captures | Replacements caused by predators |
| 15 | 2 | starvations | Prey replacements caused by energy less than or equal to current metabolic cost |
| 17 | 2 | `forager_turnovers` | Self-lineage replacements at the energy threshold |
| 19 | 2 | `prey_energy_sum` | Current energy summed across all prey slots |
| 21 | 2 | `prey_sense_sum` | Current sensing trait summed across all prey slots |
| 23 | 1 | `speed_1_count` | Prey whose speed trait is 1 |
| 24 | 1 | `speed_2_count` | Prey whose speed trait is 2 |
| 25 | 1 | `speed_3_count` | Prey whose speed trait is 3 |
| 26 | 1 | `speed_4_count` | Prey whose speed trait is 4 |
| 27 | 2 | `max_generation` | Largest generation among current prey slots |
| 29 | 2 | `state_checksum` | Wrapping sum of the canonical entity region |
| 31 | 2 | `predator_replacements` | All predator-slot lineage replacements |
| 33 | 2 | `predator_starvations` | Predator replacements caused by energy less than or equal to current metabolic cost |
| 35 | 2 | `predator_forager_turnovers` | Predator self-lineage replacements at its capture-fuelled threshold |
| 37 | 2 | `predator_energy_sum` | Current energy summed across all predator slots |
| 39 | 2 | `predator_sense_sum` | Current sensing trait summed across all predator slots |
| 41 | 1 | `predator_speed_1_count` | Predators whose speed trait is 1 |
| 42 | 1 | `predator_speed_2_count` | Predators whose speed trait is 2 |
| 43 | 1 | `predator_speed_3_count` | Predators whose speed trait is 3 |
| 44 | 1 | `predator_speed_4_count` | Predators whose speed trait is 4 |
| 45 | 2 | `predator_max_generation` | Largest generation among current predator slots |
| 47 | 1 | checksum | Two's-complement additive frame checksum |

The sum of bytes 2 through 47 modulo 256 must be zero. The magic is excluded
from this checksum so a decoder can search for it before validating a
candidate.

`state_checksum` is the wrapping sum of every 16-bit word from `data_base`
inclusive to `data_end` exclusive after that tick's completed model update.
This canonical region contains every food record, including its padding word,
then all six words of every prey and predator record. It excludes runtime
scratch counters, the telemetry buffer, and VGA memory. It is a compact
trajectory-divergence detector, not a cryptographic digest.

### Model semantics represented by the counters

Both species use the same exact trait-dependent metabolic-cost form:

```text
cost =
    species_base_metabolism
    + (speed - speed_min) * speed_metabolism
    + ((sense - sense_min) >> sense_metabolism_shift)
```

The shift is integer floor division by
`2 ** sense_metabolism_shift`. Prey pay this cost only during the active phase;
predators pay it every tick. If an agent's energy is less than or equal to its
cost, its species-specific starvation counter increments and the fixed slot
turns over without first subtracting the cost.

Prey that reach `reproduction_energy` through a food encounter preserve their
own lineage. Captured or starved prey inherit from a random prey donor.
Predators receive `predator_capture_energy` after a capture. If the resulting
energy reaches `predator_reproduction_energy`, the successful predator
preserves its own lineage; a starved predator inherits from a random predator
donor. Each turnover resets position and energy, may mutate inherited speed and
sensing, increments generation, and increments the relevant replacement
counter.

The energy sums are state observations, not a trophic mass-balance ledger.
Food grants configured prey energy, capture grants a fixed predator reward, and
slot turnover resets energy to `initial_energy`. Consequently, neither an
individual-frame nor an adjacent-frame conservation equation is valid.

### Accounting invariants

The 16-bit tick, PRNG state, counters, generation, sums, and state checksum use
guest word arithmetic. Tick and cumulative counters wrap at 65536. Host
analysis should compute adjacent counter and tick deltas modulo 65536. The prey
speed bins cannot overflow under the supported maximum of 32 prey, and the
predator bins cannot overflow under the supported maximum of 16 predators.

The following identities must hold modulo 65536:

```text
replacements = captures + starvations + forager_turnovers
predator_replacements =
    predator_starvations + predator_forager_turnovers
```

Additional per-frame invariants are:

```text
speed_1_count + speed_2_count + speed_3_count + speed_4_count
    = configured prey_count

predator_speed_1_count + predator_speed_2_count
    + predator_speed_3_count + predator_speed_4_count
    = configured predator_count

prey_energy_sum
    <= reproduction_energy * configured prey_count

predator_energy_sum
    <= predator_reproduction_energy * configured predator_count
```

Configuration validation ensures the energy-sum bounds fit in 16 bits. It also
keeps both sense and speed trait differences in the cost formula non-negative,
and bounds the maximum cost below signed model limits. These invariants detect
implementation or trace errors; they do not establish ecological validity.

## Capture and inspect

The headless runner validates ordered boot stages and the experiment's short
configuration ID while waiting for checksum-valid frames:

```text
python run_experiment.py build/os.iso --experiment build/experiment.json --output build/telemetry.bin --frames 10
```

To inspect a previously captured stream and reject any corrupt or truncated
candidate frame:

```text
python envo_telemetry.py TRACE --experiment experiment.json --strict
python envo_telemetry.py TRACE --experiment experiment.json --format jsonl
```

The default output is a JSON summary. `--format jsonl` emits all decoded fields,
one frame per line. Both modes ignore unrelated bytes such as the `B` and `K`
stage markers. The lenient parser can resynchronize after corrupt candidate
frames. Strict mode skips unrelated bytes before candidates, but fails on a
malformed or checksum-invalid `EV` candidate, a partial final frame, or a
trailing partial magic prefix.

## Compare host traces

`envo_telemetry.py` exposes incremental parsing for streamed input and
positional trace comparison for complete captures. Compare full experiment
identities first, then compare frames:

```python
import json
from pathlib import Path

from envo_telemetry import compare_trace_bytes

left_experiment = json.loads(Path("left/experiment.json").read_text())
right_experiment = json.loads(Path("right/experiment.json").read_text())
assert left_experiment["config_sha256"] == right_experiment["config_sha256"]

comparison = compare_trace_bytes(
    Path("left/telemetry.bin").read_bytes(),
    Path("right/telemetry.bin").read_bytes(),
    strict=True,
)
assert comparison.equal, comparison.mismatches[0]
```

`compare_trace_bytes` reports differing fields at each frame position and
detects missing frames. Equality is an implementation-level trajectory check,
not evidence that the model is ecologically valid. Controlled reports should
also preserve the source revision, artifact hashes, QEMU version and command,
termination rule or frame count, and whether keyboard input occurred. QEMU's
[record/replay support](https://qemu.readthedocs.io/en/master/devel/replay.html)
can control additional emulator inputs.

For a fresh-from-boot capture that has not crossed the 16-bit tick wrap, compare
the guest directly with the implemented Python reference:

```python
from pathlib import Path

from envo_config import load_model_config
from envo_reference import ReferenceModel
from envo_telemetry import assert_traces_equal, parse_frames

config = load_model_config(Path("experiment.json"))
guest = parse_frames(Path("telemetry.bin").read_bytes(), strict=True)
reference = ReferenceModel(config).run(guest[-1].tick)
assert_traces_equal(guest, reference)
```

The reference mirrors the guest's instruction ordering and integer semantics.
This makes it a strong differential-testing oracle for the implemented model,
but not an independent ecological validation.

## Research rationale and boundary

The implemented contract responds to recent replication evidence without
claiming to implement the cited models:

- Grimm et al.'s
  [2025 replication experiment](https://doi.org/10.1016/j.ecolmodel.2024.110967)
  reproduced exemplary results for 15 of 18 agent-based models and found that
  ODD descriptions helped systematically expose missing or ambiguous detail.
  This motivates keeping [MODEL.md](MODEL.md) and the machine-readable
  experiment description together.
- Fachada et al.'s
  [2026 ODD-based replication
  study](https://doi.org/10.1016/j.ecolmodel.2026.111624) found that executable
  implementations can still be behaviorally wrong and used statistical
  comparison with a validated reference model. The `EV` frames, host comparator,
  and implemented Envo reference support exact differential verification, but
  multi-seed statistical validation remains future work.
- Bagic et al.'s
  [AEGIS framework](https://doi.org/10.1371/journal.pcbi.1014109) records
  cause-specific demographic, individual, phenotypic, and genomic outputs over
  time. Envo v1.3 now reports aggregate replacement causes, trait sums, speed
  bins, and maximum current generation separately for prey and predators; it
  still has no age structure, individual histories, or genomes.
- Huang et al.
  [experimentally and computationally studied dynamic predator-prey
  trade-offs](https://doi.org/10.1038/s41467-017-01957-8), while Salahshour
  [modeled explicit internal resources, metabolic costs, capture gains, and
  reproduction](https://doi.org/10.1088/1367-2630/adaedd). These results
  motivate observing costs and lineage turnover at both trophic levels. Envo's
  fixed-slot counters and aggregate energy sums do not reproduce the cited
  mechanisms or demonstrate their reported outcomes.
- Moreno, Rodriguez-Papa, and Dolson
  [showed in 2025](https://doi.org/10.1162/artl_a_00470) that ecology, spatial
  structure, and selection pressure can leave complex signatures in
  computational phylogenies, and that low-resolution reconstructions can bias
  some metrics. `max_generation` is not a phylogeny. Parent identifiers,
  lineage-event records, and an independently checked ancestry reconstruction
  remain future work.

## Limitations

- Tick and cumulative event fields wrap at 65536; naive subtraction across the
  boundary is wrong. Per-agent generation words also wrap, so
  `max_generation` is not a lifetime lineage-depth measure after wraparound.
- The 16-bit `config_id` can collide. Equal prefixes are accepted by
  `--experiment`, so compare the full `config_sha256` and artifact provenance
  separately.
- The additive frame checksum and `state_checksum` detect common divergence or
  corruption but are collision-prone and non-cryptographic.
- Debug port `0xE9`, emulator timing, and this telemetry schema are experimental
  interfaces, not a stable hardware or public API.
- A fixed-seed trace match checks reproducibility for that trajectory. It does
  not replace replicated multi-seed experiments, statistical output comparison,
  sensitivity analysis, or ecological calibration.
