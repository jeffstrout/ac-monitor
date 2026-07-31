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
    "no_airflow",             # airflow sensor reads idle (gated on airflow.enabled)
    "airflow_mismatch",       # airflow demanded, blower not turning
    "abnormal_delta_t",
    "delta_t_not_developing",  # running long enough to condition, and it isn't
    "wrong_direction",        # only detectable with authoritative demand
    "ha_unavailable",
)

# Faults that mean "the equipment is running and failing to do its job" — as
# opposed to a sensor problem or a degraded data source. These are the ones that
# take over system_status, because an operator glancing at the panel must not see
# a confident "Cooling" on a system that is cooling nothing.
ERROR_FAULTS = ("airflow_mismatch", "delta_t_not_developing")

# hvac_action values that should have the blower turning. Not just heating and
# cooling: with fan_mode "on" the blower runs continuously with no call at all,
# and checking only hvac_action would miss a dead blower in exactly the mode
# people leave thermostats in for circulation (docs/ha-mode-source.md).
AIRFLOW_ACTIONS = ("heating", "cooling", "fan")

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
    system_status: str = "Idle"           # Cooling|Heating|Fan|Idle|Off|Unknown|Error
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
    fan_mode: str | None = None,
    demand_for_s: float | None = None,
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

    ``fan_mode`` is HA's separate fan setting ("on" runs the blower with no call).

    ``demand_for_s`` is how long the current action has been in effect, from
    :meth:`ha.HaSource.demand_for`. The two "running but not delivering" checks
    need it — equipment is allowed a spin-up and a warm-up before either one
    accuses it of failing. None means we cannot time the call, and a timed check
    that cannot be timed is skipped rather than guessed.
    """
    d = Derived(delta_t=readings.delta_t)
    faults = {n: False for n in FAULT_NAMES}

    # Any configured channel failing to read is a sensor fault.
    faults["sensor_fault"] = (not readings.health) or (not all(readings.health.values()))

    # Airflow proof from the OPTO-5 pressure switch, gated so a unit with no
    # airflow sensor fitted degrades instead of crying fault.
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

    # --- running, but not delivering -----------------------------------------
    # Two checks that only exist because demand is authoritative AND airflow is
    # sensed. Both are timed from the start of the call, so equipment gets its
    # spin-up and warm-up before being accused of failing; both go quiet when we
    # cannot vouch for the demand, because "no call" and "call we can't see" must
    # not look the same.

    # Airflow demanded and the blower isn't turning. `is False` deliberately —
    # a failed opto read is None, and that is a sensor_fault, not an HVAC fault.
    if cfg.airflow.enabled and have_demand and demand_for_s is not None:
        airflow_demanded = demand in AIRFLOW_ACTIONS or fan_mode == "on"
        if (
            airflow_demanded
            and readings.fan_running is False
            and demand_for_s >= cfg.airflow.prove_after_s
        ):
            faults["airflow_mismatch"] = True

    # Called for heat or cool, running long enough to have done something, and ΔT
    # has not developed. The failure a healthy-looking system hides in: thermostat
    # calling, blower turning, every probe reading — and the coil accomplishing
    # nothing (low charge, blocked coil, stuck reversing valve).
    if (
        have_demand
        and demand in ("cooling", "heating")
        and delta_f is not None
        and demand_for_s is not None
        and demand_for_s >= th.develop_after_s
    ):
        if demand == "cooling":
            faults["delta_t_not_developing"] = delta_f < th.cooling_develop_f
        else:
            faults["delta_t_not_developing"] = delta_f > th.heating_develop_f

    # The fault the circular logic could never raise: the thermostat calls for
    # one direction and the air is going the other way.
    if have_demand and demand_settled and delta_f is not None:
        if demand == "cooling" and delta_f < -th.status_deadband_f:
            faults["wrong_direction"] = True
        elif demand == "heating" and delta_f > th.status_deadband_f:
            faults["wrong_direction"] = True

    # A system that is running and not delivering must not report the operating
    # mode as if all were well. The mode itself is untouched — `mode`,
    # `mode_source` and the thermostat's own action still say exactly what they
    # said; only the headline status escalates.
    if any(faults[n] for n in ERROR_FAULTS):
        d.system_status = "Error"

    d.faults = faults
    return d
