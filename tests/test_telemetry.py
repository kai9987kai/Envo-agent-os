from __future__ import annotations

from dataclasses import replace
import json

import pytest

from envo_telemetry import (
    FRAME_LENGTH,
    FrameChecksumError,
    FrameRecord,
    FrameStreamParser,
    TruncatedFrameError,
    assert_traces_equal,
    compare_trace_bytes,
    compare_traces,
    encode_frame,
    load_experiment_config_id,
    main,
    parse_frames,
    summarize_frames,
    verify_config_id,
)


def _frame(**changes: int) -> FrameRecord:
    base = FrameRecord(
        config_id=0x1234,
        tick=42,
        rng_state=0xACE1,
        replacements=3,
        captures=4,
        starvations=5,
        forager_turnovers=6,
        prey_energy_sum=3_000,
        prey_sense_sum=1_500,
        speed_1_count=7,
        speed_2_count=8,
        speed_3_count=9,
        speed_4_count=6,
        max_generation=10,
        state_checksum=0xBEEF,
    )
    return replace(base, **changes)


def test_frame_round_trip_and_checksum_contract() -> None:
    frame = _frame()
    encoded = encode_frame(frame)

    assert len(encoded) == FRAME_LENGTH == 32
    assert encoded[:5] == b"EV\x02\x01\x1a"
    assert sum(encoded[2:]) & 0xFF == 0
    assert parse_frames(encoded, strict=True) == [frame]


def test_frame_wire_format_has_a_stable_golden_vector() -> None:
    assert encode_frame(_frame()).hex() == (
        "455602011a34122a00e1ac0300040005000600b80bdc05"
        "070809060a00efbe5b"
    )


def test_all_counter_widths_round_trip_at_maximum() -> None:
    frame = _frame(
        config_id=0xFFFF,
        tick=0xFFFF,
        rng_state=0xFFFF,
        replacements=0xFFFF,
        captures=0xFFFF,
        starvations=0xFFFF,
        forager_turnovers=0xFFFF,
        prey_energy_sum=0xFFFF,
        prey_sense_sum=0xFFFF,
        speed_1_count=0xFF,
        speed_2_count=0xFF,
        speed_3_count=0xFF,
        speed_4_count=0xFF,
        max_generation=0xFFFF,
        state_checksum=0xFFFF,
    )

    assert parse_frames(encode_frame(frame), strict=True) == [frame]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("captures", 0x1_0000),
        ("tick", -1),
        ("speed_1_count", 0x100),
    ],
)
def test_record_rejects_values_outside_wire_width(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValueError):
        _frame(**{field: value})


def test_strict_mode_detects_checksum_corruption() -> None:
    encoded = bytearray(encode_frame(_frame()))
    encoded[12] ^= 0x40

    with pytest.raises(FrameChecksumError, match="checksum"):
        parse_frames(encoded, strict=True)


def test_lenient_mode_resynchronizes_after_corrupt_frame() -> None:
    corrupt = bytearray(encode_frame(_frame(tick=1)))
    corrupt[-1] ^= 1
    expected = _frame(tick=2)

    assert parse_frames(bytes(corrupt) + encode_frame(expected)) == [expected]


def test_strict_mode_detects_truncated_frame() -> None:
    encoded = encode_frame(_frame())

    with pytest.raises(TruncatedFrameError, match="truncated"):
        parse_frames(encoded[:-1], strict=True)
    assert parse_frames(encoded[:-1]) == []


def test_stream_parser_handles_garbage_and_split_magic() -> None:
    first = _frame(tick=1)
    second = _frame(tick=2)
    parser = FrameStreamParser(strict=True)

    assert parser.feed(b"noiseE") == []
    assert parser.feed(b"V" + encode_frame(first)[2:11]) == []
    assert parser.feed(encode_frame(first)[11:] + b"\x00junk") == [first]
    assert parser.feed(encode_frame(second)) == [second]
    assert parser.finish() == []


def test_stream_parser_detects_partial_magic_when_finished() -> None:
    parser = FrameStreamParser(strict=True)
    parser.feed(b"garbageE")

    with pytest.raises(TruncatedFrameError):
        parser.finish()


