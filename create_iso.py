"""Dependency-free builder for the Envo Agent OS retro image.

The Python assembler is the canonical source for the boot sector and kernel.
All checked-in binary artifacts are produced from this module.
"""

# Compact semicolon-separated assembler calls intentionally mirror instruction
# groups in the generated 16-bit program.
# ruff: noqa: E701, E702

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import struct
from typing import Iterable

from envo_config import (
    AGENT_RECORD_BYTES,
    DATA_BASE,
    DEFAULT_SEED,
    MODEL_ABI_VERSION,
    TELEMETRY_FRAME_TYPE,
    TELEMETRY_MAGIC,
    TELEMETRY_PAYLOAD_BYTES,
    TELEMETRY_RECORD_BYTES,
    TELEMETRY_VERSION,
    ModelConfig,
    RuntimeLayout,
    build_experiment_document,
    configuration_id,
    configuration_sha256,
    load_model_config,
)


VERSION = "1.2.0"
SECTOR_SIZE = 512
ISO_SECTOR_SIZE = 2048
FLOPPY_SIZE = 1_474_560
KERNEL_LOAD_ADDR = 0x1000
MAX_KERNEL_SECTORS = 17  # sectors 2..18 on the first 1.44 MB floppy track
DEFAULT_MODEL_CONFIG = ModelConfig()
NUM_PREY = DEFAULT_MODEL_CONFIG.prey_count
NUM_PRED = DEFAULT_MODEL_CONFIG.predator_count
NUM_FOOD = DEFAULT_MODEL_CONFIG.food_count
ENT_SIZE = AGENT_RECORD_BYTES


class BuildError(RuntimeError):
    """Raised when an image cannot be assembled safely."""


class AssemblyError(BuildError):
    """Raised for invalid labels, branches, or instruction operands."""


@dataclass(frozen=True)
class AssemblyImage:
    code: bytes
    symbols: dict[str, int]


@dataclass(frozen=True)
class BuildArtifacts:
    boot: bytes
    kernel: bytes
    floppy: bytes
    iso: bytes
    manifest: bytes
    experiment: bytes = b""

# --- MINI ASSEMBLER ---
class ASM:
    def __init__(self, org=0):
        self.code = bytearray()
        self.labels = {}
        self.patches = []
        self.org = org

    def current_addr(self): return self.org + len(self.code)
    def label(self, name):
        if name in self.labels:
            raise AssemblyError(f"Duplicate label: {name}")
        self.labels[name] = self.current_addr()

    def db(self, data):
        if isinstance(data, int):
            if not 0 <= data <= 0xFF:
                raise AssemblyError(f"Byte outside range: {data}")
            self.code.append(data)
        elif isinstance(data, bytes): self.code.extend(data)
        elif isinstance(data, str): self.code.extend(data.encode('ascii'))
        else:
            raise AssemblyError(f"Unsupported byte data: {type(data).__name__}")

    def dw(self, val):
        if not -0x8000 <= val <= 0xFFFF:
            raise AssemblyError(f"Word outside range: {val}")
        self.code.extend(struct.pack('<H', val & 0xFFFF))

    def _relative_patch(self, opcode, label, size):
        self.db(opcode)
        self.patches.append((len(self.code), label, size, True))
        for _ in range(size):
            self.db(0)

    def _absolute_patch(self, label):
        self.patches.append((len(self.code), label, 2, False))
        self.dw(0)

    # Instructions
    def nop(self): self.db(0x90)
    def cli(self): self.db(0xFA)
    def sti(self): self.db(0xFB)
    def cld(self): self.db(0xFC)
    def hlt(self): self.db(0xF4)
    def ret(self): self.db(0xC3)
    def pusha(self): self.db(0x60)
    def popa(self): self.db(0x61)
    def push_bx(self): self.db(0x53)
    def push_dx(self): self.db(0x52)
    def pop_bx(self): self.db(0x5B)
    def pop_dx(self): self.db(0x5A)
    def int_(self, imm): self.db(0xCD); self.db(imm)

    def jmp(self, label): self._relative_patch(0xE9, label, 2)
    def je(self, label): self._relative_patch(0x74, label, 1)
    def jne(self, label): self._relative_patch(0x75, label, 1)
    def jl(self, label): self._relative_patch(0x7C, label, 1)
    def jg(self, label): self._relative_patch(0x7F, label, 1)
    def jle(self, label): self._relative_patch(0x7E, label, 1)
    def jge(self, label): self._relative_patch(0x7D, label, 1)
    def jcxz(self, label): self._relative_patch(0xE3, label, 1)
    def jc(self, label): self._relative_patch(0x72, label, 1)
    def jnc(self, label): self._relative_patch(0x73, label, 1)

    def call(self, label): self._relative_patch(0xE8, label, 2)

    # MOV
    def mov_al(self, val): self.db(0xB0); self.db(val)
    def mov_ax(self, val): self.db(0xB8); self.dw(val)
    def mov_bx(self, val): self.db(0xBB); self.dw(val)
    def mov_cx(self, val): self.db(0xB9); self.dw(val)
    def mov_dx(self, val): self.db(0xBA); self.dw(val)
    def mov_si(self, val): self.db(0xBE); self.dw(val)
    def mov_si_label(self, label):
        self.db(0xBE)
        self._absolute_patch(label)

    def mov_di(self, val): self.db(0xBF); self.dw(val)
    def mov_sp(self, val): self.db(0xBC); self.dw(val)
    def mov_bp(self, val): self.db(0xBD); self.dw(val)

    def mov_ds_ax(self): self.db(0x8E); self.db(0xD8)
    def mov_es_ax(self): self.db(0x8E); self.db(0xC0)
    def mov_ss_ax(self): self.db(0x8E); self.db(0xD0)
    def mov_mem_dl(self, label):
        self.db(b"\x88\x16")
        self._absolute_patch(label)

    def mov_dl_mem(self, label):
        self.db(b"\x8A\x16")
        self._absolute_patch(label)

    # Arithmetic
    def xor_ax_ax(self): self.db(0x31); self.db(0xC0)
    def xor_di_di(self): self.db(0x31); self.db(0xFF)
    def and_ax(self, val): self.db(0x25); self.dw(val)
    def add_ax(self, val): self.db(0x05); self.dw(val)
    def sub_ax(self, val): self.db(0x2D); self.dw(val)
    def inc_di(self): self.db(0x47)
    def inc_si(self): self.db(0x46)
    def inc_ax(self): self.db(0x40)
    def inc_cx(self): self.db(0x41)
    def dec_cx(self): self.db(0x49)
    def dec_dx(self): self.db(0x4A)
    def dec_si(self): self.db(0x4E)

    def cmp_al(self, val): self.db(0x3C); self.db(val)
    def cmp_ax(self, val): self.db(0x3D); self.dw(val)
    def cmp_cx(self, val): self.db(0x81); self.db(0xF9); self.dw(val)
    def cmp_dx(self, val): self.db(0x81); self.db(0xFA); self.dw(val)
    def cmp_bx(self, val): self.db(0x81); self.db(0xFB); self.dw(val)
    def cmp_bp(self, val): self.db(0x81); self.db(0xFD); self.dw(val)
    def cmp_si(self, val): self.db(0x81); self.db(0xFE); self.dw(val)
    def cmp_di(self, val): self.db(0x81); self.db(0xFF); self.dw(val)

    # String
    def lodsb(self): self.db(0xAC)
    def stosb(self): self.db(0xAA)
    def stosw(self): self.db(0xAB)
    def rep_stosb(self): self.db(0xF3); self.db(0xAA)
    def rep_stosw(self): self.db(0xF3); self.db(0xAB)

    # IO
    def out_dx_al(self): self.db(0xEE)

    def resolve(self):
        code = bytearray(self.code)
        for offset, label, size, relative in self.patches:
            if label not in self.labels:
                raise AssemblyError(f"Undefined label: {label}")
            target = self.labels[label]
            if relative:
                pc = self.org + offset + size
                diff = target - pc
                lower, upper = (-128, 127) if size == 1 else (-32768, 32767)
                if not lower <= diff <= upper:
                    raise AssemblyError(
                        f"Branch to {label} is out of range ({diff} for {size * 8}-bit displacement)"
                    )
                if size == 1:
                    code[offset] = diff & 0xFF
                elif size == 2:
                    struct.pack_into('<h', code, offset, diff)
            else:
                if not 0 <= target <= 0xFFFF:
                    raise AssemblyError(f"Absolute label {label} is outside real-mode range")
                struct.pack_into('<H', code, offset, target)
        return bytes(code)

    def image(self):
        return AssemblyImage(self.resolve(), dict(self.labels))

