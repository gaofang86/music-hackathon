"""Hardware-independent timing, musical semantics, and transport state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Iterable


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, float(value)))


class InteractionMode(str, Enum):
    BEGINNER = "beginner"
    ASSISTED = "assisted"
    EXPERT = "expert"


class TransportState(str, Enum):
    WAITING = "WAITING"
    READY = "READY"
    ARMED = "ARMED"
    ACTIVE = "ACTIVE"
    HOLD = "HOLD"
    STOP_QUEUED = "STOP_QUEUED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass(frozen=True)
class MusicalIntent:
    energy: float = 0.5
    follow: float = 0.5
    pulse: float = 0.5
    adventure: float = 0.35
    style_commitment: float = 0.4
    style_index: int = 0
    section_index: int = 0

    def updated(self, **changes) -> "MusicalIntent":
        normalized = {}
        for name, value in changes.items():
            if name in {
                "energy",
                "follow",
                "pulse",
                "adventure",
                "style_commitment",
            }:
                normalized[name] = clamp(value)
            elif name in {"style_index", "section_index"}:
                normalized[name] = max(0, int(value))
        return replace(self, **normalized)


@dataclass(frozen=True)
class Mrt2Parameters:
    temperature: float
    top_k: int
    cfg_musiccoca: float
    cfg_notes: float
    cfg_drums: float
    style_index: int
    section_index: int


def intent_to_mrt2(intent: MusicalIntent) -> Mrt2Parameters:
    """Map musical language to conservative MRT2 performance ranges."""
    energy_curve = intent.energy**1.35
    adventure = intent.adventure
    temperature = 0.8 + 0.45 * energy_curve + 0.3 * adventure
    top_k = round(28 + 55 * energy_curve + 57 * adventure)
    cfg_drums = 1.0 + 3.5 * intent.pulse
    cfg_notes = 0.8 + 3.4 * intent.follow
    cfg_musiccoca = 0.8 + 3.2 * intent.style_commitment
    return Mrt2Parameters(
        temperature=min(1.55, temperature),
        top_k=min(140, max(24, top_k)),
        cfg_musiccoca=cfg_musiccoca,
        cfg_notes=cfg_notes,
        cfg_drums=cfg_drums,
        style_index=intent.style_index,
        section_index=intent.section_index,
    )


class NodDetector:
    """Detect a downward-and-return head movement from normalized nose Y."""

    def __init__(self, window: int = 10, minimum_magnitude: float = 0.015):
        self.minimum_magnitude = minimum_magnitude
        self._positions: deque[float] = deque(maxlen=window)
        self._peak_y: float | None = None
        self._start_y: float | None = None

    def update(self, y: float) -> tuple[bool, float]:
        self._positions.append(y)
        if len(self._positions) < 3:
            return False, 0.0
        positions = list(self._positions)
        previous_velocity = positions[-2] - positions[-3]
        current_velocity = positions[-1] - positions[-2]
        if previous_velocity > 0:
            if self._start_y is None:
                self._start_y = positions[-3]
            if self._peak_y is None or positions[-2] > self._peak_y:
                self._peak_y = positions[-2]
        if (
            previous_velocity > 0
            and current_velocity <= 0
            and self._peak_y is not None
            and self._start_y is not None
        ):
            magnitude = self._peak_y - self._start_y
            self._peak_y = None
            self._start_y = None
            if magnitude >= self.minimum_magnitude:
                return True, magnitude
        if previous_velocity < 0 and current_velocity < 0:
            self._peak_y = None
            self._start_y = None
        return False, 0.0


class WeightedTempoSmoother:
    def __init__(
        self,
        window: int = 8,
        smoothing: float = 0.65,
        minimum_bpm: float = 40.0,
        maximum_bpm: float = 240.0,
        default_bpm: float = 120.0,
    ):
        if window < 1:
            raise ValueError("window must be at least 1")
        if not 0.0 <= smoothing <= 1.0:
            raise ValueError("smoothing must be between 0 and 1")
        self.window = window
        self.smoothing = smoothing
        self.minimum_bpm = minimum_bpm
        self.maximum_bpm = maximum_bpm
        self.default_bpm = default_bpm
        self._intervals: deque[float] = deque(maxlen=window)
        self._last_beat: float | None = None
        self._bpm = default_bpm
        self._has_estimate = False

    @property
    def bpm(self) -> float:
        return self._bpm

    def reset(self) -> None:
        self._intervals.clear()
        self._last_beat = None
        self._bpm = self.default_bpm
        self._has_estimate = False

    def add_beat(self, timestamp: float) -> float:
        if self._last_beat is not None:
            interval = timestamp - self._last_beat
            minimum = 60.0 / self.maximum_bpm
            maximum = 60.0 / self.minimum_bpm
            if minimum <= interval <= maximum:
                self._intervals.append(interval)
                target = 60.0 / self._weighted_mean(self._intervals)
                target = min(self.maximum_bpm, max(self.minimum_bpm, target))
                if not self._has_estimate:
                    self._bpm = target
                    self._has_estimate = True
                else:
                    self._bpm = (
                        self.smoothing * self._bpm
                        + (1.0 - self.smoothing) * target
                    )
        self._last_beat = timestamp
        return self._bpm

    @staticmethod
    def _weighted_mean(values: Iterable[float]) -> float:
        values = list(values)
        weights = range(1, len(values) + 1)
        return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


class BarClock:
    def __init__(self, beats_per_bar: int = 4):
        self.beats_per_bar = beats_per_bar
        self.origin: float | None = None
        self.bpm = 120.0

    @property
    def bar_duration(self) -> float:
        return self.beats_per_bar * 60.0 / self.bpm

    def start(self, origin: float, bpm: float) -> None:
        self.origin = origin
        self.bpm = bpm

    def update_bpm(self, now: float, bpm: float) -> None:
        if self.origin is None:
            self.bpm = bpm
            return
        phase = self.beat_position(now)
        self.bpm = bpm
        self.origin = now - phase * 60.0 / bpm

    def beat_position(self, now: float) -> float:
        if self.origin is None:
            return 0.0
        return max(0.0, (now - self.origin) * self.bpm / 60.0)

    def beat_number(self, now: float) -> int:
        return int(math.floor(self.beat_position(now))) % self.beats_per_bar + 1

    def beats_until(self, now: float, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, (deadline - now) * self.bpm / 60.0)

    def next_bar(self, now: float) -> float:
        if self.origin is None:
            raise RuntimeError("bar clock has not started")
        target_bar = math.floor(
            self.beat_position(now) / self.beats_per_bar
        ) + 1
        return self.origin + target_bar * self.bar_duration


@dataclass(frozen=True)
class EnsembleState:
    bpm: float = 120.0
    transport: TransportState = TransportState.WAITING
    mode: InteractionMode = InteractionMode.BEGINNER
    intent: MusicalIntent = MusicalIntent()
    scheduled_at: float | None = None
    tracking_ok: bool = True


class EnsembleController:
    def __init__(
        self,
        nods_to_start: int = 5,
        smoothing: float = 0.65,
        default_bpm: float = 120.0,
        mode: InteractionMode = InteractionMode.BEGINNER,
    ):
        if nods_to_start < 2:
            raise ValueError("nods_to_start must be at least 2")
        self.nods_to_start = nods_to_start
        self.tempo = WeightedTempoSmoother(
            smoothing=smoothing,
            default_bpm=default_bpm,
        )
        self.clock = BarClock(4)
        self.nod_count = 0
        self.state = EnsembleState(bpm=default_bpm, mode=mode)

    def record_nod(self, timestamp: float) -> EnsembleState:
        self.nod_count += 1
        bpm = self.tempo.add_beat(timestamp)
        if self.state.transport == TransportState.WAITING:
            if self.nod_count >= self.nods_to_start:
                self.clock.start(timestamp, bpm)
                self.state = replace(
                    self.state,
                    bpm=bpm,
                    transport=TransportState.READY,
                )
            else:
                self.state = replace(self.state, bpm=bpm)
        else:
            self.clock.update_bpm(timestamp, bpm)
            self.state = replace(self.state, bpm=bpm)
        return self.state

    def set_mode(self, mode: InteractionMode | str) -> EnsembleState:
        self.state = replace(self.state, mode=InteractionMode(mode))
        return self.state

    def update_intent(self, **changes) -> EnsembleState:
        if self.state.transport == TransportState.HOLD:
            return self.state
        self.state = replace(self.state, intent=self.state.intent.updated(**changes))
        return self.state

    def request_start(self, timestamp: float) -> EnsembleState:
        if self.state.transport != TransportState.READY:
            return self.state
        self.state = replace(
            self.state,
            transport=TransportState.ARMED,
            scheduled_at=self.clock.next_bar(timestamp),
        )
        return self.state

    def request_hold(self) -> EnsembleState:
        if self.state.transport == TransportState.ACTIVE:
            self.state = replace(self.state, transport=TransportState.HOLD)
        return self.state

    def request_resume(self) -> EnsembleState:
        if self.state.transport == TransportState.HOLD:
            self.state = replace(self.state, transport=TransportState.ACTIVE)
        return self.state

    def request_stop(self, timestamp: float) -> EnsembleState:
        if self.state.transport not in {
            TransportState.ACTIVE,
            TransportState.HOLD,
        }:
            return self.state
        self.state = replace(
            self.state,
            transport=TransportState.STOP_QUEUED,
            scheduled_at=self.clock.next_bar(timestamp),
        )
        return self.state

    def begin_stop_fade(self, timestamp: float, duration: float) -> EnsembleState:
        if self.state.transport == TransportState.STOP_QUEUED:
            self.state = replace(
                self.state,
                scheduled_at=timestamp + duration,
            )
        return self.state

    def emergency_stop(self) -> EnsembleState:
        self.state = replace(
            self.state,
            transport=TransportState.EMERGENCY_STOP,
            scheduled_at=None,
        )
        return self.state

    def set_tracking(self, ok: bool) -> EnsembleState:
        if not ok and self.state.transport == TransportState.ACTIVE:
            self.state = replace(
                self.state,
                tracking_ok=False,
                transport=TransportState.HOLD,
            )
        else:
            self.state = replace(self.state, tracking_ok=ok)
        return self.state

    def tick(self, timestamp: float) -> tuple[EnsembleState, str | None]:
        if self.state.scheduled_at is None or timestamp < self.state.scheduled_at:
            return self.state, None
        if self.state.transport == TransportState.ARMED:
            self.state = replace(
                self.state,
                transport=TransportState.ACTIVE,
                scheduled_at=None,
            )
            return self.state, "start"
        if self.state.transport == TransportState.STOP_QUEUED:
            self.state = replace(
                self.state,
                transport=TransportState.READY,
                scheduled_at=None,
            )
            return self.state, "stop"
        return self.state, None

    def reset(self) -> EnsembleState:
        mode = self.state.mode
        self.tempo.reset()
        self.clock = BarClock(4)
        self.nod_count = 0
        self.state = EnsembleState(bpm=self.tempo.default_bpm, mode=mode)
        return self.state
