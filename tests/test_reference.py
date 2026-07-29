from __future__ import annotations

from dataclasses import asdict, replace
import json

from envo_config import ModelConfig, configuration_id
from envo_reference import ReferenceModel, main
from envo_telemetry import encode_frame


LIVE_TICK_2_PACKET = (
    "455602011a51ee02001c3f0100010000000000f50bdc05001e0000010073676b"
)
TICK_1_PACKET = (
    "455602011a51ee010046f50100010000000000ff0bdc05001e000001001c68d8"
)
TICK_3_PACKET = (
    "455602011a51ee030039230100010000000000eb0bdc05001e00000100506796"
)


def test_default_reference_matches_live_guest_tick_two() -> None:
    frames = ReferenceModel(ModelConfig()).run(2)
    tick_two = frames[-1]

    assert encode_frame(tick_two).hex() == LIVE_TICK_2_PACKET
    assert asdict(tick_two) == {
        "config_id": 0xEE51,
        "tick": 2,
        "rng_state": 0x3F1C,
        "replacements": 1,
        "captures": 1,
        "starvations": 0,
        "forager_turnovers": 0,
        "prey_energy_sum": 3061,
        "prey_sense_sum": 1500,
        "speed_1_count": 0,
        "speed_2_count": 30,
        "speed_3_count": 0,
        "speed_4_count": 0,
        "max_generation": 1,
        "state_checksum": 0x6773,
    }


def test_default_ticks_one_through_three_are_stable() -> None:
    packets = [
        encode_frame(frame).hex()
        for frame in ReferenceModel(ModelConfig()).run(3)
    ]

    assert packets == [
        TICK_1_PACKET,
        LIVE_TICK_2_PACKET,
        TICK_3_PACKET,
    ]


def test_reference_runs_are_deterministic() -> None:
    config = ModelConfig()
    first = ReferenceModel(config)
    replay = ReferenceModel(config)

    assert first.entity_region_bytes() == replay.entity_region_bytes()
    assert first.run(12) == replay.run(12)
    assert first.entity_region_bytes() == replay.entity_region_bytes()
    assert first.rng_state == replay.rng_state


def test_alternate_seed_changes_identity_and_trajectory() -> None:
    default = ReferenceModel(ModelConfig())
    alternate_config = replace(ModelConfig(), seed=1)
    alternate = ReferenceModel(alternate_config)

    default_frames = default.run(3)
    alternate_frames = alternate.run(3)

    assert alternate.config_id == configuration_id(alternate_config)
    assert alternate.config_id != default.config_id
    assert alternate_frames != default_frames
    assert alternate.entity_region_bytes() != default.entity_region_bytes()


def test_telemetry_interval_only_changes_observation_schedule() -> None:
    every_tick = ReferenceModel(ModelConfig())
    sparse = ReferenceModel(replace(ModelConfig(), telemetry_interval=4))

    dense_frames = every_tick.run(8)
    sparse_results = [sparse.step() for _ in range(8)]
    sparse_frames = [frame for frame in sparse_results if frame is not None]

    assert [frame.tick for frame in dense_frames] == list(range(1, 9))
    assert [frame.tick for frame in sparse_frames] == [4, 8]
    assert sparse.entity_region_bytes() == every_tick.entity_region_bytes()
    assert sparse.rng_state == every_tick.rng_state


def test_night_fireflies_do_not_advance_the_model_rng(monkeypatch) -> None:
    config = replace(ModelConfig(), day_night_mask=1)
    model = ReferenceModel(config)
    before = model.rng_state

    monkeypatch.setattr(
        model,
        "_update_prey",
        lambda _index, *, night: None,
    )
    monkeypatch.setattr(model, "_update_predator", lambda _index: None)
    model.step()

    assert model.rng_state == before


def test_entity_region_serialization_and_checksum() -> None:
    config = ModelConfig()
    model = ReferenceModel(config)
    expected_bytes = (
        config.food_count * 6
        + config.prey_count * 12
        + config.predator_count * 12
    )

    assert len(model.entity_region_bytes()) == expected_bytes
    assert model.frame_record().state_checksum == model.state_checksum()


def test_single_prey_can_be_its_own_respawn_donor() -> None:
    config = replace(
        ModelConfig(),
        prey_count=1,
        predator_count=1,
    )
    model = ReferenceModel(config)
    prey = model.prey[0]
    old_generation = prey.generation

    model._rebirth_from_random_donor(prey)

    assert prey.generation == old_generation + 1
    assert model.replacements == 1


def test_cli_emits_jsonl_for_requested_ticks(capsys) -> None:
    result = main(["--ticks", "2", "--seed", "0xACE1"])
    output = capsys.readouterr()
    lines = output.out.splitlines()

    assert result == 0
    assert len(lines) == 2
    assert json.loads(lines[-1])["packet_hex"] == LIVE_TICK_2_PACKET
    assert output.err == ""
