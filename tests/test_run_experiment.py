from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import run_experiment as runner
from envo_config import (
    ModelConfig,
    build_experiment_document,
    configuration_id,
)
from envo_telemetry import FRAME_LENGTH, FrameRecord, encode_frame
from run_experiment import (
    ExperimentRunError,
    capture_experiment,
    validate_trace_sequence,
)


def _frame(tick: int) -> FrameRecord:
    return FrameRecord(
        config_id=0x1234,
        tick=tick,
        rng_state=0xACE1,
        replacements=3,
        captures=1,
        starvations=1,
        forager_turnovers=1,
        prey_energy_sum=3_000,
        prey_sense_sum=1_500,
        speed_1_count=5,
        speed_2_count=10,
        speed_3_count=10,
        speed_4_count=5,
        max_generation=2,
        state_checksum=0xBEEF,
        predator_replacements=2,
        predator_starvations=1,
        predator_forager_turnovers=1,
        predator_energy_sum=400,
        predator_sense_sum=400,
        predator_speed_1_count=0,
        predator_speed_2_count=1,
        predator_speed_3_count=2,
        predator_speed_4_count=1,
        predator_max_generation=1,
    )


@pytest.mark.parametrize("tail_length", [1, 2, 5, FRAME_LENGTH - 1])
def test_valid_sequence_allows_only_a_truncated_final_frame(
    tail_length: int,
) -> None:
    first = _frame(4)
    second = replace(first, tick=8, rng_state=0x1111)
    third = replace(first, tick=12, rng_state=0x2222)
    trace = (
        b"BK"
        + encode_frame(first)
        + encode_frame(second)
        + encode_frame(third)[:tail_length]
    )

    assert validate_trace_sequence(trace, 4) == [first, second]


def test_valid_sequence_handles_wrapping_ticks() -> None:
    first = _frame(0x8000)
    second = replace(first, tick=0)

    assert validate_trace_sequence(
        b"BK" + encode_frame(first) + encode_frame(second),
        0x8000,
    ) == [first, second]


def test_corrupt_complete_frame_is_not_resynchronized_away() -> None:
    corrupt = bytearray(encode_frame(_frame(4)))
    corrupt[10] ^= 0x01
    trace = b"BK" + bytes(corrupt) + encode_frame(_frame(8))

    with pytest.raises(
        ExperimentRunError,
        match=r"corrupt complete telemetry frame 0",
    ):
        validate_trace_sequence(trace, 4)


def test_missing_byte_from_complete_frame_is_detected() -> None:
    first = encode_frame(_frame(4))
    second = encode_frame(_frame(8))
    trace = b"BK" + first[:20] + first[21:] + second

    with pytest.raises(
        ExperimentRunError,
        match=r"corrupt complete telemetry frame 0",
    ):
        validate_trace_sequence(trace, 4)


def test_missing_first_complete_frame_is_detected_by_tick() -> None:
    trace = b"BK" + encode_frame(_frame(8))

    with pytest.raises(
        ExperimentRunError,
        match=r"first telemetry tick is 8, expected 4",
    ):
        validate_trace_sequence(trace, 4)


def test_missing_middle_complete_frame_is_detected_by_tick_gap() -> None:
    trace = b"BK" + encode_frame(_frame(4)) + encode_frame(_frame(12))

    with pytest.raises(
        ExperimentRunError,
        match=r"frame 1 has tick 12, expected 8",
    ):
        validate_trace_sequence(trace, 4)


def test_missing_frame_before_truncated_tail_is_detected_when_tick_arrives() -> None:
    partial_tick_twelve = encode_frame(_frame(12))[:9]
    trace = b"BK" + encode_frame(_frame(4)) + partial_tick_twelve

    with pytest.raises(
        ExperimentRunError,
        match=r"truncated telemetry frame has tick 12, expected 8",
    ):
        validate_trace_sequence(trace, 4)


def test_non_frame_trailing_bytes_are_rejected() -> None:
    trace = b"BK" + encode_frame(_frame(4)) + b"not-a-frame"

    with pytest.raises(
        ExperimentRunError,
        match=r"trailing data is not a frame prefix",
    ):
        validate_trace_sequence(trace, 4)


def test_truncated_tail_can_be_explicitly_disallowed() -> None:
    trace = b"BK" + encode_frame(_frame(4)) + encode_frame(_frame(8))[:7]

    with pytest.raises(
        ExperimentRunError,
        match=r"ends with a truncated telemetry frame",
    ):
        validate_trace_sequence(
            trace,
            4,
            allow_truncated_tail=False,
        )


def test_capture_accepts_truncated_tail_during_poll_and_final_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(ModelConfig(), telemetry_interval=4)
    experiment = tmp_path / "experiment.json"
    experiment.write_bytes(build_experiment_document(config, "test"))
    media = tmp_path / "os.iso"
    media.write_bytes(b"test media")
    output = tmp_path / "telemetry.bin"

    first = replace(_frame(4), config_id=configuration_id(config))
    partial_second = encode_frame(replace(first, tick=8))[:17]
    raw_trace = b"BK" + encode_frame(first) + partial_second

    class CompletedProcess:
        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def communicate(timeout: float) -> tuple[bytes, bytes]:
            assert timeout == 3
            return b"", b""

    def fake_popen(command: list[str], **_kwargs: object) -> CompletedProcess:
        debugcon = command[command.index("-debugcon") + 1]
        trace_path = Path(debugcon.removeprefix("file:"))
        trace_path.write_bytes(raw_trace)
        return CompletedProcess()

    monkeypatch.setattr(runner, "_resolve_qemu", lambda _executable: "qemu")
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    summary = capture_experiment(
        media=media,
        media_type="iso",
        experiment=experiment,
        output=output,
        frames_required=1,
        timeout_seconds=1,
        qemu="qemu",
    )

    assert summary["frames"] == 1
    assert summary["first_tick"] == 4
    assert output.read_bytes() == raw_trace


@pytest.mark.parametrize("trace", [b"", b"B", b"KB"])
def test_ordered_boot_markers_are_required(trace: bytes) -> None:
    with pytest.raises(
        ExperimentRunError,
        match=r"missing ordered B -> K",
    ):
        validate_trace_sequence(trace, 1)
