"""Encode, parse, compare, and inspect Envo Agent OS telemetry frames."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
import struct
import sys

from envo_config import (
    TELEMETRY_FRAME_TYPE,
    TELEMETRY_MAGIC,
    TELEMETRY_PAYLOAD_BYTES,
    TELEMETRY_RECORD_BYTES,
    TELEMETRY_VERSION,
    configuration_id,
    load_model_config,
)


MAGIC = TELEMETRY_MAGIC
PROTOCOL_VERSION = TELEMETRY_VERSION
FRAME_TYPE = TELEMETRY_FRAME_TYPE
PAYLOAD_LENGTH = TELEMETRY_PAYLOAD_BYTES
FRAME_LENGTH = TELEMETRY_RECORD_BYTES

_HEADER = struct.Struct("<2sBBB")
_PAYLOAD = struct.Struct("<9H4B7H4BH")
_U16_FIELDS = (
    "config_id",
    "tick",
    "rng_state",
    "replacements",
    "captures",
    "starvations",
    "forager_turnovers",
    "prey_energy_sum",
    "prey_sense_sum",
    "max_generation",
    "state_checksum",
    "predator_replacements",
    "predator_starvations",
    "predator_forager_turnovers",
    "predator_energy_sum",
    "predator_sense_sum",
    "predator_max_generation",
)
_U8_FIELDS = (
    "speed_1_count",
    "speed_2_count",
    "speed_3_count",
    "speed_4_count",
    "predator_speed_1_count",
    "predator_speed_2_count",
    "predator_speed_3_count",
    "predator_speed_4_count",
)


class TelemetryError(ValueError):
    """Base class for telemetry protocol errors."""


class FrameFormatError(TelemetryError):
    """Raised when a candidate frame has an invalid protocol header."""


class FrameChecksumError(TelemetryError):
    """Raised when a frame checksum does not validate."""


class TruncatedFrameError(TelemetryError):
    """Raised when a trace ends partway through a candidate frame."""


class ConfigMismatchError(TelemetryError):
    """Raised when telemetry does not match the expected experiment config."""


@dataclass(frozen=True)
class FrameRecord:
    """Decoded payload from one version 3 frame."""

    config_id: int
    tick: int
    rng_state: int
    replacements: int
    captures: int
    starvations: int
    forager_turnovers: int
    prey_energy_sum: int
    prey_sense_sum: int
    speed_1_count: int
    speed_2_count: int
    speed_3_count: int
    speed_4_count: int
    max_generation: int
    state_checksum: int
    predator_replacements: int
    predator_starvations: int
    predator_forager_turnovers: int
    predator_energy_sum: int
    predator_sense_sum: int
    predator_speed_1_count: int
    predator_speed_2_count: int
    predator_speed_3_count: int
    predator_speed_4_count: int
    predator_max_generation: int

    def __post_init__(self) -> None:
        for name in _U16_FIELDS:
            _validate_integer(name, getattr(self, name), 0xFFFF)
        for name in _U8_FIELDS:
            _validate_integer(name, getattr(self, name), 0xFF)


@dataclass(frozen=True)
class FrameMismatch:
    """One positional difference between two telemetry traces."""

    index: int
    differing_fields: tuple[str, ...]
    left: FrameRecord | None
    right: FrameRecord | None


@dataclass(frozen=True)
class TraceComparison:
    """Result of comparing two telemetry frame sequences."""

    equal: bool
    compared_frames: int
    left_frames: int
    right_frames: int
    mismatches: tuple[FrameMismatch, ...]

    def __bool__(self) -> bool:
        return self.equal


def _validate_integer(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be in the range 0..{maximum}")


def encode_frame(frame: FrameRecord) -> bytes:
    """Encode one immutable record into an exact 48-byte protocol frame."""

    payload = _PAYLOAD.pack(
        frame.config_id,
        frame.tick,
        frame.rng_state,
        frame.replacements,
        frame.captures,
        frame.starvations,
        frame.forager_turnovers,
        frame.prey_energy_sum,
        frame.prey_sense_sum,
        frame.speed_1_count,
        frame.speed_2_count,
        frame.speed_3_count,
        frame.speed_4_count,
        frame.max_generation,
        frame.state_checksum,
        frame.predator_replacements,
        frame.predator_starvations,
        frame.predator_forager_turnovers,
        frame.predator_energy_sum,
        frame.predator_sense_sum,
        frame.predator_speed_1_count,
        frame.predator_speed_2_count,
        frame.predator_speed_3_count,
        frame.predator_speed_4_count,
        frame.predator_max_generation,
    )
    packet_without_checksum = _HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        FRAME_TYPE,
        PAYLOAD_LENGTH,
    ) + payload
    checksum = (-sum(packet_without_checksum[len(MAGIC) :])) & 0xFF
    packet = packet_without_checksum + bytes((checksum,))
    if len(packet) != FRAME_LENGTH:
        raise AssertionError("telemetry frame size invariant failed")
    return packet


def decode_frame(packet: bytes | bytearray | memoryview) -> FrameRecord:
    """Decode one complete frame and reject any protocol violation."""

    raw = bytes(packet)
    if len(raw) != FRAME_LENGTH:
        raise TruncatedFrameError(
            f"frame must be {FRAME_LENGTH} bytes, received {len(raw)}"
        )

    magic, version, frame_type, payload_length = _HEADER.unpack_from(raw)
    if magic != MAGIC:
        raise FrameFormatError(f"invalid frame magic: {magic!r}")
    _validate_header(version, frame_type, payload_length)
    if sum(raw[len(MAGIC) :]) & 0xFF:
        raise FrameChecksumError("frame checksum does not sum to zero")

    values = _PAYLOAD.unpack_from(raw, _HEADER.size)
    return FrameRecord(*values)


def _validate_header(
    version: int,
    frame_type: int,
    payload_length: int,
) -> None:
    if version != PROTOCOL_VERSION:
        raise FrameFormatError(
            f"unsupported protocol version {version}; "
            f"expected {PROTOCOL_VERSION}"
        )
    if frame_type != FRAME_TYPE:
        raise FrameFormatError(
            f"unsupported frame type {frame_type}; expected {FRAME_TYPE}"
        )
    if payload_length != PAYLOAD_LENGTH:
        raise FrameFormatError(
            f"invalid payload length {payload_length}; "
            f"expected {PAYLOAD_LENGTH}"
        )


class FrameStreamParser:
    """Incremental telemetry decoder with garbage resynchronization."""

    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict
        self._buffer = bytearray()
        self._consumed = 0
        self._finished = False

    @property
    def buffered_bytes(self) -> int:
        """Number of undecoded bytes retained for a later chunk."""

        return len(self._buffer)

    @property
    def consumed_bytes(self) -> int:
        """Absolute number of bytes consumed or discarded so far."""

        return self._consumed

    def feed(
        self,
        chunk: bytes | bytearray | memoryview,
    ) -> list[FrameRecord]:
        """Consume a trace chunk and return all newly completed frames."""

        if self._finished:
            raise RuntimeError("cannot feed a finished telemetry parser")
        self._buffer.extend(bytes(chunk))
        return self._drain(final=False)

    def finish(self) -> list[FrameRecord]:
        """Finish the stream, detecting a partial final frame in strict mode."""

        if self._finished:
            return []
        self._finished = True
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[FrameRecord]:
        records: list[FrameRecord] = []
        while self._buffer:
            magic_offset = self._buffer.find(MAGIC)
            if magic_offset < 0:
                self._discard_without_magic(final=final)
                break
            if magic_offset:
                self._discard(magic_offset)

            if len(self._buffer) < _HEADER.size:
                if final and self.strict:
                    self._raise_truncated()
                break

            _, version, frame_type, payload_length = _HEADER.unpack_from(
                self._buffer
            )
            try:
                _validate_header(version, frame_type, payload_length)
            except FrameFormatError as exc:
                if self.strict:
                    raise FrameFormatError(
                        f"{exc} at byte offset {self._consumed}"
                    ) from exc
                self._discard(1)
                continue

            if len(self._buffer) < FRAME_LENGTH:
                if final and self.strict:
                    self._raise_truncated()
                break

            candidate = bytes(self._buffer[:FRAME_LENGTH])
            if sum(candidate[len(MAGIC) :]) & 0xFF:
                if self.strict:
                    raise FrameChecksumError(
                        "frame checksum does not sum to zero "
                        f"at byte offset {self._consumed}"
                    )
                self._discard(1)
                continue

            records.append(decode_frame(candidate))
            self._discard(FRAME_LENGTH)

        return records

    def _discard_without_magic(self, *, final: bool) -> None:
        if self._buffer.endswith(MAGIC[:1]):
            self._discard(len(self._buffer) - 1)
            if final and self.strict:
                self._raise_truncated()
            return
        self._discard(len(self._buffer))

    def _discard(self, count: int) -> None:
        if count:
            del self._buffer[:count]
            self._consumed += count

    def _raise_truncated(self) -> None:
        raise TruncatedFrameError(
            "trace ended with a truncated frame at byte offset "
            f"{self._consumed}: received {len(self._buffer)} of "
            f"{FRAME_LENGTH} bytes"
        )


def parse_frames(
    data: bytes | bytearray | memoryview,
    *,
    strict: bool = False,
) -> list[FrameRecord]:
    """Parse every valid frame from a complete trace."""

    parser = FrameStreamParser(strict=strict)
    records = parser.feed(data)
    records.extend(parser.finish())
    return records


_FRAME_FIELD_NAMES = tuple(field.name for field in fields(FrameRecord))


def compare_traces(
    left: Iterable[FrameRecord],
    right: Iterable[FrameRecord],
) -> TraceComparison:
    """Compare two traces positionally and describe every mismatch."""

    left_records = tuple(left)
    right_records = tuple(right)
    mismatches: list[FrameMismatch] = []
    shared_length = min(len(left_records), len(right_records))

    for index in range(shared_length):
        left_frame = left_records[index]
        right_frame = right_records[index]
        differing_fields = tuple(
            name
            for name in _FRAME_FIELD_NAMES
            if getattr(left_frame, name) != getattr(right_frame, name)
        )
        if differing_fields:
            mismatches.append(
                FrameMismatch(
                    index,
                    differing_fields,
                    left_frame,
                    right_frame,
                )
            )

    for index in range(shared_length, max(len(left_records), len(right_records))):
        left_frame = left_records[index] if index < len(left_records) else None
        right_frame = (
            right_records[index] if index < len(right_records) else None
        )
        mismatches.append(
            FrameMismatch(
                index,
                ("<missing>",),
                left_frame,
                right_frame,
            )
        )

    return TraceComparison(
        equal=not mismatches,
        compared_frames=shared_length,
        left_frames=len(left_records),
        right_frames=len(right_records),
        mismatches=tuple(mismatches),
    )


def compare_trace_bytes(
    left: bytes | bytearray | memoryview,
    right: bytes | bytearray | memoryview,
    *,
    strict: bool = True,
) -> TraceComparison:
    """Parse and compare two encoded telemetry streams."""

    return compare_traces(
        parse_frames(left, strict=strict),
        parse_frames(right, strict=strict),
    )


def assert_traces_equal(
    left: Iterable[FrameRecord],
    right: Iterable[FrameRecord],
) -> None:
    """Raise an informative assertion when two traces differ."""

    comparison = compare_traces(left, right)
    if comparison.equal:
        return
    first = comparison.mismatches[0]
    fields_text = ", ".join(first.differing_fields)
    raise AssertionError(
        f"telemetry traces differ at frame {first.index}: {fields_text}; "
        f"left_frames={comparison.left_frames}, "
        f"right_frames={comparison.right_frames}"
    )


def verify_config_id(
    frames_to_check: Iterable[FrameRecord],
    expected_config_id: int,
) -> None:
    """Require every frame to carry the expected 16-bit config identifier."""

    _validate_integer("expected_config_id", expected_config_id, 0xFFFF)
    frames_tuple = tuple(frames_to_check)
    if not frames_tuple:
        raise ConfigMismatchError("cannot verify config_id in an empty trace")
    mismatched = [
        (index, frame.config_id)
        for index, frame in enumerate(frames_tuple)
        if frame.config_id != expected_config_id
    ]
    if mismatched:
        index, actual = mismatched[0]
        raise ConfigMismatchError(
            f"frame {index} has config_id {actual}, "
            f"expected {expected_config_id}"
        )


def load_experiment_config_id(path: str | Path) -> int:
    """Recompute a configuration ID from a validated experiment document."""

    return configuration_id(load_model_config(Path(path)))


def summarize_frames(frames_to_summarize: Iterable[FrameRecord]) -> dict[str, object]:
    """Build a compact JSON-serializable summary of a trace."""

    records = tuple(frames_to_summarize)
    if not records:
        return {"frames": 0}

    last = records[-1]
    speed_counts = (
        last.speed_1_count,
        last.speed_2_count,
        last.speed_3_count,
        last.speed_4_count,
    )
    predator_speed_counts = (
        last.predator_speed_1_count,
        last.predator_speed_2_count,
        last.predator_speed_3_count,
        last.predator_speed_4_count,
    )
    prey_count = sum(speed_counts)
    predator_count = sum(predator_speed_counts)
    interval_deltas = {
        field: sum(
            (
                getattr(current, field) - getattr(previous, field)
            ) & 0xFFFF
            for previous, current in zip(records, records[1:])
        )
        for field in (
            "captures",
            "forager_turnovers",
            "replacements",
            "starvations",
            "predator_forager_turnovers",
            "predator_replacements",
            "predator_starvations",
        )
    }
    tick_steps = [
        (current.tick - previous.tick) & 0xFFFF
        for previous, current in zip(records, records[1:])
    ]
    return {
        "captures": last.captures,
        "config_ids": sorted({frame.config_id for frame in records}),
        "final_population": {
            "mean_energy": (
                last.prey_energy_sum / prey_count if prey_count else None
            ),
            "mean_sense": (
                last.prey_sense_sum / prey_count if prey_count else None
            ),
            "mean_speed": (
                sum(
                    speed * count
                    for speed, count in enumerate(speed_counts, start=1)
                ) / prey_count
                if prey_count
                else None
            ),
            "prey_count": prey_count,
            "speed_counts": {
                str(speed): count
                for speed, count in enumerate(speed_counts, start=1)
            },
            "predator_count": predator_count,
            "predator_mean_energy": (
                last.predator_energy_sum / predator_count
                if predator_count
                else None
            ),
            "predator_mean_sense": (
                last.predator_sense_sum / predator_count
                if predator_count
                else None
            ),
            "predator_mean_speed": (
                sum(
                    speed * count
                    for speed, count in enumerate(
                        predator_speed_counts,
                        start=1,
                    )
                ) / predator_count
                if predator_count
                else None
            ),
            "predator_speed_counts": {
                str(speed): count
                for speed, count in enumerate(
                    predator_speed_counts,
                    start=1,
                )
            },
        },
        "first_tick": records[0].tick,
        "forager_turnovers": last.forager_turnovers,
        "frames": len(records),
        "interval_event_deltas": interval_deltas,
        "last_state_checksum": last.state_checksum,
        "last_tick": last.tick,
        "max_generation": max(frame.max_generation for frame in records),
        "predator_forager_turnovers": last.predator_forager_turnovers,
        "predator_max_generation": max(
            frame.predator_max_generation for frame in records
        ),
        "predator_replacements": last.predator_replacements,
        "predator_replacement_accounting_ok": (
            last.predator_replacements
            == (
                last.predator_starvations
                + last.predator_forager_turnovers
            ) & 0xFFFF
        ),
        "predator_starvations": last.predator_starvations,
        "replacements": last.replacements,
        "replacement_accounting_ok": (
            last.replacements
            == (
                last.captures
                + last.starvations
                + last.forager_turnovers
            ) & 0xFFFF
        ),
        "starvations": last.starvations,
        "tick_step": (
            tick_steps[0]
            if tick_steps and len(set(tick_steps)) == 1
            else None
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse Envo Agent OS binary telemetry.",
    )
    parser.add_argument(
        "trace",
        help="telemetry file, or '-' to read from standard input",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "jsonl"),
        default="summary",
        help="output format (default: summary)",
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        help="experiment.json whose config_id must match every frame",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on corrupt or truncated candidate frames",
    )
    return parser


def _read_trace(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the telemetry inspection CLI."""

    args = _build_parser().parse_args(argv)
    try:
        records = parse_frames(_read_trace(args.trace), strict=args.strict)
        if not records:
            raise TelemetryError("no telemetry frames found")
        if args.experiment is not None:
            expected_config_id = load_experiment_config_id(args.experiment)
            verify_config_id(records, expected_config_id)

        if args.format == "jsonl":
            for index, record in enumerate(records):
                item = {"frame_index": index, **asdict(record)}
                print(json.dumps(item, sort_keys=True))
        else:
            print(json.dumps(summarize_frames(records), indent=2, sort_keys=True))
        return 0
    except (OSError, TelemetryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