# --- BUILDER ---

def assemble_bootloader(kernel_sectors: int) -> AssemblyImage:
    if not 1 <= kernel_sectors <= MAX_KERNEL_SECTORS:
        raise BuildError(
            f"Kernel requires {kernel_sectors} sectors; BIOS stage-1 supports "
            f"1..{MAX_KERNEL_SECTORS} sectors"
        )

    asm = ASM(org=0x7C00)
    asm.cli()
    asm.xor_ax_ax(); asm.mov_ds_ax(); asm.mov_es_ax(); asm.mov_ss_ax(); asm.mov_sp(0x7C00)
    asm.sti(); asm.cld()
    asm.mov_mem_dl('boot_drive')

    # Bochs/QEMU debug console marker: stage 1 reached.
    asm.mov_dx(0x00E9); asm.mov_al(ord('B')); asm.out_dx_al()
    asm.mov_si(3)
    asm.label('disk_retry')
    asm.mov_dl_mem('boot_drive')
    asm.xor_ax_ax(); asm.int_(0x13)
    asm.xor_ax_ax(); asm.mov_es_ax()
    asm.mov_bx(KERNEL_LOAD_ADDR)
    asm.mov_ax(0x0200 | kernel_sectors)
    asm.mov_cx(0x0002)  # cylinder 0, sector 2
    asm.db(b"\xB6\x00")  # MOV DH, 0 (head 0)
    asm.mov_dl_mem('boot_drive')
    asm.int_(0x13)
    asm.jnc('load_ok')
    asm.dec_si(); asm.jne('disk_retry')

    asm.mov_si_label('disk_error_msg'); asm.call('print_string')
    asm.label('disk_error')
    asm.cli(); asm.hlt(); asm.jmp('disk_error')

    asm.label('load_ok')
    asm.db(0xEA); asm.dw(KERNEL_LOAD_ADDR); asm.dw(0x0000)  # JMP FAR 0000:1000

    asm.label('print_string')
    asm.mov_bx(0x0007)
    asm.label('print_loop')
    asm.lodsb(); asm.cmp_al(0); asm.je('print_done')
    asm.db(b"\xB4\x0E")  # MOV AH, 0x0E
    asm.int_(0x10); asm.jmp('print_loop')
    asm.label('print_done'); asm.ret()

    asm.label('boot_drive'); asm.db(0)
    asm.label('disk_error_msg'); asm.db(b"\r\nEnvo disk read failed\r\n\x00")

    code = asm.resolve()
    if len(code) > 510:
        raise BuildError(f"Boot sector is {len(code)} bytes; maximum is 510")
    padded = code + b'\x00' * (510 - len(code)) + b'\x55\xAA'
    return AssemblyImage(padded, dict(asm.labels))


def build_bootloader(kernel_sectors: int = 1) -> bytes:
    return assemble_bootloader(kernel_sectors).code


