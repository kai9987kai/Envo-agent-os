# Envo Agent OS model

This document describes the current simulation and release 1.3.0 experiment
system (model ABI 4, telemetry protocol 3) using a concise adaptation of the ODD
(Overview, Design concepts, Details) protocol. It separates implemented
behavior from research-informed future work. The model is an educational
artificial-life system, not a calibrated representation of a natural ecosystem.

## 1. Overview

### 1.1 Purpose

The model demonstrates how a small set of local agent rules can be assembled
into a bootable 16-bit x86 program and visualized without a host operating
system. Its engineering goals are deterministic builds, repeatable initial
conditions, and observable behavior in an emulator.

The current scientific question is intentionally modest: what
population-scale motion, energy distributions, encounters, and heritable trait
turnover emerge from costed prey and predator behavior under a simple
day/night cycle and a spatial water region?

### 1.2 Entities, state variables, and scales

The world is a 320 x 200 discrete pixel plane rendered in VGA Mode 13h.

| Entity | Count | State |
| --- | ---: | --- |
| Food | 50 by default | x, y, storage padding |
| Prey | 30 by default | x, y, energy, speed trait, sensing trait, generation |
| Predator | 4 by default | x, y, energy, speed trait, sensing trait, generation |

Coordinates and entity fields are 16-bit integers. The lower 30 rows form the
default water region. A global 16-bit tick counter drives the day/night phase.
Population capacities and model constants are selected at build time from a
validated experiment configuration, then remain fixed for that guest run.

### 1.3 Process overview and scheduling

One simulation tick performs the following operations in order:

1. Increment time and select the day or night phase.
2. Poll BIOS keyboard input.
3. Clear and redraw the background, water, and day/night markers.
4. Draw food.
5. Update and draw each prey agent.
6. Update and draw each predator.
7. Emit telemetry if the tick matches the configured observation interval.
8. Apply a busy-loop frame delay.

The ordering is sequential, so agents updated later in a tick can observe
positions changed earlier in that tick. This is a model assumption, not
simultaneous ecological time.

## 2. Design concepts

### 2.1 Basic principles

Behavior is local and rule-based. Prey compare Manhattan-like distance to
predators and food; predators search for the closest prey. The simulation favors
transparent integer rules that fit in a small real-mode kernel.

### 2.2 Emergence

Population-level motion, clustering, pursuit, escape, and encounter patterns are
not scripted as global animations. However, the current model does not claim
open-ended evolution, ecological equilibrium, or lifelike complexity.

### 2.3 Adaptation and evolution

Prey and predator speed and sensing range are heritable traits in a
fixed-population turnover model. A prey feeding event that reaches the prey
reproduction threshold retains that successful forager's lineage when its slot
advances to the next generation. Prey starvation or capture instead refills the
vacated slot from an unbiased random prey donor selected from all configured
prey slots; the destination can select itself.

Predators spend trait-dependent energy every tick and gain a fixed configured
energy reward after a capture. A capture that reaches the predator reproduction
threshold retains that successful predator's lineage. Predator starvation
instead refills the slot from an unbiased random predator donor, again including
the destination itself. The two species use separate replacement and
cause-specific counters.

For either species, speed and sensing are inherited and mutate independently
with probability `1 / (mutation_mask + 1)`, one in four by default. A selected
mutation moves speed by one or sensing by `sense_mutation_step`, with an
independent random direction and configured trait bounds. Generation is the
parent's generation plus one. Trait frequencies can therefore change while both
population sizes remain constant.

This is compact asexual lineage replacement, not demographic reproduction:
there are no empty slots, mating, variable population size, age structure,
parental energy transfer, or explicit offspring.

### 2.4 Objectives and fitness

Agents have no explicit optimization objective. Prey spend trait-dependent
energy while active, recover during night rest, and gain energy from food.
Predators spend trait-dependent energy every tick and gain energy after
captures. Energy boundaries trigger lineage replacement after starvation or
successful feeding, so resource acquisition, pursuit, and avoidance can affect
which trait lineages persist. No scientific fitness score is reported, and the
implemented mechanism has not yet been shown to produce sustained adaptation or
reciprocal evolutionary change across replicated runs.

### 2.5 Sensing and interaction

- Prey scan predators and flee when one falls within their sensing trait.
- Otherwise prey wander and scan food for close collisions.
- Predators scan prey, pursue the closest visible target, and otherwise wander.
- Food is relocated after consumption.
- Captured prey slots are replaced through the lineage-turnover rule.
- Predator captures supply a fixed energy reward that can trigger successful
  predator-lineage turnover.
- Water modifies movement near the bottom of the world.
- Prey rest during the night phase.

All scans are bounded exhaustive searches. Runtime cost therefore grows
quadratically with population capacity.

