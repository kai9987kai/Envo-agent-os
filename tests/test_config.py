from __future__ import annotations

from dataclasses import replace
import json

import pytest

from create_iso import (
    VERSION,
    build_artifacts_config,
    build_kernel_config,
    main,
)
from envo_config import (
    ModelConfig,
    RuntimeLayout,
    build_experiment_document,
    configuration_id,
    configuration_sha256,
    load_model_config,
)


def test_experiment_identity_is_canonical_and_sensitive() -> None:
    config = ModelConfig()
    replay = ModelConfig()
    changed = replace(config, prey_initial_sense=58)

    assert configuration_id(config) == configuration_id(replay)
    assert configuration_sha256(config) == configuration_sha256(replay)
    assert configuration_sha256(changed) != configuration_sha256(config)
    assert configuration_id(changed) != configuration_id(config)


def test_generated_experiment_document_round_trips_as_build_input(
    tmp_path,
) -> None:
    config = replace(
        ModelConfig(),
        seed=1234,
        prey_count=24,
        telemetry_interval=8,
    )
    path = tmp_path / "experiment.json"
    path.write_bytes(build_experiment_document(config, VERSION))

    loaded = load_model_config(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == config
    assert document["config_id"] == configuration_id(config)
    assert document["configuration"] == config.to_dict()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_id", 0),
        ("config_sha256", "0" * 64),
        ("model_abi_version", 0),
        ("runtime_layout", {}),
        ("telemetry", {}),
    ],
)
def test_generated_experiment_rejects_tampered_identity(
    tmp_path,
    field: str,
    value: object,
) -> None:
    document = json.loads(build_experiment_document(ModelConfig(), VERSION))
    document[field] = value
    path = tmp_path / "tampered-experiment.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical identity"):
        load_model_config(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"prey_count": 0},
        {"speed_max": 5},
        {"telemetry_interval": 3},
        {"reproduction_energy": 32_000, "food_energy": 1_000},
        {"prey_count": 32, "reproduction_energy": 3_000},
        {"prey_base_metabolism": 0},
        {"predator_base_metabolism": 100},
        {"speed_metabolism": 128},
        {"sense_metabolism_shift": 0},
        {
            "predator_reproduction_energy": 32_000,
            "predator_capture_energy": 1_000,
        },
        {"predator_count": 16, "predator_reproduction_energy": 5_000},
    ],
)
def test_invalid_or_unobservable_configurations_are_rejected(
    changes: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        replace(ModelConfig(), **changes)


def test_every_coevolution_parameter_changes_guest_code() -> None:
    config = ModelConfig()
    baseline = build_kernel_config(config)
    variants = (
        replace(config, prey_base_metabolism=2),
        replace(config, predator_base_metabolism=2),
        replace(config, speed_metabolism=2),
        replace(config, sense_metabolism_shift=4),
        replace(config, predator_capture_energy=41),
        replace(config, predator_reproduction_energy=161),
    )

    assert all(build_kernel_config(variant) != baseline for variant in variants)


def test_custom_configuration_changes_kernel_and_embeds_identity() -> None:
    default = ModelConfig()
    custom = replace(
        default,
        seed=7,
        food_count=40,
        prey_count=24,
        predator_count=6,
        telemetry_interval=4,
    )

    artifacts = build_artifacts_config(custom)
    manifest = json.loads(artifacts.manifest)
    experiment = json.loads(artifacts.experiment)

    assert artifacts.kernel == build_kernel_config(custom)
    assert artifacts.kernel != build_kernel_config(default)
    assert manifest["config_id"] == configuration_id(custom)
    assert manifest["config_sha256"] == configuration_sha256(custom)
    assert manifest["telemetry"]["interval_ticks"] == 4
    assert experiment["config_id"] == manifest["config_id"]


def test_runtime_layout_keeps_model_state_below_vga_memory() -> None:
    layout = RuntimeLayout.from_config(
        replace(
            ModelConfig(),
            food_count=128,
            prey_count=32,
            predator_count=16,
        )
    )

    assert layout.telemetry_buffer < layout.scratch_start < layout.food_base
    assert layout.food_base < layout.prey_base < layout.predator_base
    assert layout.data_end < 0xA000


def test_cli_rebuilds_from_experiment_and_allows_explicit_overrides(
    tmp_path,
) -> None:
    source = replace(ModelConfig(), seed=10, telemetry_interval=8)
    config_path = tmp_path / "source-experiment.json"
    config_path.write_bytes(build_experiment_document(source, VERSION))
    output_dir = tmp_path / "build"

    result = main(
        [
            "--config",
            str(config_path),
            "--seed",
            "11",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result == 0
    rebuilt = load_model_config(output_dir / "experiment.json")
    assert rebuilt == replace(source, seed=11)
