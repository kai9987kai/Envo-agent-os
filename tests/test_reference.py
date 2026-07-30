from __future__ import annotations

from dataclasses import asdict, replace
import json

from envo_config import ModelConfig, configuration_id
from envo_reference import ReferenceModel, main
from envo_telemetry import encode_frame


TICK_1_PACKET = (
    "455603012aca2a010046f50100010000000000e20bdc05001e000001001368"
    "000000000000a4019001000004000000fe"
)
TICK_2_PACKET = (
    "455603012aca2a02001c3f0100010000000000ba0bdc05001e000001003867"
    "00000000000090019001000004000000f5"
)
TICK_3_PACKET = (
    "455603012aca2a030039230100010000000000920bdc05001e00000100e366"
    "0000000000007c01900100000400000085"
)


def test_default_reference_tick_two_is_stable() -> None:
    frames = ReferenceModel(ModelConfig()).run(2)
    tick_two = frames[-1]

    assert encode_frame(tick_two).hex() == TICK_2_PACKET
    assert asdict(tick_two) == {
        "config_id": 0x2ACA,
        "tick": 2,
        "rng_state": 0x3F1C,
        "replacements": 1,
        "captures": 1,
        "starvations": 0,
        "forager_turnovers": 0,
        "prey_energy_sum": 3002,
        "prey_sense_sum": 1500,
        "speed_1_count": 0,
        "speed_2_count": 30,
        "speed_3_count": 0,
        "speed_4_count": 0,
        "max_generation": 1,
        "state_checksum": 0x6738,
        "predator_replacements": 0,
        "predator_starvations": 0,
        "predator_forager_turnovers": 0,
        "predator_energy_sum": 400,
        "predator_sense_sum": 400,
        "predator_speed_1_count": 0,
        "predator_speed_2_count": 0,
        "predator_speed_3_count": 4,
        "predator_speed_4_count": 0,
        "predator_max_generation": 0,
    }


def test_default_ticks_one_through_three_are_stable() -> None:
    packets = [
        encode_frame(frame).hex()
        for frame in ReferenceModel(ModelConfig()).run(3)
    ]

    assert packets == [
        TICK_1_PACKET,
        TICK_2_PACKET,
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


def test_prey_energy_equal_to_cost_turns_over_without_underflow() -> None:
    config = replace(ModelConfig(), prey_count=1, predator_count=1)
    model = ReferenceModel(config)
    prey = model.prey[0]
    prey.energy = model._metabolic_cost(
        prey,
        base=config.prey_base_metabolism,
    )

    model._update_prey(0, night=False)

    assert model.starvations == 1
    assert model.replacements == 1
    assert prey.energy == config.initial_energy
    assert prey.energy != 0xFFFF


def test_predator_starvation_is_replacement_accounted() -> None:
    config = replace(ModelConfig(), prey_count=1, predator_count=1)
    model = ReferenceModel(config)
    predator = model.predators[0]
    old_generation = predator.generation
    predator.energy = model._metabolic_cost(
        predator,
        base=config.predator_base_metabolism,
    )

    model._update_predator(0)

    assert model.predator_starvations == 1
    assert model.predator_forager_turnovers == 0
    assert model.predator_replacements == 1
    assert model.predator_replacements == (
        model.predator_starvations + model.predator_forager_turnovers
    )
    assert predator.energy == config.initial_energy
    assert predator.generation == old_generation + 1


def test_predator_capture_fuels_successful_turnover_and_inheritance(
    monkeypatch,
) -> None:
    config = replace(ModelConfig(), prey_count=1, predator_count=1)
    model = ReferenceModel(config)
    prey = model.prey[0]
    predator = model.predators[0]
    prey.x = predator.x = 100
    prey.y = predator.y = 100
    predator.speed = config.speed_max
    predator.sense = config.sense_max
    predator.generation = 7
    metabolic_cost = model._metabolic_cost(
        predator,
        base=config.predator_base_metabolism,
    )
    predator.energy = (
        config.predator_reproduction_energy
        - config.predator_capture_energy
        + metabolic_cost
    )
    monkeypatch.setattr(model, "_next_random", lambda: 1)

    model._update_predator(0)

    assert model.captures == 1
    assert model.predator_starvations == 0
    assert model.predator_forager_turnovers == 1
    assert model.predator_replacements == 1
    assert predator.energy == config.initial_energy
    assert predator.speed == config.speed_max
    assert predator.sense == config.sense_max
    assert predator.generation == 8


def test_trait_cost_is_monotonic_in_speed_and_sense() -> None:
    config = ModelConfig()
    model = ReferenceModel(config)
    agent = model.prey[0]
    agent.speed = config.speed_min
    agent.sense = config.sense_min
    baseline = model._metabolic_cost(
        agent,
        base=config.prey_base_metabolism,
    )

    agent.speed = config.speed_max
    faster = model._metabolic_cost(
        agent,
        base=config.prey_base_metabolism,
    )
    agent.speed = config.speed_min
    agent.sense = config.sense_max
    more_sensing = model._metabolic_cost(
        agent,
        base=config.prey_base_metabolism,
    )
    agent.speed = config.speed_max
    combined = model._metabolic_cost(
        agent,
        base=config.prey_base_metabolism,
    )

    assert baseline < faster < combined
    assert baseline < more_sensing < combined


def test_large_downward_sense_mutation_clamps_without_word_underflow(
    monkeypatch,
) -> None:
    config = replace(
        ModelConfig(),
        prey_count=1,
        predator_count=1,
        mutation_mask=1,
        sense_mutation_step=127,
    )
    model = ReferenceModel(config)
    destination = model.prey[0]
    parent = model.prey[0]
    parent.sense = config.sense_min
    random_values = iter((1, 0, 0))
    monkeypatch.setattr(model, "_next_random", lambda: next(random_values))

    model._inherit(destination, parent, predator=False)

    assert destination.sense == config.sense_min


def test_cli_emits_jsonl_for_requested_ticks(capsys) -> None:
    result = main(["--ticks", "2", "--seed", "0xACE1"])
    output = capsys.readouterr()
    lines = output.out.splitlines()

    assert result == 0
    assert len(lines) == 2
    assert json.loads(lines[-1])["packet_hex"] == TICK_2_PACKET
    assert output.err == ""