### 2.6 Stochasticity and repeatability

The canonical build validates all trajectory-affecting compile-time inputs as a
`ModelConfig`. Its canonical JSON representation, runtime memory layout, and
telemetry ABI are hashed together. `experiment.json` records the full SHA-256
identity and a compact configuration ID carried in each telemetry frame.
With the same source revision, rebuilding from that document is intended to
produce byte-identical artifacts and the same guest trajectory in deterministic
emulation. The full SHA-256 is authoritative; the 16-bit frame ID is only a
collision-prone prefix used for fast mismatch detection.

`build-manifest.json` records the identity and artifact digests. Repeatability
across different emulator versions or timing modes must be verified rather than
assumed; QEMU's
[record/replay support](https://qemu.readthedocs.io/en/master/devel/replay.html)
is the preferred basis for controlled runs.

Only modeled stochastic events advance the xorshift16 state. Night fireflies
derive their cosmetic positions from the tick and draw-loop index, and telemetry
does not advance the PRNG. Changing `telemetry_interval` therefore changes the
observation schedule and configuration identity, but not the modeled state
trajectory for an otherwise identical run.

### 2.7 Observation

VGA output is the human-facing observation surface. Checksummed, fixed-size
records on x86 debug port `0xE9` provide a machine-facing surface for smoke
tests, trajectory comparison, and host-side analysis. The boot and kernel
stages emit ASCII `B` and `K` markers. Version 3 observation frames preserve the
version 2 prey payload prefix and append predator replacement causes, energy and
sensing sums, the full predator speed histogram, and predator maximum
generation. The frames also report the configuration ID, tick and PRNG state,
prey metrics, and a compact checksum over canonical entity state. The
configured observation interval is a power of two from 1 through 32768 ticks.
See
[TELEMETRY.md](TELEMETRY.md) for the experiment identity, wire layout, host
parser and comparison workflow, and wraparound rules.

## 3. Details

### 3.1 Initialization

The BIOS boot sector loads the generated kernel from the floppy-emulation image
into low memory and transfers control to it. The kernel establishes real-mode
segments and stack state, enters VGA Mode 13h, initializes the configured random
seed, places entities, and begins the tick loop.

By default prey begin with speed 2, sensing range 50, energy 100, and generation
zero; predators begin with speed 3, sensing range 100, and energy 100. These
values can be changed in a validated build configuration. Subsequent prey and
predator replacements inherit and can mutate speed and sensing; initial values
remain model parameters, not evolved outcomes.

### 3.2 Input data

The guest consumes no external datasets. Its model inputs are the canonical
compile-time experiment configuration and runtime `R`/`P` keyboard events. The
generated `experiment.json` can be supplied to `create_iso.py --config` to
reconstruct those inputs.

### 3.3 Environmental submodel

A configured single-bit tick mask alternates day and night. The phase changes
background rendering and prey activity. Water occupies a configured spatial
band. Resource growth, seasons, drought, refuges, and habitat-quality gradients
are not implemented.

### 3.4 Prey submodel

During the active phase, each prey first computes its integer metabolic cost:

```text
prey_cost =
    prey_base_metabolism
    + (speed - speed_min) * speed_metabolism
    + ((sense - sense_min) >> sense_metabolism_shift)
```

The right shift is floor division by
`2 ** sense_metabolism_shift`. Configuration validation keeps both trait
differences non-negative. With release defaults, this is
`1 + (speed - 1) + floor((sense - 24) / 32)`; the initial prey therefore costs
2 energy per active tick. If current energy is less than or equal to the cost,
the prey starves and turns over without first subtracting it. Otherwise the
cost is subtracted, then the prey scans predators.

A nearby predator causes movement away from that predator using the prey speed
trait. With no nearby predator, the prey performs a symmetric random walk whose
magnitude is its speed. Its coordinates are clamped to drawable bounds. It is
pushed upward in deep water, scans food, and gains `food_energy` after a close
encounter; that food item is then relocated. During night rest, prey do not pay
metabolism or move and recover one energy unit per tick up to the prey
reproduction threshold.

Starvation and predator capture each trigger fixed-slot lineage replacement. A
food encounter also triggers replacement when the gained energy reaches or
exceeds `reproduction_energy`; night recovery alone does not. Every replacement
randomizes the slot position, resets it to `initial_energy`, inherits and
possibly mutates both traits, and increments generation. The feeding-threshold
event inherits from that successful forager. Starvation and capture inherit from
an unbiased random prey donor selected from every prey slot, including the
destination itself.

### 3.5 Predator submodel

Every tick, before hunting, each predator computes:

```text
predator_cost =
    predator_base_metabolism
    + (speed - speed_min) * speed_metabolism
    + ((sense - sense_min) >> sense_metabolism_shift)
```

With release defaults, the initial predator costs 5 energy per tick. If current
energy is less than or equal to the cost, the predator starvation counter is
incremented and that slot is replaced from a random predator donor. Otherwise
the cost is subtracted and the predator scans all prey, retaining the closest
target. If that target is within the predator sensing trait, the predator moves
by its speed trait along each differing axis; otherwise it wanders.

A close encounter increments the prey capture counter and replaces the captured
prey slot using the prey turnover rule. The predator then gains the fixed
`predator_capture_energy` reward, 40 by default. If its energy is now greater
than or equal to `predator_reproduction_energy`, 160 by default, the predator
forager-turnover counter is incremented and the predator replaces its own slot:
position and energy are reset, its own speed and sensing are inherited with
bounded mutation, and generation advances. Otherwise it retains the gained
energy. Each predator can complete at most one capture in its update.

Predator starvation instead chooses an unbiased random donor from all predator
slots, including the destination, before applying the same inheritance and
mutation rules. Thus capture success can change predator trait frequencies, but
this remains fixed-slot lineage turnover. The capture reward is not the
captured prey's actual energy, and resets create or discard energy; the model
does not claim trophic energy conservation.

### 3.6 Display and timing submodel

The kernel redraws the full framebuffer every tick, then delays with a CPU-bound
loop. Tick duration therefore depends on emulator configuration and is not a
physical time unit. Quantitative experiments should compare tick counts, not
wall-clock time.

## 4. Validity and testable invariants

The implementation should continuously check these invariants:

- identical source and canonical configuration produce identical artifact hashes;
- every compared experiment has the same full configuration SHA-256 and
  artifact provenance;
- every trace frame matches the experiment's 16-bit configuration prefix and
  frame checksum;
- prey replacement causes account for the prey replacement counter modulo
  65536:
  `replacements = captures + starvations + forager_turnovers`;
- predator replacement causes account for the predator replacement counter
  modulo 65536:
  `predator_replacements = predator_starvations +
  predator_forager_turnovers`;
- prey and predator speed-histogram counts separately sum to their configured
  fixed population sizes;
- reported prey and predator energy sums do not exceed the corresponding
  reproduction threshold times the configured population size;
- every agent's computed metabolic cost follows the configured formula and is
  positive and representable as a signed 16-bit model value;
- the boot sector is exactly 512 bytes and ends in signature `0xAA55`;
- the kernel fits within the number of sectors the bootloader reads;
- every assembled relative branch is in range and every label resolves;
- agent coordinates remain within drawable bounds;
- entity scans never leave their allocated state regions;
- media descriptors and boot catalogs pass structural validation; and
- a fixed-seed QEMU run produces the expected telemetry and framebuffer
  checkpoints.

Passing these checks establishes implementation repeatability, not ecological
validity.

## 5. Research-informed roadmap

Status is explicit below. Research citations motivate tests and mechanisms; they
do not transfer ecological validity or published results to Envo Agent OS. The
near-term order is multi-seed statistical validation of the costed fixed-slot
model, energy-conserving variable demography, then host-side MAP-Elites
experiments.

### 5.1 Experiment identity and observation ABI - extended in release 1.3.0

The builder now emits a machine-readable `experiment.json`, places the same
document at `/EXPERIMENT.JSON;1` in the ISO, and binds 48-byte `EV` v3 records
to its 16-bit configuration prefix. The experiment document reports model ABI
4. Version 3 retains the complete version 2 payload as an offset-stable prefix,
then appends predator counters and trait aggregates. Power-of-two telemetry
intervals, full configuration hashes, checksummed frames, canonical
entity-state checkpoints, strict parsing, summaries, and positional trace
comparison are implemented. Rendering and telemetry are trajectory-neutral.
The full contract and its compatibility, collision, and wraparound limits are
specified in [TELEMETRY.md](TELEMETRY.md).

This work targets transparent implementation verification. Grimm et al.'s
[2025 replication experiment](https://doi.org/10.1016/j.ecolmodel.2024.110967)
found ODD useful in a study that reproduced exemplary results for 15 of 18
models. Fachada et al.'s
[2026 ODD-based study](https://doi.org/10.1016/j.ecolmodel.2026.111624)
demonstrated that executable code can still be behaviorally wrong and used
statistical comparison against a validated reference. Envo now uses its ABI for
exact short-horizon differential checks against a host reference. Multi-seed
statistical validation remains future work.

### 5.2 Costed predator-prey lineage turnover - implemented in release 1.3.0

Release 1.3 makes the existing speed and sensing benefits costly through the
exact integer metabolic formula in sections 3.4 and 3.5. It also activates
predator metabolism, capture energy, starvation, inheritance, bounded mutation,
successful-forager turnover, generation tracking, and cause-specific telemetry.
This supplies reciprocal selection pathways without adding a global fitness
function: prey traits affect avoidance and predator capture opportunities,
while predator traits affect pursuit and prey survival.

[Huang et al., Nature Communications
2017](https://doi.org/10.1038/s41467-017-01957-8) experimentally and
computationally showed that predator-prey coevolution can change the shape of
growth-defense trade-offs and resulting diversity. [Salahshour, New Journal of
Physics
2025](https://doi.org/10.1088/1367-2630/adaedd) used explicit internal
resources, metabolic expenditure, capture gains, and resource-dependent
reproduction in a spatial predator-prey model. These papers motivate testing
costed reciprocal turnover and observing both trophic levels; Envo's fixed
integer rewards and slot resets are not implementations of either published
model.

### 5.3 Energy-conserving demography and life histories - next

Replace fixed-slot turnover with explicit empty slots, variable population
size, and energy-conserving birth/death accounting. Birth should transfer
parental energy to offspring, and predation should distinguish assimilated
energy from loss rather than grant a fixed reward. Then consider age,
maturation, heritable reproductive investment, and a niche trait such as water
affinity or resource preference. Cause-specific births, deaths, and energy
flows should remain observable.

[Chaparro-Pedraza and Bank, PLOS Biology
2025](https://doi.org/10.1371/journal.pbio.3003492) motivate testing whether
life-history and niche feedback can sustain diversity. The
[AEGIS framework, PLOS Computational Biology
2026](https://doi.org/10.1371/journal.pcbi.1014109) demonstrates explicit
parameterization plus longitudinal demographic, phenotypic, and genomic
outputs for life-history experiments. The current Envo model implements none of
AEGIS's age structure, sexual reproduction, individual histories, or genomes.

### 5.4 Reference implemented; statistical validation and MAP-Elites next

`envo_reference.py` is an independently readable, instruction-order host mirror
that produces the same version 3 records. Golden packets cover the first three
default ticks, and CI differentially compares live floppy and ISO frames
against the reference. This is an implementation oracle, not an independently
validated ecological model. After the demographic rules stabilize, extend the
reference in parallel, compare declared distributions over many seeds, and
define acceptance tests before tuning.

Once the reference and guest agree statistically, use host-side
MAP-Elites-style search to find high-performing but behaviorally distinct
genomes. Candidate descriptors could include water use, active-period
preference, mean speed, sensing investment, and predator-avoidance style.
Export selected genomes into the boot image instead of optimizing in 16-bit
real mode.

Motivation:
[Fachada et al., Ecological Modelling
2026](https://doi.org/10.1016/j.ecolmodel.2026.111624),
[Leniabreeder, ALIFE 2024](https://doi.org/10.1162/isal_a_00827), and
[QDax, JMLR 2024](https://www.jmlr.org/papers/v25/23-1027.html).

### 5.5 Lineage and trajectory analysis

Version 3 telemetry provides event causes, aggregate energy and sensing, speed
diversity, and maximum current generation for both trophic levels, plus PRNG
state and deterministic divergence checks. It does not reconstruct ancestry.
Add stable lineage identifiers, parent links, and lineage-event records before
applying phylogenetic metrics.
Moreno, Rodriguez-Papa, and Dolson
[reported in 2025](https://doi.org/10.1162/artl_a_00470) that ecology, spatial
structure, and selection pressure can produce complex phylogenetic signatures,
while low-resolution reconstruction can bias some metrics.

Opt-in sampled trajectories could then evaluate behavioral heterogeneity
separately from aggregate performance. A later experiment may test
multi-timescale path-divergence metrics, but the
[June 2026 result](https://arxiv.org/abs/2606.17091) is a preprint and remains
an experimental direction rather than established guidance. System-level
diversity is further motivated by
[System Neural Diversity, JMLR
2025](https://www.jmlr.org/papers/v26/24-1477.html).

### 5.6 Habitat disturbance and resilience

Replace cosmetic environmental variation with reproducible resource seasons,
drought, refuges, and spatial habitat-quality changes. Compare extinction,
recovery time, and trait-distribution changes across matched seeds.

Motivation:
[Derets and Nehaniv, Artificial Life
2025](https://doi.org/10.1162/ARTL_A_00457).

### 5.7 Platform evolution

Keep the compact BIOS edition as a retro target while adding a separate x86-64
UEFI Graphics Output Protocol build. Any media work should remain conformant to
current specifications and should not imply Secure Boot support until signing,
key management, and verification are implemented and tested.

References:
[UEFI Specification 2.11](https://uefi.org/specifications),
[ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/),
and the
[Intel architecture manuals](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html).
