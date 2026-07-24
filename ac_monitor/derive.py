"""Derived metrics and fault logic from a HAT :class:`Readings` snapshot.

Pure and stateless so it's easy to test. Fault thresholds come from config.
Air-side ΔT is the headline number; faults are named booleans the dashboard and
MQTT expose. See docs/software-design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .hat import Readings

FAULT_NAMES = ("sensor_fault", "no_airflow", "abnormal_delta_t")


@dataclass
class Derived:
    delta_t: float | None = None          # display unit, input - output
    mode: str | None = None               # "cooling" | "heating" | None
    faults: dict[str, bool] = field(default_factory=lambda: {n: False for n in FAULT_NAMES})

    @property
    def any_fault(self) -> bool:
        return any(self.faults.values())


def compute(readings: Readings, cfg: Config) -> Derived:
    d = Derived(delta_t=readings.delta_t)
    faults = {n: False for n in FAULT_NAMES}

    # Any configured channel failing to read is a sensor fault.
    faults["sensor_fault"] = (not readings.health) or (not all(readings.health.values()))

    # Airflow proof: fan idle (sail switch open) => no airflow.
    faults["no_airflow"] = readings.fan_running is False

    # Abnormal ΔT — only meaningful when air is moving and both air probes read.
    # Mode is inferred from the sign of ΔT (supply colder than return = cooling).
    ic = readings.temps_c.get("input_air")
    oc = readings.temps_c.get("output_air")
    if ic is not None and oc is not None and readings.fan_running:
        delta_f = (ic - oc) * 9.0 / 5.0          # compare in °F against the _f thresholds
        th = cfg.thresholds.delta_t
        if delta_f >= 0:
            d.mode = "cooling"
            faults["abnormal_delta_t"] = not (th.cooling_min_f <= delta_f <= th.cooling_max_f)
        else:
            d.mode = "heating"
            faults["abnormal_delta_t"] = not (th.heating_min_f <= -delta_f <= th.heating_max_f)

    d.faults = faults
    return d
