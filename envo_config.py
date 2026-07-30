"""Canonical experiment configuration and memory layout for Envo Agent OS."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import Path
from typing import Any


MODEL_ABI_VERSION = 4
EXPERIMENT_FORMAT_VERSION = 1
DEFAULT_SEED = 0xACE1
DATA_BASE = 0x9000
FOOD_RECORD_BYTES = 6
AGENT_RECORD_BYTES = 12
TELEMETRY_BUFFER_BYTES = 96
TELEMETRY_RECORD_BYTES = 48
TELEMETRY_MAGIC = b"EV"
TELEMETRY_VERSION = 3
TELEMETRY_FRAME_TYPE = 1
TELEMETRY_PAYLOAD_BYTES = 42
TELEMETRY_PAYLOAD_FIELDS = (
    "config_id",
    "tick",
    "rng_state",
    "replacements",
    "captures",
    "starvations",
    "forager_turnovers",
    "prey_energy_sum",
    "prey_sense_sum",
    "speed_1_count",
    "speed_2_count",
    "speed_3_count",
    "speed_4_count",
    "max_generation",
    "state_checksum",
    "predator_replacements",
    "predator_starvations",
    "predator_forager_turnovers",
    "predator_energy_sum",
    "predator_sense_sum",
    "predator_speed_1_count",
    "predator_speed_2_count",
    "predator_speed_3_count",
    "predator_speed_4_count",
    "predator_max_generation",
)


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


@dataclass(frozen=True)
class ModelConfig:
    """All compile-time inputs that can affect a guest trajectory."""

    seed: int = DEFAULT_SEED
    food_count: int = 50
    prey_count: int = 30
    predator_count: int = 4
    initial_energy: int = 100
    prey_initial_speed: int = 2
    prey_initial_sense: int = 50
    predator_initial_speed: int = 3
    predator_initial_sense: int = 100
    reproduction_energy: int = 160
    food_energy: int = 20
    prey_base_metabolism: int = 1
    predator_base_metabolism: int = 1
    speed_metabolism: int = 1
    sense_metabolism_shift: int = 5
    predator_capture_energy: int = 40
    predator_reproduction_energy: int = 160
    mutation_mask: int = 3
    speed_min: int = 1
    speed_max: int = 4
    sense_min: int = 24
    sense_max: int = 120
    sense_mutation_step: int = 8
    day_night_mask: int = 0x0100
    waterline: int = 170
    telemetry_interval: int = 1

    def __post_init__(self) -> None:
        bounded_words = {
            "initial_energy": self.initial_energy,
            "prey_initial_sense": self.prey_initial_sense,
            "predator_initial_sense": self.predator_initial_sense,
            "reproduction_energy": self.reproduction_energy,
            "predator_reproduction_energy": self.predator_reproduction_energy,
            "sense_min": self.sense_min,
            "sense_max": self.sense_max,
            "day_night_mask": self.day_night_mask,
        }
        for name, value in bounded_words.items():
            if not 1 <= value <= 0x7FFF:
                raise ValueError(f"{name} must be in the range 1..32767")

        if not 1 <= self.seed <= 0xFFFF:
            raise ValueError("seed must be in the range 1..65535")
        if not 1 <= self.food_count <= 128:
            raise ValueError("food_count must be in the range 1..128")
        if not 1 <= self.prey_count <= 32:
            raise ValueError("prey_count must be in the range 1..32")
        if not 1 <= self.predator_count <= 16:
            raise ValueError("predator_count must be in the range 1..16")
        if self.speed_min != 1 or self.speed_max != 4:
            raise ValueError("the v3 telemetry ABI requires speed bounds 1..4")
        if not self.speed_min <= self.prey_initial_speed <= self.speed_max:
            raise ValueError("prey_initial_speed is outside the speed bounds")
        if not self.speed_min <= self.predator_initial_speed <= self.speed_max:
            raise ValueError("predator_initial_speed is outside the speed bounds")
        if not self.sense_min <= self.prey_initial_sense <= self.sense_max:
            raise ValueError("prey_initial_sense is outside the sense bounds")
        if not self.sense_min <= self.predator_initial_sense <= self.sense_max:
            raise ValueError("predator_initial_sense is outside the sense bounds")
        if not 1 <= self.food_energy <= 0x7FFF:
            raise ValueError("food_energy must be in the range 1..32767")
        for name, value in (
            ("prey_base_metabolism", self.prey_base_metabolism),
            ("predator_base_metabolism", self.predator_base_metabolism),
            ("speed_metabolism", self.speed_metabolism),
        ):
            if not 1 <= value <= 0x7F:
                raise ValueError(f"{name} must be in the range 1..127")
        if not 1 <= self.sense_metabolism_shift <= 15:
            raise ValueError("sense_metabolism_shift must be in the range 1..15")
        if not 1 <= self.predator_capture_energy <= 0x7FFF:
            raise ValueError(
                "predator_capture_energy must be in the range 1..32767"
            )
        if self.initial_energy >= self.reproduction_energy:
            raise ValueError("initial_energy must be below reproduction_energy")
        if self.initial_energy >= self.predator_reproduction_energy:
            raise ValueError(
                "initial_energy must be below predator_reproduction_energy"
            )
        if self.prey_base_metabolism >= self.initial_energy:
            raise ValueError(
                "prey_base_metabolism must be below initial_energy"
            )
        if self.predator_base_metabolism >= self.initial_energy:
            raise ValueError(
                "predator_base_metabolism must be below initial_energy"
            )
        if self.reproduction_energy + self.food_energy > 0x7FFF:
            raise ValueError(
                "reproduction_energy plus food_energy must not exceed 32767"
            )
        if (
            self.predator_reproduction_energy
            + self.predator_capture_energy
            > 0x7FFF
        ):
            raise ValueError(
                "predator_reproduction_energy plus "
                "predator_capture_energy must not exceed 32767"
            )
        if self.reproduction_energy * self.prey_count > 0xFFFF:
            raise ValueError("prey energy sum does not fit the telemetry ABI")
        if (
            self.predator_reproduction_energy * self.predator_count
            > 0xFFFF
        ):
            raise ValueError(
                "predator energy sum does not fit the telemetry ABI"
            )
        if self.sense_max * self.prey_count > 0xFFFF:
            raise ValueError("prey sense sum does not fit the telemetry ABI")
        if self.sense_max * self.predator_count > 0xFFFF:
            raise ValueError("predator sense sum does not fit the telemetry ABI")
        maximum_trait_cost = (
            (self.speed_max - self.speed_min) * self.speed_metabolism
            + (
                (self.sense_max - self.sense_min)
                >> self.sense_metabolism_shift
            )
        )
        if (
            self.prey_base_metabolism + maximum_trait_cost > 0x7FFF
            or self.predator_base_metabolism + maximum_trait_cost > 0x7FFF
        ):
            raise ValueError("maximum trait-dependent metabolism exceeds 32767")
        if not 2 <= self.waterline <= 196:
            raise ValueError("waterline must be in the range 2..196")
        if not _is_power_of_two(self.day_night_mask):
            raise ValueError("day_night_mask must contain exactly one bit")
        if not _is_power_of_two(self.telemetry_interval):
            raise ValueError("telemetry_interval must be a power of two")
        if not 1 <= self.telemetry_interval <= 0x8000:
            raise ValueError("telemetry_interval must be in the range 1..32768")
        if self.mutation_mask not in {1, 3, 7, 15, 31, 63, 127, 255}:
            raise ValueError("mutation_mask must be one less than a power of two")
        if not 1 <= self.sense_mutation_step <= 0x7F:
            raise ValueError("sense_mutation_step must be in the range 1..127")
        if self.sense_max + self.sense_mutation_step > 0x7FFF:
            raise ValueError(
                "sense_max plus sense_mutation_step must not exceed 32767"
            )

        RuntimeLayout.from_config(self)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ModelConfig":
        """Load a strict configuration mapping or generated experiment document."""

        if "configuration" in value:
            value = value["configuration"]
        if not isinstance(value, dict):
            raise ValueError("configuration must be a JSON object")

        names = {field.name for field in fields(cls)}
        unknown = sorted(set(value) - names)
        missing = sorted(names - set(value))
        if unknown:
            raise ValueError(
                "unknown configuration field(s): " + ", ".join(unknown)
            )
        if missing:
            raise ValueError(
                "missing configuration field(s): " + ", ".join(missing)
            )
        if any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in value.values()
        ):
            raise ValueError("all configuration fields must be JSON integers")
        return cls(**value)


@dataclass(frozen=True)
class RuntimeLayout:
    """Addresses and record sizes that form the model's guest-memory ABI."""

    data_base: int
    telemetry_buffer: int
    scratch_start: int
    food_base: int
    prey_base: int
    predator_base: int
    data_end: int
    food_record_bytes: int = FOOD_RECORD_BYTES
    agent_record_bytes: int = AGENT_RECORD_BYTES
    telemetry_record_bytes: int = TELEMETRY_RECORD_BYTES

    @classmethod
    def from_config(cls, config: ModelConfig) -> "RuntimeLayout":
        telemetry_buffer = DATA_BASE - TELEMETRY_BUFFER_BYTES
        scratch_start = DATA_BASE - 22
        food_base = DATA_BASE
        prey_base = food_base + config.food_count * FOOD_RECORD_BYTES
        predator_base = prey_base + config.prey_count * AGENT_RECORD_BYTES
        data_end = predator_base + config.predator_count * AGENT_RECORD_BYTES

        if telemetry_buffer + TELEMETRY_RECORD_BYTES > scratch_start:
            raise ValueError("telemetry buffer overlaps runtime scratch words")
        if data_end >= 0xA000:
            raise ValueError("entity storage overlaps VGA memory")

        return cls(
            data_base=DATA_BASE,
            telemetry_buffer=telemetry_buffer,
            scratch_start=scratch_start,
            food_base=food_base,
            prey_base=prey_base,
            predator_base=predator_base,
            data_end=data_end,
        )

    def to_dict(self) -> dict[str, int | str]:
        values: dict[str, int | str] = asdict(self)
        for key in (
            "data_base",
            "telemetry_buffer",
            "scratch_start",
            "food_base",
            "prey_base",
            "predator_base",
            "data_end",
        ):
            values[f"{key}_hex"] = f"0x{values[key]:04X}"
        return values