def test_trace_comparison_reports_fields_and_missing_frames() -> None:
    first = _frame(tick=1)
    changed = _frame(tick=2, captures=9)
    left = [first, changed]
    right = [first, _frame(tick=2), _frame(tick=3)]

    comparison = compare_traces(left, right)

    assert not comparison
    assert comparison.compared_frames == 2
    assert comparison.left_frames == 2
    assert comparison.right_frames == 3
    assert comparison.mismatches[0].index == 1
    assert comparison.mismatches[0].differing_fields == ("captures",)
    assert comparison.mismatches[1].differing_fields == ("<missing>",)

    with pytest.raises(AssertionError, match="captures"):
        assert_traces_equal(left, right)


def test_encoded_trace_comparison() -> None:
    frames = [_frame(tick=1), _frame(tick=2)]
    encoded = b"".join(encode_frame(frame) for frame in frames)

    assert compare_trace_bytes(encoded, encoded).equal
    assert not compare_trace_bytes(
        encoded,
        encode_frame(frames[0]),
    ).equal


def test_summary_derives_population_metrics_and_wrapping_deltas() -> None:
    first = _frame(
        tick=0xFFFF,
        replacements=0xFFFF,
        captures=0xFFFF,
        starvations=0,
        forager_turnovers=0,
    )
    second = _frame(
        tick=1,
        replacements=2,
        captures=1,
        starvations=1,
        forager_turnovers=0,
        prey_energy_sum=3_000,
        prey_sense_sum=1_500,
    )

    summary = summarize_frames([first, second])
    population = summary["final_population"]

    assert summary["tick_step"] == 2
    assert summary["interval_event_deltas"]["replacements"] == 3
    assert summary["replacement_accounting_ok"]
    assert population["prey_count"] == 30
    assert population["mean_energy"] == 100
    assert population["mean_sense"] == 50
    assert population["mean_speed"] == pytest.approx(2.4666666667)


def test_experiment_config_verification_accepts_hex_string(tmp_path) -> None:
    experiment = tmp_path / "experiment.json"
    experiment.write_text(
        json.dumps({"model": {"config_id": "0x1234"}}),
        encoding="utf-8",
    )
    frames = [_frame(tick=1), _frame(tick=2)]

    assert load_experiment_config_id(experiment) == 0x1234
    verify_config_id(frames, 0x1234)

    with pytest.raises(ValueError, match="expected"):
        verify_config_id(frames, 0x9999)


def test_cli_outputs_jsonl_and_checks_experiment(
    tmp_path,
    capsys,
) -> None:
    trace = tmp_path / "trace.bin"
    trace.write_bytes(encode_frame(_frame(tick=1)) + encode_frame(_frame(tick=2)))
    experiment = tmp_path / "experiment.json"
    experiment.write_text(
        json.dumps({"config_id": 0x1234}),
        encoding="utf-8",
    )

    result = main(
        [
            str(trace),
            "--strict",
            "--format",
            "jsonl",
            "--experiment",
            str(experiment),
        ]
    )
    output = capsys.readouterr()
    lines = output.out.splitlines()

    assert result == 0
    assert len(lines) == 2
    assert json.loads(lines[0])["tick"] == 1
    assert json.loads(lines[1])["frame_index"] == 1
    assert output.err == ""


def test_cli_summary_and_config_mismatch(tmp_path, capsys) -> None:
    trace = tmp_path / "trace.bin"
    trace.write_bytes(encode_frame(_frame(tick=7, captures=8)))

    assert main([str(trace), "--strict"]) == 0
    summary_output = capsys.readouterr()
    summary = json.loads(summary_output.out)
    assert summary["frames"] == 1
    assert summary["last_tick"] == 7
    assert summary["captures"] == 8

    experiment = tmp_path / "experiment.json"
    experiment.write_text(json.dumps({"config_id": 1}), encoding="utf-8")
    assert main([str(trace), "--experiment", str(experiment)]) == 1
    mismatch_output = capsys.readouterr()
    assert "config_id" in mismatch_output.err
