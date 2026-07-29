# Envo Agent OS model

This document describes the v1 simulation using a concise adaptation of the ODD
(Overview, Design concepts, Details) protocol. It separates implemented behavior
from research-informed future work. The model is an educational artificial-life
system, not a calibrated representation of a natural ecosystem.

## 1. Overview

### 1.1 Purpose

The model demonstrates how a small set of local agent rules can be assembled
into a bootable 16-bit x86 program and visualized without a host operating
system. Its engineering goals are deterministic builds, repeatable initial
conditions, and observable behavior in an emulator.

The current scientific question is intentionally modest: what population-scale
motion and encounters emerge from fixed food, prey, and predator rules under a
simple day/night cycle and a spatial water region?

### 1.2 Entities, state variables, and scales

The world is a 320 x 200 discrete pixel plane rendered in VGA Mode 13h.

| Entity | Count | State |
| --- | ---: | --- |
| Food | 50 | x, y, storage padding |
| Prey | 30 | x, y, energy, speed trait, sensing trait, generation |
| Predator | 4 | x, y, energy, speed trait, sensing trait, generation |

Coordinates and entity fields are 16-bit integers. The lower 30 rows form the
water region. A global 16-bit tick counter drives the day/night phase. Population
capacities are fixed in v1.

### 1.3 Process overview and scheduling

One simulation tick performs the following operations in order:

1. Increment time and select the day or night phase.
2. Poll BIOS keyboard input.
3. Clear and redraw the background, water, and day/night markers.
4. Draw food.
5. Update and draw each prey agent.
6. Update and draw each predator.
7. Apply a busy-loop frame delay.

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
not scripted as global animations. However, v1 does not claim open-ended
evolution, ecological equilibrium, or lifelike complexity.

### 2.3 Adaptation and evolution

Prey speed and sensing range are heritable traits in a fixed-population turnover
model. A prey that reaches the reproduction threshold retains its own lineage
when its slot advances to the next generation. Starvation or capture instead
refills the vacated slot from an unbiased random prey donor. Speed and sensing
are inherited, each with a bounded one-in-four mutation chance, and generation
is incremented. Trait frequencies can therefore change while total prey
capacity remains constant.

This is a compact asexual replacement model, not full demographic reproduction:
there are no empty slots, mating, variable population size, age structure, or
evolving predator traits.

### 2.4 Objectives and fitness

Agents have no explicit optimization objective. Prey spend energy while active,
recover during night rest, and gain energy from food. Energy boundaries trigger
lineage replacement, so resource acquisition and avoidance affect which trait
lineages persist. No scientific fitness score is reported, and the implemented
mechanism has not yet been shown to produce sustained adaptation.

### 2.5 Sensing and interaction

- Prey scan predators and flee when one falls within their sensing trait.
- Otherwise prey wander and scan food for close collisions.
- Predators scan prey, pursue the closest visible target, and otherwise wander.
- Food is relocated after consumption.
- Captured prey slots are replaced through the lineage-turnover rule.
- Water modifies movement near the bottom of the world.
- Prey rest during the night phase.

All scans are bounded exhaustive searches. Runtime cost therefore grows
quadratically with population capacity.

### 2.6 Stochasticity and repeatability

The canonical build accepts `--seed N`. A seed initializes the guest's
deterministic pseudo-random sequence, which controls placement, wandering, and
relocation. The same source revision, build options, and seed are intended to
produce byte-identical artifacts and the same guest trajectory in deterministic
emulation.

