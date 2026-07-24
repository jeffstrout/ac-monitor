"""Shared in-memory application state: latest readings + derived metrics.

The poller writes it; the web layer reads it. ``snapshot()`` is the JSON the
dashboard, /api/state, and (later) MQTT publish.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .config import Config
from .debounce import Debouncer
from .derive import Derived
from .hat import Readings


@dataclass
class AppState:
    config: Config
    readings: Readings | None = None
    derived: Derived | None = None
    last_poll_at: float | None = None
    poll_count: int = 0
    consecutive_errors: int = 0
    fan_debouncer: Debouncer | None = None

    def __post_init__(self) -> None:
        if self.fan_debouncer is None:
            self.fan_debouncer = Debouncer(self.config.poll.fan_debounce_s)

    def update(self, readings: Readings, derived: Derived, now: float) -> None:
        self.readings = readings
        self.derived = derived
        self.last_poll_at = now
        self.poll_count += 1
        self.consecutive_errors = 0 if readings.i2c_ok else self.consecutive_errors + 1

    def snapshot(self) -> dict:
        r, d = self.readings, self.derived
        return {
            "unit": self.config.temperature_unit,
            "temps": r.temps if r else {},
            "temps_c": r.temps_c if r else {},
            "volts": r.volts if r else {},
            "fan_running": r.fan_running if r else None,
            "delta_t": (d.delta_t if d else None),
            "mode": (d.mode if d else None),
            "faults": (d.faults if d else {}),
            "health": r.health if r else {},
            "i2c_ok": r.i2c_ok if r else False,
            "toggles": {
                "display_push": self.config.display.enabled,
                "mqtt": self.config.mqtt.enabled,
            },
            "last_poll_at": self.last_poll_at,
            "poll_count": self.poll_count,
            "consecutive_errors": self.consecutive_errors,
        }
