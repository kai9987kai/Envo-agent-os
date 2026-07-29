from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
import struct

import pytest

from create_iso import (
    MAX_KERNEL_SECTORS,
    SECTOR_SIZE,
    VERSION,
    assemble_kernel_config,
    build_artifacts_config,
)
from envo_config import (
    AGENT_RECORD_BYTES,
    DATA_BASE,
    EXPERIMENT_FORMAT_VERSION,
    FOOD_RECORD_BYTES,
    MODEL_ABI_VERSION,
    TELEMETRY_FRAME_TYPE,
    TELEMETRY_MAGIC,
    TELEMETRY_PAYLOAD_BYTES,
    TELEMETRY_PAYLOAD_FIELDS,
    TELEMETRY_RECORD_BYTES,
    TELEMETRY_VERSION,
    ModelConfig,
    RuntimeLayout,
    build_experiment_document,
    canonical_identity_bytes,
    configuration_id,
    configuration_sha256,
)
from envo_telemetry import (
    FRAME_LENGTH,
    FRAME_TYPE,
    MAGIC,
    PAYLOAD_LENGTH,
    PROTOCOL_VERSION,
    FrameRecord,
)


def test_experiment_document_round_trips_canonical_configuration() -> None:
    config = replace(
        ModelConfig(),
        seed=0x1234,
        food_count=64,
        prey_count=28,
        predator_count=8,
        telemetry_interval=4,
    )

    document_bytes = build_experiment_document(config, VERSION)
    document = json.loads(document_bytes)

    assert document_bytes.endswith(b"\n")
    assert document_bytes == build_experiment_document(config, VERSION)
    assert document["format_version"] == EXPERIMENT_FORMAT_VERSION
    assert document["model_abi_version"] == MODEL_ABI_VERSION
    assert document["project_version"] == VERSION
    assert ModelConfig.from_mapping(document) == config
    assert document["configuration"] == config.to_dict()
    assert document["config_id"] == configuration_id(config)
    assert document["config_id_hex"] == f"0x{configuration_id(config):04X}"
    assert document["config_sha256"] == configuration_sha256(config)
    assert document["config_sha256"] == hashlib.sha256(
        canonical_identity_bytes(config)
    ).hexdigest()


def test_every_mutable_model_input_changes_the_provenance_identity() -> None:
    config = ModelConfig()
    variants = {
        "seed": replace(config, seed=config.seed + 1),
        "food_count": replace(config, food_count=config.food_count - 1),
        "prey_count": replace(config, prey_count=config.prey_count - 1),
        "predator_count": replace(
            config,
            predator_count=config.predator_count - 1,
        ),
        "initial_energy": replace(
            config,
            initial_energy=config.initial_energy - 1,
        ),
        "prey_initial_speed": replace(config, prey_initial_speed=1),
        "prey_initial_sense": replace(
            config,
            prey_initial_sense=config.prey_initial_sense - 1,
        ),
        "predator_initial_speed": replace(config, predator_initial_speed=2),
        "predator_initial_sense": replace(
            config,
            predator_initial_sense=config.predator_initial_sense - 1,
        ),
        "reproduction_energy": replace(
            config,
            reproduction_energy=config.reproduction_energy - 1,
        ),
        "food_energy": replace(
            config,
            food_energy=config.food_energy - 1,
        ),
        "mutation_mask": replace(config, mutation_mask=7),
        "sense_min": replace(config, sense_min=config.sense_min - 1),
        "sense_max": replace(config, sense_max=config.sense_max - 1),
        "sense_mutation_step": replace(
            config,
            sense_mutation_step=config.sense_mutation_step - 1,
        ),
        "day_night_mask": replace(
            config,
            day_night_mask=config.day_night_mask << 1,
        ),
        "waterline": replace(config, waterline=config.waterline - 1),
        "telemetry_interval": replace(config, telemetry_interval=2),
    }
    base_sha = configuration_sha256(config)
    base_id = configuration_id(config)

    for name, variant in variants.items():
        assert configuration_sha256(variant) != base_sha, name
        assert configuration_id(variant) != base_id, name

    with pytest.raises(ValueError, match="speed bounds"):
        replace(config, speed_min=0)
    with pytest.raises(ValueError, match="speed bounds"):
        replace(config, speed_max=5)