def assemble_kernel_config(config: ModelConfig) -> AssemblyImage:
    """Assemble a kernel for one validated model experiment."""

    layout = RuntimeLayout.from_config(config)
    seed = config.seed
    NUM_PREY = config.prey_count
    NUM_PRED = config.predator_count
    NUM_FOOD = config.food_count
    ENT_SIZE = layout.agent_record_bytes
    prey_base = layout.prey_base
    predator_base = layout.predator_base

    asm = ASM(org=0x1000)

    # --- CONSTANTS ---
    COLOR_PREY = 0x2F
    COLOR_PRED = 0x28
    COLOR_FOOD = 0x2C

    VGA_SEG = 0xA000

    # Entity: X, Y, energy, speed, sense, generation (six 16-bit words).
    # Scratch words below DATA_BASE: PRNG, tick, replacements, captures,
    # target, min distance, starvations, and successful-forager turnovers.

    asm.label('start')
    asm.cli()
    asm.xor_ax_ax(); asm.mov_ds_ax(); asm.mov_es_ax(); asm.mov_ss_ax(); asm.mov_sp(0x7C00)
    asm.sti(); asm.cld()
    asm.mov_dx(0x00E9); asm.mov_al(ord('K')); asm.out_dx_al()
    asm.mov_ax(0x0013); asm.int_(0x10)  # VGA mode 13h

    # Reproducible runtime state.
    asm.mov_bx(DATA_BASE - 2); asm.mov_ax(seed); asm.db(b"\x89\x07")
    asm.xor_ax_ax()
    for offset in (4, 6, 8, 10, 12, 14, 16):
        asm.mov_bx(DATA_BASE - offset); asm.db(b"\x89\x07")

    # Reset ES to 0 for data operations
    asm.xor_ax_ax(); asm.mov_es_ax()

    asm.call('init_entities')

    # --- MAIN LOOP ---
    asm.label('main_loop')

    # 0. Time & Day/Night Cycle
    asm.mov_bx(DATA_BASE - 4)
    asm.db(0x8B); asm.db(0x07) # MOV AX, [BX]
    asm.inc_ax()
    asm.db(0x89); asm.db(0x07) # MOV [BX], AX

    # Palette Shifting (Simulate Day/Night)
    # Check if Time bit 8 is set (Cycle speed)
    asm.db(0xA9); asm.dw(config.day_night_mask)
    asm.je('is_day')
    # Night: Darken Palette (Simple hack: Use different BG color?)
    # Real palette shifting is complex. Let's just set a flag in DX.
    asm.mov_dx(1) # Night
    asm.jmp('time_done')
    asm.label('is_day')
    asm.mov_dx(0) # Day
    asm.label('time_done')

    # 1. Input
    asm.mov_ax(0x0100); asm.int_(0x16); asm.je('no_key')
    asm.mov_ax(0x0000); asm.int_(0x16)
    asm.cmp_al(114); asm.je('key_restart') # r
    asm.cmp_al(112); asm.je('key_pause') # p
    asm.jmp('no_key')
    asm.label('key_restart'); asm.jmp('start')
    asm.label('key_pause'); asm.mov_ax(0); asm.int_(0x16); asm.cmp_al(112); asm.je('no_key'); asm.jmp('key_pause')
    asm.label('no_key')

    # 2. Draw BG
    asm.mov_ax(VGA_SEG); asm.mov_es_ax()
    asm.xor_di_di(); asm.mov_cx(32000)
    # If Night (DX=1), use Dark Blue (0x10), else Blue (0x01)
    asm.cmp_dx(1); asm.je('bg_night')
    asm.mov_ax(0x0101); asm.jmp('bg_draw')
    asm.label('bg_night'); asm.mov_ax(0x1010)
    asm.label('bg_draw'); asm.db(0xF3); asm.db(0xAB)

    # Draw the configured water band.
    asm.mov_di(320 * config.waterline)
    asm.mov_cx(320 * (200 - config.waterline) // 2)
    asm.mov_ax(0x3333) # Water Color
    asm.db(0xF3); asm.db(0xAB)

    # Draw Fireflies (If Night)
    asm.cmp_dx(1); asm.jne('skip_fireflies')
    asm.mov_cx(20) # 20 Fireflies
    asm.label('firefly_loop')
    # Cosmetic rendering must not consume the model PRNG. Derive a stable
    # upper-screen position from the tick and loop index instead.
    asm.db(b"\x89\xCF\xC1\xE7\x0A")  # DI=CX; DI<<=10
    asm.mov_bx(DATA_BASE - 4); asm.db(b"\x03\x3F")  # ADD DI,[BX]
    asm.db(b"\x81\xE7"); asm.dw(0x7FFF)
    asm.mov_ax(0x2C2C); asm.stosb()  # Yellow pixel in the upper half
    asm.label('ff_next')
    asm.dec_cx(); asm.jcxz('skip_fireflies'); asm.jmp('firefly_loop')
    asm.label('skip_fireflies')

    # Draw UI (Sun/Moon)
    asm.mov_di(320*5 + 300) # Top Right
    asm.cmp_dx(1); asm.je('draw_moon')
    asm.mov_ax(0x2C2C); asm.jmp('draw_celest') # Sun (Yellow)
    asm.label('draw_moon'); asm.mov_ax(0x0F0F) # Moon (White)
    asm.label('draw_celest'); asm.db(0xAA); asm.db(0xAA); asm.db(0xAA); asm.db(0xAA) # 4 pixels

    # 3. Update Entities
    # Food
    asm.mov_si(DATA_BASE); asm.mov_cx(NUM_FOOD); asm.call('update_food')
    # Prey
    asm.mov_si(prey_base); asm.mov_cx(NUM_PREY); asm.call('update_prey')
    # Pred
    asm.mov_si(predator_base); asm.mov_cx(NUM_PRED); asm.call('update_pred')

    # Emit the versioned experiment record at the configured power-of-two
    # interval. Telemetry is observational: it never advances the PRNG.
    asm.mov_bx(DATA_BASE - 4); asm.db(b"\x8B\x07")
    asm.db(b"\xA9"); asm.dw(config.telemetry_interval - 1)
    asm.jne('telemetry_done')
    asm.call('emit_telemetry')
    asm.label('telemetry_done')

    asm.call('delay')
    asm.jmp('main_loop')

    # --- FUNCTIONS ---

    # Fixed 32-byte experiment record:
    # EV, version, type, payload length, nine words, speed histogram,
    # max generation, state checksum, and an additive checksum byte.
    asm.label('emit_telemetry')
    asm.pusha(); asm.db(b"\x06")  # PUSH ES
    asm.xor_ax_ax(); asm.mov_es_ax()
    asm.mov_di(layout.telemetry_buffer)
    for byte in (
        *TELEMETRY_MAGIC,
        TELEMETRY_VERSION,
        TELEMETRY_FRAME_TYPE,
        TELEMETRY_PAYLOAD_BYTES,
    ):
        asm.mov_al(byte); asm.stosb()

    asm.mov_ax(configuration_id(config)); asm.stosw()
    for address in (
        DATA_BASE - 4,   # tick
        DATA_BASE - 2,   # PRNG state
        DATA_BASE - 6,   # replacements
        DATA_BASE - 8,   # captures
        DATA_BASE - 14,  # starvations
        DATA_BASE - 16,  # successful-forager turnovers
    ):
        asm.mov_bx(address); asm.db(b"\x8B\x07"); asm.stosw()

    # Reserve energy sum, sense sum, four speed bins, max generation, and
    # the state checksum. All are filled below.
    asm.xor_ax_ax(); asm.mov_cx(6); asm.rep_stosw()

    asm.mov_si(prey_base); asm.mov_cx(NUM_PREY)
    asm.label('telemetry_metric_loop')
    asm.db(b"\x8B\x44\x04")  # MOV AX, [SI+4]
    asm.db(b"\x01\x06"); asm.dw(layout.telemetry_buffer + 19)
    asm.db(b"\x8B\x44\x08")  # MOV AX, [SI+8]
    asm.db(b"\x01\x06"); asm.dw(layout.telemetry_buffer + 21)
    asm.db(b"\x8B\x5C\x06\x4B")  # MOV BX, [SI+6]; DEC BX
    asm.db(b"\xFE\x87"); asm.dw(layout.telemetry_buffer + 23)
    asm.db(b"\x8B\x44\x0A")
    asm.db(b"\x3B\x06"); asm.dw(layout.telemetry_buffer + 27)
    asm.jle('telemetry_generation_done')
    asm.db(b"\xA3"); asm.dw(layout.telemetry_buffer + 27)
    asm.label('telemetry_generation_done')
    asm.db(b"\x83\xC6"); asm.db(ENT_SIZE)
    asm.dec_cx(); asm.jcxz('telemetry_metrics_done'); asm.jmp('telemetry_metric_loop')
    asm.label('telemetry_metrics_done')

    # Canonical guest-state checksum: a wrapping sum of every 16-bit word in
    # the food, prey, and predator regions after the completed model update.
    asm.mov_si(DATA_BASE)
    asm.mov_cx((layout.data_end - DATA_BASE) // 2)
    asm.mov_bx(0)
    asm.label('telemetry_state_sum')
    asm.db(b"\x03\x1C\x83\xC6\x02")  # ADD BX,[SI]; ADD SI,2
    asm.dec_cx(); asm.jcxz('telemetry_state_done'); asm.jmp('telemetry_state_sum')
    asm.label('telemetry_state_done')
    asm.db(b"\x89\x1E"); asm.dw(layout.telemetry_buffer + 29)

    # The final byte negates the wrapping sum from version through payload.
    asm.mov_si(layout.telemetry_buffer + len(TELEMETRY_MAGIC))
    asm.mov_cx(TELEMETRY_RECORD_BYTES - len(TELEMETRY_MAGIC) - 1)
    asm.mov_bx(0)
    asm.label('telemetry_checksum_loop')
    asm.lodsb(); asm.db(b"\x00\xC3")
    asm.dec_cx(); asm.jcxz('telemetry_checksum_done'); asm.jmp('telemetry_checksum_loop')
    asm.label('telemetry_checksum_done')
    asm.db(b"\xF6\xDB\x88\xD8\xA2")
    asm.dw(layout.telemetry_buffer + TELEMETRY_RECORD_BYTES - 1)

    asm.mov_dx(0x00E9); asm.mov_si(layout.telemetry_buffer)
    asm.mov_cx(TELEMETRY_RECORD_BYTES)
    asm.label('telemetry_send_loop')
    asm.lodsb(); asm.out_dx_al()
    asm.dec_cx(); asm.jcxz('telemetry_send_done'); asm.jmp('telemetry_send_loop')
    asm.label('telemetry_send_done')
    asm.db(b"\x07"); asm.popa(); asm.ret()  # POP ES

    # Reproducible xorshift16 PRNG. It is small enough for real mode and avoids
    # the lattice produced by the previous state += 14 sequence.
    asm.label('rand')
    asm.push_bx(); asm.push_dx()
    asm.mov_bx(DATA_BASE - 2); asm.db(b"\x8B\x07")  # MOV AX, [BX]
    asm.db(b"\x89\xC2\xC1\xE0\x07\x31\xC2")  # DX=AX; AX<<=7; DX^=AX
    asm.db(b"\x89\xD0\x89\xC2\xC1\xE8\x09\x31\xC2")  # AX=DX; DX=AX; AX>>=9; DX^=AX
    asm.db(b"\x89\xD0\x89\xC2\xC1\xE0\x08\x31\xD0")  # AX=DX; DX=AX; AX<<=8; AX^=DX
    asm.db(b"\x89\x07")
    asm.pop_dx(); asm.pop_bx(); asm.ret()

    asm.label('rand_x')
    asm.call('rand'); asm.and_ax(0x00FF); asm.add_ax(32); asm.ret()  # 32..287
    asm.label('rand_y')
    asm.call('rand'); asm.and_ax(0x007F); asm.add_ax(24); asm.ret()  # 24..151

    # Init
    asm.label('init_entities')
    asm.mov_di(DATA_BASE)

    # Food (Simple X,Y)
    asm.mov_cx(NUM_FOOD)
    asm.label('init_food')
    asm.call('rand_x'); asm.stosw()
    asm.call('rand_y'); asm.stosw()
    asm.mov_ax(0); asm.stosw()  # Padding
    asm.dec_cx(); asm.jcxz('init_prey'); asm.jmp('init_food')

    # Prey (Advanced Struct)
    asm.label('init_prey')
    asm.mov_cx(NUM_PREY)
    asm.label('init_prey_loop')
    asm.call('rand_x'); asm.stosw()
    asm.call('rand_y'); asm.stosw()
    asm.mov_ax(config.initial_energy); asm.stosw()
    asm.mov_ax(config.prey_initial_speed); asm.stosw()
    asm.mov_ax(config.prey_initial_sense); asm.stosw()
    asm.mov_ax(0); asm.stosw()  # Generation
    asm.dec_cx(); asm.jcxz('init_pred'); asm.jmp('init_prey_loop')

    # Pred
    asm.label('init_pred')
    asm.mov_cx(NUM_PRED)
    asm.label('init_pred_loop')
    asm.call('rand_x'); asm.stosw()
    asm.call('rand_y'); asm.stosw()
    asm.mov_ax(config.initial_energy); asm.stosw()
    asm.mov_ax(config.predator_initial_speed); asm.stosw()
    asm.mov_ax(config.predator_initial_sense); asm.stosw()
    asm.mov_ax(0); asm.stosw()
    asm.dec_cx(); asm.jcxz('init_done'); asm.jmp('init_pred_loop')
    asm.label('init_done'); asm.ret()

    # Draw Pixel
    asm.label('draw_pixel')
    asm.pusha()
    asm.db(0x06) # PUSH ES
    asm.cmp_cx(0); asm.jl('draw_skip'); asm.cmp_dx(0); asm.jl('draw_skip')
    asm.cmp_cx(318); asm.jg('draw_skip'); asm.cmp_dx(198); asm.jg('draw_skip')
    asm.mov_ax(0xA000); asm.mov_es_ax()
    asm.db(0x89); asm.db(0xD0); asm.db(0xC1); asm.db(0xE0); asm.db(0x08)
    asm.mov_di(0); asm.db(0x89); asm.db(0xD7); asm.db(0xC1); asm.db(0xE7); asm.db(0x06)
    asm.db(0x01); asm.db(0xF8); asm.db(0x01); asm.db(0xC8)
    asm.mov_di(0); asm.db(0x89); asm.db(0xC7)
    asm.mov_ax(0); asm.db(0x88); asm.db(0xD8)
    asm.db(0xAA); asm.db(0xAA); asm.db(0x81); asm.db(0xC7); asm.dw(318); asm.db(0xAA); asm.db(0xAA)
    asm.label('draw_skip'); asm.db(0x07); asm.popa(); asm.ret() # POP ES

    # Keep the 2x2 sprites inside the 320x200 framebuffer.
    asm.label('clamp_entity')
    asm.db(b"\x8B\x04"); asm.cmp_ax(2); asm.jl('clamp_x_min')
    asm.cmp_ax(316); asm.jle('clamp_y')
    asm.mov_ax(316); asm.db(b"\x89\x04"); asm.jmp('clamp_y')
    asm.label('clamp_x_min')
    asm.mov_ax(2); asm.db(b"\x89\x04")
    asm.label('clamp_y')
    asm.db(b"\x8B\x44\x02"); asm.cmp_ax(2); asm.jl('clamp_y_min')
    asm.cmp_ax(196); asm.jle('clamp_done')
    asm.mov_ax(196); asm.db(b"\x89\x44\x02"); asm.jmp('clamp_done')
    asm.label('clamp_y_min')
    asm.mov_ax(2); asm.db(b"\x89\x44\x02")
    asm.label('clamp_done'); asm.ret()

    # Fixed-population asexual replacement. Vacated slots use an unbiased
    # random living donor; successful foragers preserve their own lineage.
    asm.label('rebirth_prey')
    asm.pusha()
    asm.db(b"\x89\xFD")  # MOV BP, DI (destination slot)
    asm.call('rand_x'); asm.db(b"\x89\x46\x00")
    asm.call('rand_y'); asm.db(b"\x89\x46\x02")
    asm.mov_ax(config.initial_energy); asm.db(b"\x89\x46\x04")

    asm.label('random_parent_retry')
    parent_mask = (1 << (NUM_PREY - 1).bit_length()) - 1
    asm.call('rand'); asm.and_ax(parent_mask); asm.cmp_ax(NUM_PREY)
    asm.jge('random_parent_retry')
    asm.label('parent_index_ready')
    asm.db(b"\x89\xC3\xC1\xE0\x03\xC1\xE3\x02\x01\xD8")  # AX=index*12
    asm.mov_si(prey_base); asm.db(b"\x01\xC6")
    asm.jmp('inherit_genes')

    asm.label('rebirth_self')
    asm.pusha()
    asm.db(b"\x89\xFD")
    asm.call('rand_x'); asm.db(b"\x89\x46\x00")
    asm.call('rand_y'); asm.db(b"\x89\x46\x02")
    asm.mov_ax(config.initial_energy); asm.db(b"\x89\x46\x04")
    asm.db(b"\x89\xEE")  # MOV SI, BP

    asm.label('inherit_genes')

    # Speed: inherit, then occasionally mutate within configured bounds.
    asm.db(b"\x8B\x5C\x06"); asm.call('rand'); asm.db(b"\xA8"); asm.db(config.mutation_mask)
    asm.jne('speed_store'); asm.call('rand'); asm.db(b"\xA8\x01")
    asm.je('speed_down')
    asm.db(0x43); asm.cmp_bx(config.speed_max); asm.jle('speed_store'); asm.mov_bx(config.speed_max); asm.jmp('speed_store')
    asm.label('speed_down')
    asm.db(0x4B); asm.cmp_bx(config.speed_min); asm.jge('speed_store'); asm.mov_bx(config.speed_min)
    asm.label('speed_store'); asm.db(b"\x89\x5E\x06")

    # Sense: inherit, then occasionally mutate by the configured step.
    asm.db(b"\x8B\x5C\x08"); asm.call('rand'); asm.db(b"\xA8"); asm.db(config.mutation_mask)
    asm.jne('sense_store'); asm.call('rand'); asm.db(b"\xA8\x01")
    asm.je('sense_down')
    asm.db(b"\x83\xC3"); asm.db(config.sense_mutation_step); asm.cmp_bx(config.sense_max); asm.jle('sense_store')
    asm.mov_bx(config.sense_max); asm.jmp('sense_store')
    asm.label('sense_down')
    asm.db(b"\x83\xEB"); asm.db(config.sense_mutation_step); asm.cmp_bx(config.sense_min); asm.jge('sense_store'); asm.mov_bx(config.sense_min)
    asm.label('sense_store'); asm.db(b"\x89\x5E\x08")

    asm.db(b"\x8B\x44\x0A"); asm.inc_ax(); asm.db(b"\x89\x46\x0A")
    asm.mov_bx(DATA_BASE - 6); asm.db(b"\xFF\x07")  # births++
    asm.popa(); asm.ret()

    # Update Food
    asm.label('update_food')
    asm.label('food_loop')
    asm.pusha()
    asm.db(0xAD); asm.mov_bx(0); asm.db(0x89); asm.db(0xC3)
    asm.db(0xAD); asm.mov_dx(0); asm.db(0x89); asm.db(0xC2)
    asm.mov_cx(0); asm.db(0x89); asm.db(0xD9); asm.mov_bx(COLOR_FOOD); asm.call('draw_pixel')
    asm.popa()
    asm.db(0x83); asm.db(0xC6); asm.db(0x06) # Food is 6 bytes
    asm.dec_cx(); asm.jcxz('food_done'); asm.jmp('food_loop')
    asm.label('food_done'); asm.ret()

    # Update prey: circadian energy, predator sensing, bounded movement,
    # resource consumption, and fixed-population evolutionary replacement.
    asm.label('update_prey')
    asm.label('prey_loop')
    asm.pusha()

    # Recompute day/night because drawing calls use DX.
    asm.pusha()
    asm.mov_bx(DATA_BASE - 4); asm.db(0x8B); asm.db(0x07); asm.db(0xA9); asm.dw(config.day_night_mask)
    asm.popa()
    asm.je('prey_awake')
    # Night: sleep and recover, capped at the reproduction threshold.
    asm.db(b"\x81\x7C\x04"); asm.dw(config.reproduction_energy); asm.jl('prey_night_heal')
    asm.jmp('prey_draw')
    asm.label('prey_night_heal')
    asm.db(b"\x83\x44\x04\x01")
    asm.jmp('prey_draw')

    asm.label('prey_awake')
    asm.db(b"\xFF\x4C\x04")  # metabolism: DEC word [SI+4]
    asm.jne('prey_has_energy')
    asm.mov_bx(DATA_BASE - 14); asm.db(b"\xFF\x07")  # starvations++
    asm.db(b"\x89\xF7"); asm.call('rebirth_prey'); asm.jmp('prey_draw')

    asm.label('prey_has_energy')
    # Flee when any predator is inside the inherited sense radius.
    asm.mov_di(predator_base)
    asm.mov_dx(NUM_PRED)
    asm.label('flee_scan')
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.jge('flee_x_pos')
    asm.db(b"\xF7\xD8")
    asm.label('flee_x_pos'); asm.db(b"\x89\xC3")
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.jge('flee_y_pos')
    asm.db(b"\xF7\xD8")
    asm.label('flee_y_pos'); asm.db(b"\x01\xC3")
    asm.db(b"\x3B\x5C\x08"); asm.jle('flee_now')

    asm.db(b"\x83\xC7\x0C")
    asm.dec_dx(); asm.cmp_dx(0); asm.je('flee_none'); asm.jmp('flee_scan')

    asm.label('flee_now')
    asm.db(b"\x8B\x5C\x06")
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.jge('flee_right')
    asm.db(b"\x29\x1C"); asm.jmp('flee_vertical')
    asm.label('flee_right'); asm.db(b"\x01\x1C")
    asm.label('flee_vertical')
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.jge('flee_down')
    asm.db(b"\x29\x5C\x02"); asm.jmp('prey_move_done')
    asm.label('flee_down'); asm.db(b"\x01\x5C\x02")
    asm.jmp('prey_move_done')

    asm.label('flee_none')
    # Symmetric random walk whose magnitude is the speed gene.
    asm.db(b"\x8B\x5C\x06")
    asm.call('rand'); asm.db(b"\xA8\x01"); asm.je('wander_x_minus')
    asm.db(b"\x01\x1C"); asm.jmp('wander_y')
    asm.label('wander_x_minus'); asm.db(b"\x29\x1C")
    asm.label('wander_y')
    asm.call('rand'); asm.db(b"\xA8\x01"); asm.je('wander_y_minus')
    asm.db(b"\x01\x5C\x02"); asm.jmp('prey_move_done')
    asm.label('wander_y_minus'); asm.db(b"\x29\x5C\x02")

    asm.label('prey_move_done')
    asm.call('clamp_entity')

    # Water creates a persistent upward drag below the configured waterline.
    asm.db(b"\x81\x7C\x02"); asm.dw(config.waterline)
    asm.jl('not_water')
    asm.db(b"\xFF\x4C\x02")
    asm.label('not_water')

    # Eat Food
    asm.mov_di(DATA_BASE); asm.mov_dx(NUM_FOOD)
    asm.label('eat_loop')
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.jge('eat_x_pos')
    asm.db(b"\xF7\xD8")
    asm.label('eat_x_pos'); asm.cmp_ax(4); asm.jg('no_eat')
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.jge('eat_y_pos')
    asm.db(b"\xF7\xD8")
    asm.label('eat_y_pos'); asm.cmp_ax(4); asm.jg('no_eat')

    asm.call('rand_x'); asm.db(b"\x89\x05")
    asm.call('rand_y'); asm.db(b"\x89\x45\x02")
    asm.db(b"\x81\x44\x04"); asm.dw(config.food_energy)
    asm.db(b"\x81\x7C\x04"); asm.dw(config.reproduction_energy); asm.jl('prey_ate')
    asm.mov_bx(DATA_BASE - 16); asm.db(b"\xFF\x07")  # forager turnovers++
    asm.db(b"\x89\xF7"); asm.call('rebirth_self')
    asm.label('prey_ate')
    asm.mov_bx(0x0F); asm.jmp('prey_draw_c')
    asm.label('no_eat')
    asm.db(b"\x83\xC7\x06"); asm.dec_dx(); asm.cmp_dx(0); asm.je('eat_done'); asm.jmp('eat_loop')
    asm.label('eat_done')

    asm.label('prey_draw')
    asm.mov_bx(COLOR_PREY)
    asm.label('prey_draw_c')
    asm.db(b"\x8B\x0C\x8B\x54\x02"); asm.call('draw_pixel')

    asm.popa()
    asm.db(b"\x83\xC6\x0C")
    asm.dec_cx(); asm.jcxz('prey_done'); asm.jmp('prey_loop')
    asm.label('prey_done'); asm.ret()

    # Update predators: nearest-target pursuit, gene-scaled movement, predation.
    asm.label('update_pred')
    asm.label('pred_loop')
    asm.pusha()

    asm.mov_di(prey_base)
    asm.mov_dx(NUM_PREY)
    asm.mov_bx(DATA_BASE - 12); asm.mov_ax(0x7FFF); asm.db(b"\x89\x07")
    asm.mov_bx(DATA_BASE - 10); asm.xor_ax_ax(); asm.db(b"\x89\x07")

    asm.label('chase_scan')
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.jge('chase_x_pos')
    asm.db(b"\xF7\xD8")
    asm.label('chase_x_pos'); asm.db(b"\x89\xC3")
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.jge('chase_y_pos')
    asm.db(b"\xF7\xD8")
    asm.label('chase_y_pos'); asm.db(b"\x01\xD8")
    asm.mov_bx(DATA_BASE - 12); asm.db(b"\x3B\x07")
    asm.jge('not_closer')
    asm.db(b"\x89\x07")
    asm.mov_bx(DATA_BASE - 10); asm.db(b"\x89\x3F")
    asm.label('not_closer')
    asm.db(b"\x83\xC7\x0C"); asm.dec_dx(); asm.cmp_dx(0)
    asm.je('chase_done'); asm.jmp('chase_scan')

    asm.label('chase_done')
    asm.mov_bx(DATA_BASE - 12); asm.db(b"\x8B\x07\x3B\x44\x08")
    asm.jg('chase_none')

    asm.mov_bx(DATA_BASE - 10); asm.db(b"\x8B\x3F")
    asm.db(b"\x8B\x5C\x06")
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.je('chase_vertical')
    asm.jg('chase_left')
    asm.db(b"\x01\x1C"); asm.jmp('chase_vertical')
    asm.label('chase_left'); asm.db(b"\x29\x1C")
    asm.label('chase_vertical')
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.je('pred_move_done')
    asm.jg('chase_up')
    asm.db(b"\x01\x5C\x02"); asm.jmp('pred_move_done')
    asm.label('chase_up'); asm.db(b"\x29\x5C\x02")
    asm.jmp('pred_move_done')

    asm.label('chase_none')
    asm.db(b"\x8B\x5C\x06")
    asm.call('rand'); asm.db(b"\xA8\x01"); asm.je('pred_x_minus')
    asm.db(b"\x01\x1C"); asm.jmp('pred_wander_y')
    asm.label('pred_x_minus'); asm.db(b"\x29\x1C")
    asm.label('pred_wander_y')
    asm.call('rand'); asm.db(b"\xA8\x01"); asm.je('pred_y_minus')
    asm.db(b"\x01\x5C\x02"); asm.jmp('pred_move_done')
    asm.label('pred_y_minus'); asm.db(b"\x29\x5C\x02")

    asm.label('pred_move_done')
    asm.call('clamp_entity')

    # Eat Prey
    asm.mov_di(prey_base); asm.mov_dx(NUM_PREY)
    asm.label('kill_loop')
    asm.db(b"\x8B\x04\x2B\x05"); asm.cmp_ax(0); asm.jge('kill_x_pos')
    asm.db(b"\xF7\xD8")
    asm.label('kill_x_pos'); asm.cmp_ax(4); asm.jg('no_kill')
    asm.db(b"\x8B\x44\x02\x2B\x45\x02"); asm.cmp_ax(0); asm.jge('kill_y_pos')
    asm.db(b"\xF7\xD8")
    asm.label('kill_y_pos'); asm.cmp_ax(4); asm.jg('no_kill')

    asm.call('rebirth_prey')
    asm.mov_bx(DATA_BASE - 8); asm.db(b"\xFF\x07")  # kills++
    asm.mov_bx(0x0F); asm.jmp('pred_draw_c')
    asm.label('no_kill')
    asm.db(b"\x83\xC7\x0C"); asm.dec_dx(); asm.cmp_dx(0); asm.je('kill_done'); asm.jmp('kill_loop')
    asm.label('kill_done')

    asm.mov_bx(COLOR_PRED)
    asm.label('pred_draw_c')
    asm.db(b"\x8B\x0C\x8B\x54\x02"); asm.call('draw_pixel')

    asm.popa()
    asm.db(b"\x83\xC6\x0C")
    asm.dec_cx(); asm.jcxz('pred_done'); asm.jmp('pred_loop')
    asm.label('pred_done'); asm.ret()

    # Delay
    asm.label('delay')
    asm.mov_cx(5000); asm.label('d1'); asm.pusha(); asm.mov_cx(100); asm.label('d2'); asm.nop(); asm.dec_cx(); asm.jcxz('d2_d'); asm.jmp('d2'); asm.label('d2_d'); asm.popa(); asm.dec_cx(); asm.jcxz('d1_d'); asm.jmp('d1'); asm.label('d1_d'); asm.ret()

    return asm.image()


def assemble_kernel(seed: int = DEFAULT_SEED) -> AssemblyImage:
    """Compatibility wrapper for the default model with a selected seed."""

    try:
        config = ModelConfig(seed=seed)
    except ValueError as exc:
        raise BuildError(str(exc)) from exc
    return assemble_kernel_config(config)


def build_kernel_config(config: ModelConfig) -> bytes:
    return assemble_kernel_config(config).code


def build_kernel(seed: int = DEFAULT_SEED) -> bytes:
    return assemble_kernel(seed).code


def make_floppy_image(boot_bin: bytes, kernel_bin: bytes) -> bytes:
    if len(boot_bin) != SECTOR_SIZE or boot_bin[-2:] != b"\x55\xAA":
        raise BuildError("Boot image must be one 512-byte sector ending in 0x55AA")
    kernel_sectors = (len(kernel_bin) + SECTOR_SIZE - 1) // SECTOR_SIZE
    if not 1 <= kernel_sectors <= MAX_KERNEL_SECTORS:
        raise BuildError(
            f"Kernel requires {kernel_sectors} sectors; maximum is {MAX_KERNEL_SECTORS}"
        )
    if SECTOR_SIZE + len(kernel_bin) > FLOPPY_SIZE:
        raise BuildError("Kernel does not fit in the floppy image")

    image = bytearray(FLOPPY_SIZE)
    image[:SECTOR_SIZE] = boot_bin
    image[SECTOR_SIZE:SECTOR_SIZE + len(kernel_bin)] = kernel_bin
    return bytes(image)


def create_floppy_img(boot_bin: bytes, kernel_bin: bytes, floppy_path) -> bool:
    Path(floppy_path).write_bytes(make_floppy_image(boot_bin, kernel_bin))
    return True


def _both_endian16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _both_endian32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _directory_record(
    identifier: bytes,
    extent_lba: int,
    data_length: int,
    *,
    directory: bool = False,
) -> bytes:
    if not 1 <= len(identifier) <= 255:
        raise BuildError("ISO9660 identifiers must contain 1..255 bytes")
    padding = b"\x00" if len(identifier) % 2 == 0 else b""
    record = bytearray(33 + len(identifier) + len(padding))
    record[0] = len(record)
    record[2:10] = _both_endian32(extent_lba)
    record[10:18] = _both_endian32(data_length)
    record[18:25] = bytes((126, 1, 1, 0, 0, 0, 0))  # 2026-01-01 UTC
    record[25] = 0x02 if directory else 0
    record[28:32] = _both_endian16(1)
    record[32] = len(identifier)
    record[33:33 + len(identifier)] = identifier
    return bytes(record)


def _path_table(root_lba: int, byte_order: str) -> bytes:
    if byte_order == "little":
        prefix = struct.pack("<BBIH", 1, 0, root_lba, 1)
    elif byte_order == "big":
        prefix = struct.pack(">BBIH", 1, 0, root_lba, 1)
    else:
        raise BuildError(f"Unsupported path-table byte order: {byte_order}")
    return prefix + b"\x00\x00"


def _iso_readme() -> bytes:
    return (
        "ENVO AGENT OS - RETRO ARTIFICIAL LIFE EDITION\r\n"
        f"Version {VERSION}\r\n\r\n"
        "Boot with an x86 BIOS emulator. Controls: P pauses; R restarts.\r\n"
        "Green prey forage and inherit speed/sense traits; red predators hunt.\r\n"
        "Builds are deterministic for a fixed experiment configuration.\r\n"
        "EXPERIMENT.JSON records the machine-readable model identity.\r\n"
        "Project: https://github.com/kai9987kai/Envo-agent-os\r\n"
    ).encode("ascii")


def make_iso_image(
    floppy_data: bytes,
    experiment_data: bytes | None = None,
) -> bytes:
    """Create a mountable ISO9660 + El Torito floppy-emulation image."""
    if len(floppy_data) != FLOPPY_SIZE:
        raise BuildError(f"El Torito floppy must be {FLOPPY_SIZE} bytes")
    if floppy_data[510:512] != b"\x55\xAA":
        raise BuildError("El Torito boot image is missing its boot signature")

    pvd_lba = 16
    boot_record_lba = 17
    terminator_lba = 18
    path_l_lba = 19
    path_m_lba = 20
    root_lba = 21
    boot_catalog_lba = 22
    boot_image_lba = 23
    floppy_blocks = (len(floppy_data) + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE
    readme_data = _iso_readme()
    readme_lba = boot_image_lba + floppy_blocks
    readme_blocks = (len(readme_data) + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE
    experiment_lba = readme_lba + readme_blocks
    experiment_blocks = (
        (len(experiment_data) + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE
        if experiment_data is not None
        else 0
    )
    volume_space_size = experiment_lba + experiment_blocks

    sectors = [bytearray(ISO_SECTOR_SIZE) for _ in range(volume_space_size)]
    root_record = _directory_record(
        b"\x00", root_lba, ISO_SECTOR_SIZE, directory=True
    )
    path_l = _path_table(root_lba, "little")
    path_m = _path_table(root_lba, "big")

    pvd = sectors[pvd_lba]
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[8:40] = b"ENVO AGENT OS".ljust(32, b" ")
    pvd[40:72] = b"ENVO_AGENT_OS".ljust(32, b" ")
    pvd[80:88] = _both_endian32(volume_space_size)
    pvd[120:124] = _both_endian16(1)
    pvd[124:128] = _both_endian16(1)
    pvd[128:132] = _both_endian16(ISO_SECTOR_SIZE)
    pvd[132:140] = _both_endian32(len(path_l))
    pvd[140:144] = struct.pack("<I", path_l_lba)
    pvd[148:152] = struct.pack(">I", path_m_lba)
    pvd[156:190] = root_record
    pvd[574:702] = b"ENVO AGENT OS PYTHON BUILDER".ljust(128, b" ")
    pvd[702:739] = b"CREATE_ISO.PY".ljust(37, b" ")
    pvd[739:776] = b"ENVO AGENT OS".ljust(37, b" ")
    iso_timestamp = b"2026010100000000\x00"
    pvd[813:830] = iso_timestamp
    pvd[830:847] = iso_timestamp
    pvd[881] = 1

    boot_record = sectors[boot_record_lba]
    boot_record[0] = 0
    boot_record[1:6] = b"CD001"
    boot_record[6] = 1
    boot_record[7:39] = b"EL TORITO SPECIFICATION".ljust(32, b"\x00")
    boot_record[71:75] = struct.pack("<I", boot_catalog_lba)

    terminator = sectors[terminator_lba]
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1

    sectors[path_l_lba][:len(path_l)] = path_l
    sectors[path_m_lba][:len(path_m)] = path_m

    root_entries = [
        root_record,
        _directory_record(b"\x01", root_lba, ISO_SECTOR_SIZE, directory=True),
        _directory_record(b"BOOT.CAT;1", boot_catalog_lba, ISO_SECTOR_SIZE),
        _directory_record(b"BOOT.IMG;1", boot_image_lba, len(floppy_data)),
        _directory_record(b"README.TXT;1", readme_lba, len(readme_data)),
    ]
    if experiment_data is not None:
        root_entries.append(
            _directory_record(
                b"EXPERIMENT.JSON;1",
                experiment_lba,
                len(experiment_data),
            )
        )
    root_data = b"".join(root_entries)
    if len(root_data) > ISO_SECTOR_SIZE:
        raise BuildError("ISO root directory does not fit in one sector")
    sectors[root_lba][:len(root_data)] = root_data

    catalog = sectors[boot_catalog_lba]
    catalog[0] = 1
    catalog[1] = 0  # x86 platform
    catalog[4:28] = b"ENVO AGENT OS".ljust(24, b"\x00")
    catalog[30:32] = b"\x55\xAA"
    checksum = -sum(struct.unpack("<16H", bytes(catalog[:32]))) & 0xFFFF
    struct.pack_into("<H", catalog, 28, checksum)
    catalog[32] = 0x88  # bootable
    catalog[33] = 0x02  # 1.44 MB floppy emulation
    struct.pack_into("<H", catalog, 38, 1)
    struct.pack_into("<I", catalog, 40, boot_image_lba)

    for block_index in range(floppy_blocks):
        start = block_index * ISO_SECTOR_SIZE
        chunk = floppy_data[start:start + ISO_SECTOR_SIZE]
        sectors[boot_image_lba + block_index][:len(chunk)] = chunk
    for block_index in range(readme_blocks):
        start = block_index * ISO_SECTOR_SIZE
        chunk = readme_data[start:start + ISO_SECTOR_SIZE]
        sectors[readme_lba + block_index][:len(chunk)] = chunk
    if experiment_data is not None:
        for block_index in range(experiment_blocks):
            start = block_index * ISO_SECTOR_SIZE
            chunk = experiment_data[start:start + ISO_SECTOR_SIZE]
            sectors[experiment_lba + block_index][:len(chunk)] = chunk

    image = b"".join(bytes(sector) for sector in sectors)
    if len(image) != volume_space_size * ISO_SECTOR_SIZE:
        raise BuildError("ISO size invariant failed")
    return image

def create_iso(floppy_img_path, iso_path) -> bool:
    Path(iso_path).write_bytes(make_iso_image(Path(floppy_img_path).read_bytes()))
    return True

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_artifacts_config(config: ModelConfig) -> BuildArtifacts:
    """Build every artifact for one canonical experiment configuration."""

    kernel = build_kernel_config(config)
    kernel_sectors = (len(kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE
    boot = build_bootloader(kernel_sectors)
    floppy = make_floppy_image(boot, kernel)
    experiment = build_experiment_document(config, VERSION)
    iso = make_iso_image(floppy, experiment)

    entries = {
        "boot.bin": boot,
        "experiment.json": experiment,
        "kernel.bin": kernel,
        "floppy.img": floppy,
        "os.iso": iso,
    }
    manifest_object = {
        "artifacts": {
            name: {"bytes": len(data), "sha256": _sha256(data)}
            for name, data in entries.items()
        },
        "config_id": configuration_id(config),
        "config_id_hex": f"0x{configuration_id(config):04X}",
        "config_sha256": configuration_sha256(config),
        "format_version": 2,
        "kernel_load_address": f"0x{KERNEL_LOAD_ADDR:04X}",
        "kernel_sectors": kernel_sectors,
        "model_abi_version": MODEL_ABI_VERSION,
        "project": "Envo Agent OS",
        "seed": config.seed,
        "telemetry": {
            "interval_ticks": config.telemetry_interval,
            "record_bytes": TELEMETRY_RECORD_BYTES,
            "version": TELEMETRY_VERSION,
        },
        "version": VERSION,
    }
    manifest = (
        json.dumps(manifest_object, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return BuildArtifacts(boot, kernel, floppy, iso, manifest, experiment)


def build_artifacts(seed: int = DEFAULT_SEED) -> BuildArtifacts:
    """Compatibility wrapper for the default model with a selected seed."""

    try:
        config = ModelConfig(seed=seed)
    except ValueError as exc:
        raise BuildError(str(exc)) from exc
    return build_artifacts_config(config)


def _artifact_entries(artifacts: BuildArtifacts) -> dict[str, bytes]:
    return {
        "boot.bin": artifacts.boot,
        "kernel.bin": artifacts.kernel,
        "floppy.img": artifacts.floppy,
        "os.iso": artifacts.iso,
        "build-manifest.json": artifacts.manifest,
        "experiment.json": artifacts.experiment,
    }


def write_artifacts(artifacts: BuildArtifacts, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in _artifact_entries(artifacts).items():
        destination = output_dir / name
        temporary = output_dir / f".{name}.tmp"
        temporary.write_bytes(data)
        temporary.replace(destination)


def check_artifacts(artifacts: BuildArtifacts, output_dir: Path) -> list[str]:
    mismatches = []
    for name, expected in _artifact_entries(artifacts).items():
        path = output_dir / name
        if not path.is_file():
            mismatches.append(f"{name}: missing")
        elif path.read_bytes() != expected:
            mismatches.append(f"{name}: differs from reproducible build")
    return mismatches


def _parse_seed(value: str) -> int:
    try:
        seed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "seed must be an integer (decimal or 0xHEX)"
        ) from exc
    if not 1 <= seed <= 0xFFFF:
        raise argparse.ArgumentTypeError("seed must be in the range 1..65535")
    return seed


def _parse_telemetry_interval(value: str) -> int:
    try:
        interval = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "telemetry interval must be an integer"
        ) from exc
    if not 1 <= interval <= 0x8000 or interval & (interval - 1):
        raise argparse.ArgumentTypeError(
            "telemetry interval must be a power of two in the range 1..32768"
        )
    return interval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic Envo Agent OS boot media."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="artifact directory (default: repository directory)",
    )
    parser.add_argument(
        "--seed",
        type=_parse_seed,
        default=None,
        help=(
            "override the non-zero 16-bit simulation seed "
            f"(default without --config: 0x{DEFAULT_SEED:04X})"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "JSON model configuration or a previously generated "
            "experiment.json"
        ),
    )
    parser.add_argument(
        "--telemetry-interval",
        type=_parse_telemetry_interval,
        default=None,
        help="override the experiment telemetry interval with a power of two",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify existing artifacts instead of writing them",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.config is None:
            config = ModelConfig(
                seed=args.seed or DEFAULT_SEED,
                telemetry_interval=args.telemetry_interval or 1,
            )
        else:
            config = load_model_config(args.config)
            overrides = {}
            if args.seed is not None:
                overrides["seed"] = args.seed
            if args.telemetry_interval is not None:
                overrides["telemetry_interval"] = args.telemetry_interval
            if overrides:
                config = replace(config, **overrides)
        artifacts = build_artifacts_config(config)
        output_dir = args.output_dir.resolve()
        if args.check:
            mismatches = check_artifacts(artifacts, output_dir)
            if mismatches:
                for mismatch in mismatches:
                    print(f"ERROR: {mismatch}")
                return 1
            print(f"Artifacts verified in {output_dir}")
            return 0

        write_artifacts(artifacts, output_dir)
        manifest = json.loads(artifacts.manifest)
        print(
            f"Built Envo Agent OS {VERSION}: "
            f"{len(artifacts.kernel)}-byte kernel in "
            f"{manifest['kernel_sectors']} sector(s)"
        )
        print(
            "Experiment "
            f"{manifest['config_id_hex']} "
            f"(telemetry every {config.telemetry_interval} tick(s))"
        )
        print(f"Artifacts written to {output_dir}")
        return 0
    except (BuildError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
