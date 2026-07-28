"""Derived metrics and fault logic from a HAT :class:`Readings` snapshot.

Pure and stateless so it's easy to test. Fault thresholds come from config.
Air-side ΔT is the headline number; faults are named booleans the dashboard and
MQTT expose. See docs/software-design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .hat import Readings

FAULT_NAMES = (
    "sensor_fault",
    "no_airflow",        # needs the sail switch — disabled while it is removed
    "abnormal_delta_t",
    "wrong_direction",   # only detectable with authoritative demand
    "ha_unavailable",
)

# hvac_action -> system_status. Duplicated from ha.ACTION_TO_STATUS deliberately:
# derive stays pure and importing ha here would drag urllib into it.
_ACTION_STATUS = {
    "heating": "Heating",
    "cooling": "Cooling",
    "fan": "Fan",
    "idle": "Idle",
    "off": "Off",
}


@dataclass
class Derived:
    delta_t: float | None = None          # display unit, input - output
    mode: str | None = None               # "cooling" | "heating" | None
    system_status: str = "Idle"           # Cooling|Heating|Fan|Idle|Off|Unknown
    # Where system_status came from, so the panel can say so rather than
    # presenting a guess and a fact identically.
    mode_source: str = "inferred"         # home_assistant | inferred | unavailable
    faults: dict[str, bool] = field(default_factory=lambda: {n: False for n in FAULT_NAMES})

    @property
    def any_fault(self) -> bool:
        return any(self.faults.values())


def compute(
    readings: Readings,
    cfg: Config,
    demand: str | None = None,
    demand_settled: bool = True,
) -> Derived:
    """Derive metrics and faults. Pure and stateless — that is why it is testable.

    ``demand`` is the thermostat's own ``hvac_action`` from Home Assistant, or
    None when we cannot vouch for it. It is authoritative: it decides the mode
    and therefore which ΔT band applies. Previously the mode was inferred from
    the sign of ΔT and then ΔT was checked against that mode's band — a circle
    in which warm air during a cooling call was reclassified as heating and
    passed. See docs/ha-mode-source.md.

    ``demand_settled`` is False during a changeover, so a heat pump reversing
    does not trip ``wrong_direction`` while the valve is still moving.
    """
    d = Derived(delta_t=readings.delta_t)
    faults = {n: False for n in FAULT_NAMES}

    # Any configured channel failing to read is a sensor fault.
    faults["sensor_fault"] = (not readings.health) or (not all(readings.health.values()))

    # Airflow proof needs the sail switch, which is currently removed. Gated
    # rather than deleted so restoring it is a config flag plus wiring.
    if cfg.airflow.enabled:
        faults["no_airflow"] = readings.fan_running is False

    ic = readings.temps_c.get("input_air")
    oc = readings.temps_c.get("output_air")
    th = cfg.thresholds.delta_t
    delta_f = None
    if ic is not None and oc is not None:
        delta_f = (ic - oc) * 9.0 / 5.0          # compare in °F against the _f thresholds

    have_demand = demand in _ACTION_STATUS

    if have_demand:
        d.mode_source = "home_assistant"
        d.system_status = _ACTION_STATUS[demand]
        d.mode = demand if demand in ("cooling", "heating") else None

    elif cfg.homeassistant.enabled:
        # Configured but unreachable. With the sail switch gone there is not
        # enough local signal to infer honestly: ΔT ~ 0 means both "off" and
        # "blower running with no call". Say so rather than guessing — a
        # confident wrong answer is worse than an admitted gap.
        d.mode_source = "unavailable"
        d.system_status = "Unknown"
        faults["ha_unavailable"] = True

    else:
        # Legacy inference, for a deployment with no Home Assistant configured.
        d.mode_source = "inferred"
        moving = readings.fan_running if cfg.airflow.enabled else None
        if delta_f is not None:
            d.mode = "cooling" if delta_f >= 0 else "heating"
        if cfg.airflow.enabled and not moving:
            d.system_status = "Idle"
            d.mode = None
        elif delta_f is not None and delta_f > th.status_deadband_f:
            d.system_status = "Cooling"
        elif delta_f is not None and delta_f < -th.status_deadband_f:
            d.system_status = "Heating"
        elif cfg.airflow.enabled:
            d.system_status = "Fan"
        else:
            d.system_status = "Idle"
            d.mode = None

    # ΔT bands. Gated on an actual call for heat or cool: ΔT is meaningless
    # otherwise, and with HA unavailable we deliberately do not evaluate it at
    # all rather than pick a band on a guess.
    if delta_f is not None and d.mode in ("cooling", "heating") and d.mode_source != "unavailable":
        if d.mode == "cooling":
            faults["abnormal_delta_t"] = not (th.cooling_min_f <= delta_f <= th.cooling_max_f)
        else:
            faults["abnormal_delta_t"] = not (th.heating_min_f <= -delta_f <= th.heating_max_f)

    # The fault the circular logic could never raise: the thermostat calls for
    # one direction and the air is going the other way.
    if have_demand and demand_settled and delta_f is not None:
        if demand == "cooling" and delta_f < -th.status_deadband_f:
            faults["wrong_direction"] = True
        elif demand == "heating" and delta_f > th.status_deadband_f:
            faults["wrong_direction"] = True

    d.faults = faults
    return d