@pytest.mark.parametrize(
    "config",
    [
        ModelConfig(),
        replace(
            ModelConfig(),
            food_count=1,
            prey_count=1,
            predator_count=1,
        ),
        replace(
            ModelConfig(),
            food_count=128,
            prey_count=32,
            predator_count=16,
        ),
    ],
)
def test_runtime_layout_regions_are_exact_and_non_overlapping(
    config: ModelConfig,
) -> None:
    layout = RuntimeLayout.from_config(config)

    assert layout.food_base == DATA_BASE
    assert layout.prey_base == (
        layout.food_base + config.food_count * FOOD_RECORD_BYTES
    )
    assert layout.predator_base == (
        layout.prey_base + config.prey_count * AGENT_RECORD_BYTES
    )
    assert layout.data_end == (
        layout.predator_base + config.predator_count * AGENT_RECORD_BYTES
    )
    assert layout.telemetry_buffer + TELEMETRY_RECORD_BYTES <= (
        layout.scratch_start
    )
    assert layout.scratch_start + 16 == layout.data_base
    assert layout.data_end < 0xA000
    assert layout.data_end % 2 == 0

    regions = (
        (
            "telemetry",
            layout.telemetry_buffer,
            layout.telemetry_buffer + TELEMETRY_RECORD_BYTES,
        ),
        ("scratch", layout.scratch_start, layout.data_base),
        ("food", layout.food_base, layout.prey_base),
        ("prey", layout.prey_base, layout.predator_base),
        ("predator", layout.predator_base, layout.data_end),
    )
    for left, right in zip(regions, regions[1:]):
        assert left[2] <= right[1], f"{left[0]} overlaps {right[0]}"


def test_guest_payload_contract_is_statically_inspectable() -> None:
    assert MAGIC == TELEMETRY_MAGIC
    assert PROTOCOL_VERSION == TELEMETRY_VERSION
    assert FRAME_TYPE == TELEMETRY_FRAME_TYPE
    assert PAYLOAD_LENGTH == TELEMETRY_PAYLOAD_BYTES
    assert FRAME_LENGTH == TELEMETRY_RECORD_BYTES
    assert tuple(field.name for field in fields(FrameRecord)) == (
        TELEMETRY_PAYLOAD_FIELDS
    )
    assert struct.calcsize("<9H4B2H") == TELEMETRY_PAYLOAD_BYTES
    assert (
        len(TELEMETRY_MAGIC)
        + 3
        + TELEMETRY_PAYLOAD_BYTES
        + 1
        == TELEMETRY_RECORD_BYTES
    )

    config = ModelConfig()
    layout = RuntimeLayout.from_config(config)
    assert config.speed_max - config.speed_min + 1 == 4
    assert (layout.data_end - DATA_BASE) % 2 == 0
    assert (
        layout.data_end - DATA_BASE
        == config.food_count * FOOD_RECORD_BYTES
        + (config.prey_count + config.predator_count) * AGENT_RECORD_BYTES
    )


def test_kernel_statically_embeds_the_v2_payload_header_and_config_id() -> None:
    config = replace(ModelConfig(), seed=0x1234, telemetry_interval=2)
    image = assemble_kernel_config(config)
    emit_start = image.symbols["emit_telemetry"] - 0x1000
    emit_end = image.symbols["rand"] - 0x1000
    emitter = image.code[emit_start:emit_end]

    header = (
        *TELEMETRY_MAGIC,
        TELEMETRY_VERSION,
        TELEMETRY_FRAME_TYPE,
        TELEMETRY_PAYLOAD_BYTES,
    )
    header_stores = b"".join(bytes((0xB0, value, 0xAA)) for value in header)
    config_store = (
        b"\xB8"
        + configuration_id(config).to_bytes(2, "little")
        + b"\xAB"
    )

    assert header_stores in emitter
    assert config_store in emitter
    assert b"\xB9\x20\x00" in emitter
    assert len(image.code) <= MAX_KERNEL_SECTORS * SECTOR_SIZE


def test_manifest_v2_describes_every_exact_experiment_artifact() -> None:
    config = replace(
        ModelConfig(),
        seed=0x1234,
        food_count=64,
        prey_count=28,
        predator_count=8,
        telemetry_interval=2,
    )
    artifacts = build_artifacts_config(config)
    manifest = json.loads(artifacts.manifest)
    experiment = json.loads(artifacts.experiment)
    artifact_bytes = {
        "boot.bin": artifacts.boot,
        "experiment.json": artifacts.experiment,
        "kernel.bin": artifacts.kernel,
        "floppy.img": artifacts.floppy,
        "os.iso": artifacts.iso,
    }

    assert manifest["format_version"] == 2
    assert manifest["model_abi_version"] == MODEL_ABI_VERSION
    assert manifest["version"] == VERSION
    assert manifest["seed"] == config.seed
    assert manifest["config_id"] == experiment["config_id"]
    assert manifest["config_id"] == configuration_id(config)
    assert manifest["config_sha256"] == experiment["config_sha256"]
    assert manifest["config_sha256"] == configuration_sha256(config)
    assert manifest["telemetry"] == {
        "interval_ticks": config.telemetry_interval,
        "record_bytes": TELEMETRY_RECORD_BYTES,
        "version": TELEMETRY_VERSION,
    }
    assert manifest["kernel_sectors"] == (
        len(artifacts.kernel) + SECTOR_SIZE - 1
    ) // SECTOR_SIZE
    assert set(manifest["artifacts"]) == set(artifact_bytes)

    for name, data in artifact_bytes.items():
        assert manifest["artifacts"][name] == {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    assert artifacts.experiment == build_experiment_document(config, VERSION)
    assert ModelConfig.from_mapping(experiment) == config
