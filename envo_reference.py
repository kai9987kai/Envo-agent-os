"""Exact pure-Python reference model for the Envo Agent OS v3 guest."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import struct
import sys
from typing import Sequence

from envo_config import ModelConfig, configuration_id, load_model_config
from envo_telemetry import FrameRecord, encode_frame


WORD_MASK = 0xFFFF
SIGN_BIT = 0x8000


def _word(value: int) -> int:
    return value & WORD_MASK


def _signed_word(value: int) -> int:
    value &= WORD_MASK
    return value - 0x10000 if value & SIGN_BIT else value


def _signed_less(left: int, right: int) -> bool:
    return _signed_word(left) < _signed_word(right)


def _signed_greater(left: int, right: int) -> bool:
    return _signed_word(left) > _signed_word(right)


def _absolute_word_difference(left: int, right: int) -> int:
    difference = _word(left - right)
    if _signed_word(difference) < 0:
        difference = _word(-difference)
    return difference


def xorshift16(value: int) -> int:
    """Apply the exact three-shift 16-bit PRNG transition used by the guest."""

    _validate_word("value", value)
    value ^= _word(value << 7)
    value ^= value >> 9
    value ^= _word(value << 8)
    return _word(value)


def _validate_word(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= WORD_MASK:
        raise ValueError(f"{name} must be in the range 0..65535")


@dataclass(slots=True)
class FoodRecord:
    """One six-byte food record in guest-memory order."""

    x: int
    y: int
    padding: int = 0

    def to_bytes(self) -> bytes:
        return struct.pack("<3H", self.x, self.y, self.padding)


@dataclass(slots=True)
class AgentRecord:
    """One twelve-byte prey or predator record in guest-memory order."""

    x: int
    y: int
    energy: int
    speed: int
    sense: int
    generation: int

    def to_bytes(self) -> bytes:
        return struct.pack(
            "<6H",
            self.x,
            self.y,
            self.energy,
            self.speed,
            self.sense,
            self.generation,
        )


class ReferenceModel:
    """Instruction-order-equivalent host implementation of the guest model."""

    def __init__(self, config: ModelConfig) -> None:
        if not isinstance(config, ModelConfig):
            raise TypeError("config must be a ModelConfig")
        self.config = config
        self.config_id = configuration_id(config)
        self.tick = 0
        self.rng_state = config.seed
        self.replacements = 0
        self.captures = 0
        self.starvations = 0
        self.forager_turnovers = 0
        self.predator_replacements = 0
        self.predator_starvations = 0
        self.predator_forager_turnovers = 0
        self.food: list[FoodRecord] = []
        self.prey: list[AgentRecord] = []
        self.predators: list[AgentRecord] = []
        self._initialize_entities()

    def _next_random(self) -> int:
        self.rng_state = xorshift16(self.rng_state)
        return self.rng_state

    def _random_x(self) -> int:
        return (self._next_random() & 0x00FF) + 32

    def _random_y(self) -> int:
        return (self._next_random() & 0x007F) + 24

    def _initialize_entities(self) -> None:
        config = self.config
        for _ in range(config.food_count):
            self.food.append(FoodRecord(self._random_x(), self._random_y()))
        for _ in range(config.prey_count):
            self.prey.append(
                AgentRecord(
                    self._random_x(),
                    self._random_y(),
                    config.initial_energy,
                    config.prey_initial_speed,
                    config.prey_initial_sense,
                    0,
                )
            )
        for _ in range(config.predator_count):
            self.predators.append(
                AgentRecord(
                    self._random_x(),
                    self._random_y(),
                    config.initial_energy,
                    config.predator_initial_speed,
                    config.predator_initial_sense,
                    0,
                )
            )

    def step(self) -> FrameRecord | None:
        """Advance one completed guest tick and emit telemetry when configured."""

        self.tick = _word(self.tick + 1)
        night = bool(self.tick & self.config.day_night_mask)

        for prey_index in range(len(self.prey)):
            self._update_prey(prey_index, night=night)
        for predator_index in range(len(self.predators)):
            self._update_predator(predator_index)

        if self.tick & (self.config.telemetry_interval - 1):
            return None
        return self.frame_record()

    def run(self, ticks: int) -> list[FrameRecord]:
        """Advance multiple ticks and return every telemetry frame emitted."""

        if isinstance(ticks, bool) or not isinstance(ticks, int):
            raise TypeError("ticks must be an integer")
        if ticks < 0:
            raise ValueError("ticks must not be negative")
        frames: list[FrameRecord] = []
        for _ in range(ticks):
            frame = self.step()
            if frame is not None:
                frames.append(frame)
        return frames

    def _update_prey(self, prey_index: int, *, night: bool) -> None:
        prey = self.prey[prey_index]
        config = self.config

        if night:
            if _signed_less(prey.energy, config.reproduction_energy):
                prey.energy = _word(prey.energy + 1)
            return

        metabolic_cost = self._metabolic_cost(
            prey,
            base=config.prey_base_metabolism,
        )
        if prey.energy <= metabolic_cost:
            self.starvations = _word(self.starvations + 1)
            self._rebirth_from_random_donor(prey)
            return
        prey.energy = _word(prey.energy - metabolic_cost)

        visible_predator = self._first_visible_predator(prey)
        if visible_predator is None:
            self._wander(prey)
        else:
            self._flee(prey, visible_predator)
        self._clamp(prey)

        if not _signed_less(prey.y, config.waterline):
            prey.y = _word(prey.y - 1)

        for food in self.food:
            if not self._collides(prey, food):
                continue
            food.x = self._random_x()
            food.y = self._random_y()
            prey.energy = _word(prey.energy + config.food_energy)
            if not _signed_less(prey.energy, config.reproduction_energy):
                self.forager_turnovers = _word(self.forager_turnovers + 1)
                self._rebirth_from_self(prey)
            return

    def _first_visible_predator(
        self,
        prey: AgentRecord,
    ) -> AgentRecord | None:
        for predator in self.predators:
            distance = _word(
                _absolute_word_difference(prey.x, predator.x)
                + _absolute_word_difference(prey.y, predator.y)
            )
            if not _signed_greater(distance, prey.sense):
                return predator
        return None

    def _flee(self, prey: AgentRecord, predator: AgentRecord) -> None:
        x_difference = _word(prey.x - predator.x)
        if _signed_word(x_difference) >= 0:
            prey.x = _word(prey.x + prey.speed)
        else:
            prey.x = _word(prey.x - prey.speed)

        y_difference = _word(prey.y - predator.y)
        if _signed_word(y_difference) >= 0:
            prey.y = _word(prey.y + prey.speed)
        else:
            prey.y = _word(prey.y - prey.speed)

    def _wander(self, agent: AgentRecord) -> None:
        if self._next_random() & 1:
            agent.x = _word(agent.x + agent.speed)
        else:
            agent.x = _word(agent.x - agent.speed)
        if self._next_random() & 1:
            agent.y = _word(agent.y + agent.speed)
        else:
            agent.y = _word(agent.y - agent.speed)

    @staticmethod
    def _clamp(agent: AgentRecord) -> None:
        if _signed_less(agent.x, 2):
            agent.x = 2
        elif _signed_greater(agent.x, 316):
            agent.x = 316

        if _signed_less(agent.y, 2):
            agent.y = 2
        elif _signed_greater(agent.y, 196):
            agent.y = 196

    @staticmethod
    def _collides(
        left: AgentRecord,
        right: AgentRecord | FoodRecord,
    ) -> bool:
        x_distance = _absolute_word_difference(left.x, right.x)
        if _signed_greater(x_distance, 4):
            return False
        y_distance = _absolute_word_difference(left.y, right.y)
        return not _signed_greater(y_distance, 4)

    def _rebirth_from_random_donor(self, destination: AgentRecord) -> None:
        destination.x = self._random_x()
        destination.y = self._random_y()
        destination.energy = self.config.initial_energy

        parent_mask = (1 << (self.config.prey_count - 1).bit_length()) - 1
        while True:
            parent_index = self._next_random() & parent_mask
            if parent_index < self.config.prey_count:
                break
        self._inherit(destination, self.prey[parent_index], predator=False)

    def _rebirth_from_self(self, destination: AgentRecord) -> None:
        destination.x = self._random_x()
        destination.y = self._random_y()
        destination.energy = self.config.initial_energy
        self._inherit(destination, destination, predator=False)

    def _rebirth_predator_from_random_donor(
        self,
        destination: AgentRecord,
    ) -> None:
        destination.x = self._random_x()
        destination.y = self._random_y()
        destination.energy = self.config.initial_energy

        parent_mask = (
            1 << (self.config.predator_count - 1).bit_length()
        ) - 1
        while True:
            parent_index = self._next_random() & parent_mask
            if parent_index < self.config.predator_count:
                break
        self._inherit(
            destination,
            self.predators[parent_index],
            predator=True,
        )

    def _rebirth_predator_from_self(
        self,
        destination: AgentRecord,
    ) -> None:
        destination.x = self._random_x()
        destination.y = self._random_y()
        destination.energy = self.config.initial_energy
        self._inherit(destination, destination, predator=True)

    def _inherit(
        self,
        destination: AgentRecord,
        parent: AgentRecord,
        *,
        predator: bool,
    ) -> None:
        config = self.config
        speed = parent.speed
        if self._next_random() & config.mutation_mask == 0:
            if self._next_random() & 1:
                speed = min(_word(speed + 1), config.speed_max)
            else:
                speed = max(_word(speed - 1), config.speed_min)
        destination.speed = _word(speed)

        sense = parent.sense
        if self._next_random() & config.mutation_mask == 0:
            if self._next_random() & 1:
                sense = min(
                    _word(sense + config.sense_mutation_step),
                    config.sense_max,
                )
            else:
                sense = _word(sense - config.sense_mutation_step)
                if _signed_less(sense, config.sense_min):
                    sense = config.sense_min
        destination.sense = _word(sense)
        destination.generation = _word(parent.generation + 1)
        if predator:
            self.predator_replacements = _word(
                self.predator_replacements + 1
            )
        else:
            self.replacements = _word(self.replacements + 1)

    def _metabolic_cost(self, agent: AgentRecord, *, base: int) -> int:
        config = self.config
        return (
            base
            + (agent.speed - config.speed_min) * config.speed_metabolism
            + (
                (agent.sense - config.sense_min)
                >> config.sense_metabolism_shift
            )
        )

    def _update_predator(self, predator_index: int) -> None:
        predator = self.predators[predator_index]
        metabolic_cost = self._metabolic_cost(
            predator,
            base=self.config.predator_base_metabolism,
        )
        if predator.energy <= metabolic_cost:
            self.predator_starvations = _word(
                self.predator_starvations + 1
            )
            self._rebirth_predator_from_random_donor(predator)
            return
        predator.energy = _word(predator.energy - metabolic_cost)

        target = self._closest_prey(predator)

        if target is not None:
            self._chase(predator, target)
        else:
            self._wander(predator)
        self._clamp(predator)

        for prey in self.prey:
            if not self._collides(predator, prey):
                continue
            self._rebirth_from_random_donor(prey)
            self.captures = _word(self.captures + 1)
            predator.energy = _word(
                predator.energy + self.config.predator_capture_energy
            )
            if not _signed_less(
                predator.energy,
                self.config.predator_reproduction_energy,
            ):
                self.predator_forager_turnovers = _word(
                    self.predator_forager_turnovers + 1
                )
                self._rebirth_predator_from_self(predator)
            return

    def _closest_prey(self, predator: AgentRecord) -> AgentRecord | None:
        minimum_distance = 0x7FFF
        target: AgentRecord | None = None
        for prey in self.prey:
            distance = _word(
                _absolute_word_difference(predator.x, prey.x)
                + _absolute_word_difference(predator.y, prey.y)
            )
            if _signed_word(distance) >= _signed_word(minimum_distance):
                continue
            minimum_distance = distance
            target = prey

        if target is None or _signed_greater(
            minimum_distance,
            predator.sense,
        ):
            return None
        return target

    @staticmethod
    def _chase(predator: AgentRecord, target: AgentRecord) -> None:
        x_difference = _word(predator.x - target.x)
        if x_difference:
            if _signed_word(x_difference) > 0:
                predator.x = _word(predator.x - predator.speed)
            else:
                predator.x = _word(predator.x + predator.speed)

        y_difference = _word(predator.y - target.y)
        if y_difference:
            if _signed_word(y_difference) > 0:
                predator.y = _word(predator.y - predator.speed)
            else:
                predator.y = _word(predator.y + predator.speed)

    def entity_region_bytes(self) -> bytes:
        """Serialize food, prey, and predator memory exactly as the guest does."""

        return b"".join(
            (
                *(item.to_bytes() for item in self.food),
                *(item.to_bytes() for item in self.prey),
                *(item.to_bytes() for item in self.predators),
            )
        )

    def state_checksum(self) -> int:
        """Return the guest's wrapping sum of entity-region 16-bit words."""

        state = self.entity_region_bytes()
        words = struct.unpack(f"<{len(state) // 2}H", state)
        return sum(words) & WORD_MASK

    def frame_record(self) -> FrameRecord:
        """Build telemetry metrics from the current completed model state."""

        energy_sum = 0
        sense_sum = 0
        speed_counts = [0, 0, 0, 0]
        maximum_generation = 0
        for prey in self.prey:
            energy_sum = _word(energy_sum + prey.energy)
            sense_sum = _word(sense_sum + prey.sense)
            speed_index = _word(prey.speed - 1)
            speed_counts[speed_index] = _word(
                speed_counts[speed_index] + 1
            ) & 0xFF
            if prey.generation > maximum_generation:
                maximum_generation = prey.generation

        predator_energy_sum = 0
        predator_sense_sum = 0
        predator_speed_counts = [0, 0, 0, 0]
        predator_maximum_generation = 0
        for predator in self.predators:
            predator_energy_sum = _word(
                predator_energy_sum + predator.energy
            )
            predator_sense_sum = _word(
                predator_sense_sum + predator.sense
            )
            speed_index = _word(predator.speed - 1)
            predator_speed_counts[speed_index] = _word(
                predator_speed_counts[speed_index] + 1
            ) & 0xFF
            if predator.generation > predator_maximum_generation:
                predator_maximum_generation = predator.generation

        return FrameRecord(
            config_id=self.config_id,
            tick=self.tick,
            rng_state=self.rng_state,
            replacements=self.replacements,
            captures=self.captures,
            starvations=self.starvations,
            forager_turnovers=self.forager_turnovers,
            prey_energy_sum=energy_sum,
            prey_sense_sum=sense_sum,
            speed_1_count=speed_counts[0],
            speed_2_count=speed_counts[1],
            speed_3_count=speed_counts[2],
            speed_4_count=speed_counts[3],
            max_generation=maximum_generation,
            state_checksum=self.state_checksum(),
            predator_replacements=self.predator_replacements,
            predator_starvations=self.predator_starvations,
            predator_forager_turnovers=self.predator_forager_turnovers,
            predator_energy_sum=predator_energy_sum,
            predator_sense_sum=predator_sense_sum,
            predator_speed_1_count=predator_speed_counts[0],
            predator_speed_2_count=predator_speed_counts[1],
            predator_speed_3_count=predator_speed_counts[2],
            predator_speed_4_count=predator_speed_counts[3],
            predator_max_generation=predator_maximum_generation,
        )


def _parse_integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "value must be a decimal or 0x-prefixed integer"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the exact Envo Agent OS Python reference model.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="model configuration or generated experiment JSON",
    )
    parser.add_argument(
        "--seed",
        type=_parse_integer,
        help="override the configured 16-bit seed",
    )
    parser.add_argument(
        "--telemetry-interval",
        type=_parse_integer,
        help="override the configured telemetry interval",
    )
    parser.add_argument(
        "--ticks",
        type=_parse_integer,
        default=10,
        help="number of model ticks to run (default: 10)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Emit one JSON object per telemetry frame from a reference run."""

    args = _build_parser().parse_args(argv)
    try:
        config = (
            load_model_config(args.config)
            if args.config is not None
            else ModelConfig()
        )
        overrides = {}
        if args.seed is not None:
            overrides["seed"] = args.seed
        if args.telemetry_interval is not None:
            overrides["telemetry_interval"] = args.telemetry_interval
        if overrides:
            config = replace(config, **overrides)

        model = ReferenceModel(config)
        for frame in model.run(args.ticks):
            item = {**asdict(frame), "packet_hex": encode_frame(frame).hex()}
            print(json.dumps(item, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