`build-manifest.json` records the seed and artifact digests. Repeatability across
different emulator versions or timing modes must be verified rather than
assumed; QEMU's
[record/replay support](https://qemu.readthedocs.io/en/master/devel/replay.html)
is the preferred basis for controlled runs.

### 2.7 Observation

VGA output is the human-facing observation surface. Compact records on x86
debug port `0xE9` provide a machine-facing surface for smoke tests and host-side
analysis. The boot and kernel stages emit ASCII `B` and `K` markers. Every frame
then emits `F`, the low byte of the birth counter, the low byte of the capture
counter, and a newline byte. The counters are binary, not printable decimal
text. The telemetry schema is experimental; observations should be associated
with a seed and build manifest outside the guest.

## 3. Details

### 3.1 Initialization

The BIOS boot sector loads the generated kernel from the floppy-emulation image
into low memory and transfers control to it. The kernel establishes real-mode
segments and stack state, enters VGA Mode 13h, initializes the configured random
seed, places entities, and begins the tick loop.

Prey begin with a shared speed and sensing range and generation zero. Predators
begin faster and with a larger sensing range. Subsequent prey replacements can
inherit and mutate speed and sensing; initial values remain model parameters,
not evolved outcomes.

### 3.2 Input data

The guest consumes no external datasets. Its model inputs are compile-time
constants, the deterministic seed, and runtime `R`/`P` keyboard events.

### 3.3 Environmental submodel

The high phase bit of the tick counter alternates day and night. The phase
changes background rendering and prey activity. Water occupies a fixed spatial
band. Resource growth, seasons, drought, refuges, and habitat-quality gradients
are not implemented.

### 3.4 Prey submodel

During the active phase, each prey agent spends one energy unit and scans
predators. A nearby predator causes movement away from that predator using the
prey speed trait. With no nearby predator, the prey performs a symmetric random
walk whose magnitude is its speed. Its coordinates are clamped to drawable
bounds. It is pushed upward in deep water, scans food, and gains energy after a
close encounter with a food item; that item is then relocated. During rest, the
prey does not move and recovers energy up to the reproduction threshold.

Energy zero, energy at or above 160, and predator capture each trigger
fixed-slot lineage replacement. The replacement starts at a new position with
energy 100, applies bounded mutation, and increments generation. A threshold
event inherits from that successful forager; starvation and capture inherit
from an unbiased random prey donor.

### 3.5 Predator submodel

Each predator scans all prey and retains the closest target. If the target is
within the predator sensing trait, the predator moves by its speed trait along
each differing axis. Otherwise it wanders. A close encounter increments the
capture counter and replaces the captured prey slot using the lineage-turnover
rule. Predator energy, inheritance, and reproduction do not yet affect the
outcome.

### 3.6 Display and timing submodel

The kernel redraws the full framebuffer every tick, then delays with a CPU-bound
loop. Tick duration therefore depends on emulator configuration and is not a
physical time unit. Quantitative experiments should compare tick counts, not
wall-clock time.

## 4. Validity and testable invariants

The implementation should continuously check these invariants:

- identical source, seed, and options produce identical artifact hashes;
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

Everything in this section is future work unless explicitly moved into the model
description above.

### 5.1 Richer eco-evolutionary population dynamics

Extend the current fixed-slot replacement mechanism with age, explicit empty
slots, variable population size, predator energy and inheritance, and an
energy-conserving birth/death accounting model. Add a niche trait such as water
affinity or resource preference, and make movement and sensing costs
trait-dependent. This would test whether life-history and niche feedback create
diversity beyond the current speed/sensing mutation loop.

Motivation:
[Chaparro-Pedraza and Bank, PLOS Biology 2025](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.3003492).
Their results motivate the mechanism; Envo Agent OS would require its own
validation and cannot assume the same outcome.

### 5.2 Host-side quality-diversity search

Create a pure Python reference model and use MAP-Elites-style search to discover
high-performing but behaviorally different genomes. Candidate descriptors could
include water use, active-period preference, mean speed, sensing investment, and
predator-avoidance style. Export a small archive of selected genomes into the
boot image rather than performing expensive optimization in 16-bit real mode.

Motivation:
[Leniabreeder, ALIFE 2024](https://doi.org/10.1162/isal_a_00827) and
[QDax, JMLR 2024](https://www.jmlr.org/papers/v25/23-1027.html).

### 5.3 Behavioral diversity and complexity metrics

Extend telemetry with births, deaths, captures, energy transfer, trait moments,
and sampled trajectories. Evaluate behavioral heterogeneity separately from
task performance. A later experiment may test multi-timescale path-divergence
metrics, but the June 2026 result is a preprint and should be treated as an
experimental direction rather than established guidance.

Motivation:
[System Neural Diversity, JMLR 2025](https://www.jmlr.org/papers/v26/24-1477.html)
and
[Multi-Scale Path Divergence, 2026 preprint](https://arxiv.org/abs/2606.17091).

### 5.4 Habitat disturbance and resilience

Replace cosmetic environmental variation with reproducible resource seasons,
drought, refuges, and spatial habitat-quality changes. Compare extinction,
recovery time, and trait-distribution changes across matched seeds.

Motivation:
[Derets and Nehaniv, Artificial Life 2025](https://doi.org/10.1162/ARTL_A_00457).

### 5.5 Platform evolution

Keep the compact BIOS edition as a retro target while adding a separate x86-64
UEFI Graphics Output Protocol build. Any media work should remain conformant to
current specifications and should not imply Secure Boot support until signing,
key management, and verification are implemented and tested.

References:
[UEFI Specification 2.11](https://uefi.org/specifications),
[ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/),
and the
[Intel architecture manuals](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html).
