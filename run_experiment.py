"""Run Envo Agent OS headlessly and capture validated telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence

from envo_config import load_model_config
from envo_telemetry import (
    FRAME_LENGTH,
    FRAME_TYPE,
    MAGIC,
    PAYLOAD_LENGTH,
    PROTOCOL_VERSION,
    FrameRecord,
    TelemetryError,
    decode_frame,
    load_experiment_config_id,
    summarize_frames,
    verify_config_id,
)


class ExperimentRunError(RuntimeError):
    """Raised when a headless experiment cannot produce a valid trace."""


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def qemu_command(
    executable: str,
    media: Path,
    media_type: str,
    telemetry_path: Path,
) -> list[str]:
    """Construct the isolated QEMU command used by the experiment runner."""

    command = [
        executable,
        "-machine",
        "pc,accel=tcg",
        "-m",
        "16M",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "none",
        "-nic",
        "none",
        "-no-reboot",
        "-no-shutdown",
        "-debugcon",
        f"file:{telemetry_path}",
        "-global",
        "isa-debugcon.iobase=0xe9",
    ]
    if media_type == "iso":
        command.extend(
            [
                "-boot",
                "d",
                "-drive",
                f"file={media},format=raw,media=cdrom,readonly=on",
            ]
        )
    elif media_type == "floppy":
        command.extend(
            [
                "-boot",
                "a",
                "-drive",
                f"file={media},format=raw,if=floppy,readonly=on",
            ]
        )
    else:
        raise ValueError(f"unsupported media type: {media_type}")
    return command


def _resolve_qemu(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved:
        return resolved
    candidate = Path(executable)
    if candidate.is_file():
        return str(candidate.resolve())
    raise ExperimentRunError(
        f"QEMU executable not found: {executable!r}; install qemu-system-x86"
    )


def _stop_process(process: subprocess.Popen[bytes]) -> bytes:
    if process.poll() is None:
        process.terminate()
        try:
            _, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate(timeout=3)
        return stderr
    _, stderr = process.communicate(timeout=3)
    return stderr


def _ordered_boot_markers(raw_trace: bytes) -> tuple[int, int] | None:
    boot = raw_trace.find(b"B")
    if boot < 0:
        return None
    kernel = raw_trace.find(b"K", boot + 1)
    if kernel < 0:
        return None
    return boot, kernel


def validate_trace_sequence(
    raw_trace: bytes,
    telemetry_interval: int,
    *,
    allow_truncated_tail: bool = True,
) -> list[FrameRecord]:
    """Validate the exact post-boot frame sequence in a debugcon trace.

    Debugcon writes are an ordered byte stream: after the ``B`` and ``K`` stage
    markers, each complete telemetry record must begin at the next fixed frame
    boundary. The only non-frame bytes accepted there are a final prefix of a
    frame, because QEMU can be observed or terminated while that record is
    still being written.
    """

    if (
        isinstance(telemetry_interval, bool)
        or not isinstance(telemetry_interval, int)
        or not 1 <= telemetry_interval <= 0xFFFF
    ):
        raise ValueError("telemetry_interval must be in the range 1..65535")

    markers = _ordered_boot_markers(raw_trace)
    if markers is None:
        raise ExperimentRunError("trace is missing ordered B -> K boot markers")
    _, kernel = markers
    frame_stream = raw_trace[kernel + 1 :]

    frames: list[FrameRecord] = []
    offset = 0
    while len(frame_stream) - offset >= FRAME_LENGTH:
        candidate = frame_stream[offset : offset + FRAME_LENGTH]
        try:
            frames.append(decode_frame(candidate))
        except TelemetryError as exc:
            absolute_offset = kernel + 1 + offset
            raise ExperimentRunError(
                "corrupt complete telemetry frame "
                f"{len(frames)} at byte offset {absolute_offset}: {exc}"
            ) from exc
        offset += FRAME_LENGTH

    tail = frame_stream[offset:]
    if tail:
        header = MAGIC + bytes((PROTOCOL_VERSION, FRAME_TYPE, PAYLOAD_LENGTH))
        prefix_length = min(len(tail), len(header))
        if tail[:prefix_length] != header[:prefix_length]:
            absolute_offset = kernel + 1 + offset
            raise ExperimentRunError(
                "unexpected bytes after complete telemetry frames at byte "
                f"offset {absolute_offset}; trailing data is not a frame prefix"
            )
        if not allow_truncated_tail:
            raise ExperimentRunError(
                "trace ends with a truncated telemetry frame: "
                f"received {len(tail)} of {FRAME_LENGTH} bytes"
            )

    if frames and frames[0].tick != telemetry_interval:
        raise ExperimentRunError(
            f"first telemetry tick is {frames[0].tick}, expected {telemetry_interval}"
        )
    for index, (previous, current) in enumerate(
        zip(frames, frames[1:]),
        start=1,
    ):
        expected_tick = (previous.tick + telemetry_interval) & 0xFFFF
        if current.tick != expected_tick:
            raise ExperimentRunError(
                f"telemetry frame {index} has tick {current.tick}, "
                f"expected {expected_tick} after tick {previous.tick}"
            )

    payload_offset = len(MAGIC) + 3
    tick_offset = payload_offset + 2
    tick_end = tick_offset + 2
    if tail and len(tail) >= tick_end:
        partial_tick = int.from_bytes(tail[tick_offset:tick_end], "little")
        expected_tick = (
            telemetry_interval
            if not frames
            else (frames[-1].tick + telemetry_interval) & 0xFFFF
        )
        if partial_tick != expected_tick:
            raise ExperimentRunError(
                "truncated telemetry frame has tick "
                f"{partial_tick}, expected {expected_tick}"
            )

    return frames


def capture_experiment(
    *,
    media: Path,
    media_type: str,
    experiment: Path,
    output: Path,
    frames_required: int,
    timeout_seconds: float,
    qemu: str,
) -> dict[str, object]:
    """Capture at least ``frames_required`` valid, configuration-bound frames."""

    if not media.is_file():
        raise ExperimentRunError(f"boot media not found: {media}")
    if not experiment.is_file():
        raise ExperimentRunError(f"experiment identity not found: {experiment}")

    config = load_model_config(experiment)
    expected_config_id = load_experiment_config_id(experiment)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    if temporary.exists():
        temporary.unlink()

    command = qemu_command(
        _resolve_qemu(qemu),
        media.resolve(),
        media_type,
        temporary,
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + timeout_seconds
    captured = []
    while time.monotonic() < deadline:
        if temporary.is_file():
            current_trace = temporary.read_bytes()
            if _ordered_boot_markers(current_trace) is not None:
                try:
                    captured = validate_trace_sequence(
                        current_trace,
                        config.telemetry_interval,
                    )
                except ExperimentRunError:
                    break
                if len(captured) >= frames_required:
                    break
        if process.poll() is not None:
            break
        time.sleep(0.05)

    stderr = _stop_process(process)
    raw_trace = temporary.read_bytes() if temporary.is_file() else b""
    if temporary.is_file():
        temporary.replace(output)
    captured = validate_trace_sequence(
        raw_trace,
        config.telemetry_interval,
    )

    if len(captured) < frames_required:
        detail = stderr.decode("utf-8", errors="replace").strip()
        suffix = f"; QEMU: {detail}" if detail else ""
        raise ExperimentRunError(
            f"captured {len(captured)} valid frame(s), expected at least "
            f"{frames_required} within {timeout_seconds:g} seconds; "
            f"trace retained at {output}{suffix}"
        )
    verify_config_id(captured, expected_config_id)
    summary = summarize_frames(captured)
    summary.update(
        {
            "experiment": str(experiment.resolve()),
            "media": str(media.resolve()),
            "media_type": media_type,
            "trace": str(output),
        }
    )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Boot Envo Agent OS in headless QEMU and capture validated telemetry."
        )
    )
    parser.add_argument("media", type=Path, help="os.iso or floppy.img")
    parser.add_argument(
        "--media-type",
        choices=("iso", "floppy"),
        help="boot-media type (default: inferred from extension)",
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        help="matching experiment.json (default: beside the media)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("telemetry.bin"),
        help="captured binary trace (default: telemetry.bin)",
    )
    parser.add_argument(
        "--frames",
        type=_positive_int,
        default=1,
        help="minimum complete frames to capture (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=15.0,
        help="maximum runtime in seconds (default: 15)",
    )
    parser.add_argument(
        "--qemu",
        default="qemu-system-i386",
        help="QEMU executable name or path",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="also write the summary as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    media_type = args.media_type
    if media_type is None:
        media_type = "iso" if args.media.suffix.lower() == ".iso" else "floppy"
    experiment = args.experiment or args.media.with_name("experiment.json")

    try:
        summary = capture_experiment(
            media=args.media,
            media_type=media_type,
            experiment=experiment,
            output=args.output,
            frames_required=args.frames,
            timeout_seconds=args.timeout,
            qemu=args.qemu,
        )
        rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
        sys.stdout.write(rendered)
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(rendered, encoding="utf-8")
        return 0
    except (ExperimentRunError, OSError, TelemetryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