def canonical_identity(config: ModelConfig) -> dict[str, Any]:
    """Return the exact model/runtime identity used for hashing."""

    layout = RuntimeLayout.from_config(config)
    return {
        "model_abi_version": MODEL_ABI_VERSION,
        "configuration": config.to_dict(),
        "runtime_layout": layout.to_dict(),
        "telemetry": {
            "magic": TELEMETRY_MAGIC.decode("ascii"),
            "version": TELEMETRY_VERSION,
            "frame_type": TELEMETRY_FRAME_TYPE,
            "payload_bytes": TELEMETRY_PAYLOAD_BYTES,
            "payload_encoding": (
                "9 little-endian words, 4 bytes, 7 words, 4 bytes, "
                "then 1 word"
            ),
            "payload_fields": list(TELEMETRY_PAYLOAD_FIELDS),
            "record_bytes": TELEMETRY_RECORD_BYTES,
            "checksum": "8-bit two's-complement sum over version through payload",
        },
    }


def canonical_identity_bytes(config: ModelConfig) -> bytes:
    return json.dumps(
        canonical_identity(config),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def configuration_sha256(config: ModelConfig) -> str:
    return hashlib.sha256(canonical_identity_bytes(config)).hexdigest()


def configuration_id(config: ModelConfig) -> int:
    """Return the little-endian 16-bit trace identity prefix."""

    digest = bytes.fromhex(configuration_sha256(config))
    return int.from_bytes(digest[:2], "little")


def build_experiment_document(config: ModelConfig, project_version: str) -> bytes:
    identity = canonical_identity(config)
    document = {
        "format_version": EXPERIMENT_FORMAT_VERSION,
        "project": "Envo Agent OS",
        "project_version": project_version,
        "config_id": configuration_id(config),
        "config_id_hex": f"0x{configuration_id(config):04X}",
        "config_sha256": configuration_sha256(config),
        **identity,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_experiment_document(document: object) -> ModelConfig:
    """Validate a generated experiment document against canonical identity."""

    if not isinstance(document, dict):
        raise ValueError("experiment document must be a JSON object")
    if "configuration" not in document:
        raise ValueError("experiment document is missing configuration")

    config = ModelConfig.from_mapping(document)
    expected = {
        "format_version": EXPERIMENT_FORMAT_VERSION,
        "project": "Envo Agent OS",
        "config_id": configuration_id(config),
        "config_id_hex": f"0x{configuration_id(config):04X}",
        "config_sha256": configuration_sha256(config),
        **canonical_identity(config),
    }
    for key, expected_value in expected.items():
        if key not in document:
            raise ValueError(f"experiment document is missing {key}")
        if document[key] != expected_value:
            raise ValueError(
                f"experiment document {key} does not match canonical identity"
            )

    project_version = document.get("project_version")
    if not isinstance(project_version, str) or not project_version:
        raise ValueError(
            "experiment document project_version must be a non-empty string"
        )
    return config


def load_model_config(path: Path) -> ModelConfig:
    """Read a strict model configuration from JSON."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    if "configuration" in document:
        try:
            return validate_experiment_document(document)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return ModelConfig.from_mapping(document)
