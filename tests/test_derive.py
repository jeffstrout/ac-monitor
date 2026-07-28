"""Tests for ac_monitor.derive — fault logic from readings + thresholds."""

from ac_monitor import config as cfgmod
from ac_monitor import derive
from ac_monitor.hat import Readings


def _readings(input_c, output_c, fan=True, health=None):
    r = Readings(unit="F")
    r.temps_c = {"input_air": input_c, "output_air": output_c}
    r.temps = {"input_air": None, "output_air": None}
    r.fan_running = fan
    r.health = health or {"input_air": True, "output_air": True, "fan": True}
    r.delta_t = None if (input_c is None or output_c is None) else (input_c - output_c) * 9 / 5
    return r


# Default deployment: no Home Assistant, no airflow sensor.
CFG = cfgmod.from_dict({})

# The sail switch is removed from the live unit, but the logic is retained behind
# airflow.enabled. These tests keep exercising it so restoring the sensor is a
# config flag plus wiring rather than a rewrite.
CFG_AIRFLOW = cfgmod.from_dict({"airflow": {"enabled": True}})

# Home Assistant configured — demand is authoritative.
CFG_HA = cfgmod.from_dict(
    {"homeassistant": {"enabled": True, "token": "t", "base_url": "http://ha"}}
)


def test_sensor_fault_on_unhealthy_channel():
    r = _readings(25, 12, health={"input_air": True, "output_air": False, "fan": True})
    d = derive.compute(r, CFG)
    assert d.faults["sensor_fault"] is True


def test_no_airflow_when_fan_idle():
    d = derive.compute(_readings(25, 12, fan=False), CFG_AIRFLOW)
    assert d.faults["no_airflow"] is True


def test_cooling_in_band_no_fault():
    # ΔT = 25-12 = 13 °C = 23.4 °F ... just outside 15-22 -> abnormal. Use 22 -> 9 F? pick in-band:
    # input 25, output 15 -> ΔT 10 C = 18 F, inside 15-22 cooling band.
    d = derive.compute(_readings(25, 15), CFG)
    assert d.mode == "cooling"
    assert d.faults["abnormal_delta_t"] is False


def test_cooling_out_of_band_fault():
    # input 25, output 24 -> ΔT 1 C = 1.8 F, below cooling_min 15 -> abnormal.
    d = derive.compute(_readings(25, 24), CFG_AIRFLOW)
    assert d.mode == "cooling"
    assert d.faults["abnormal_delta_t"] is True


def test_heating_mode_detected():
    # output warmer than input -> negative ΔT -> heating.
    d = derive.compute(_readings(20, 40), CFG)  # ΔT = -20 C = -36 F -> |36| in 25-70 band
    assert d.mode == "heating"
    assert d.faults["abnormal_delta_t"] is False


def test_no_abnormal_when_fan_off():
    d = derive.compute(_readings(25, 24, fan=False), CFG_AIRFLOW)
    assert d.faults["abnormal_delta_t"] is False   # not evaluated when air isn't moving
    assert d.faults["no_airflow"] is True


def test_any_fault_property():
    d = derive.compute(_readings(25, 15), CFG)
    assert d.any_fault is False


# --- system status (fan + ΔT deadband) ---------------------------------------

def test_status_idle_when_fan_off():
    assert derive.compute(_readings(25, 15, fan=False), CFG_AIRFLOW).system_status == "Idle"


def test_status_idle_when_fan_unknown():
    assert derive.compute(_readings(25, 15, fan=None), CFG_AIRFLOW).system_status == "Idle"


def test_status_cooling_above_deadband():
    # input 25, output 15 -> ΔT +18 °F (> +5) with fan on -> Cooling.
    assert derive.compute(_readings(25, 15), CFG).system_status == "Cooling"


def test_status_heating_below_negative_deadband():
    # output warmer -> ΔT -36 °F (< -5) -> Heating.
    assert derive.compute(_readings(20, 40), CFG).system_status == "Heating"


def test_status_fan_within_deadband():
    # input 20, output 19 -> ΔT +1.8 °F, within ±5 -> Fan (moving air, no heat/cool).
    assert derive.compute(_readings(20, 19), CFG_AIRFLOW).system_status == "Fan"


def test_status_fan_when_air_probes_missing_but_fan_on():
    r = _readings(None, None, fan=True, health={"suction_line": True, "fan": True})
    assert derive.compute(r, CFG_AIRFLOW).system_status == "Fan"


# --- authoritative demand from Home Assistant (docs/ha-mode-source.md) -------

def test_demand_decides_the_band_not_the_sign_of_delta_t():
    """THE regression this change exists to prevent.

    Thermostat calls for cooling; the air is going the wrong way (supply warmer
    than return). The old logic inferred mode from ΔT's sign, reclassified this
    as heating, checked it against the heating band, and passed. With
    authoritative demand it is evaluated as cooling — and fails.
    """
    r = _readings(15, 25)          # input 15 °C, output 25 °C -> ΔT = -18 °F
    d = derive.compute(r, CFG_HA, demand="cooling")

    assert d.mode == "cooling"                      # not reclassified
    assert d.faults["abnormal_delta_t"] is True     # would have been False before


def test_wrong_direction_fires_when_air_opposes_the_call():
    d = derive.compute(_readings(15, 25), CFG_HA, demand="cooling")
    assert d.faults["wrong_direction"] is True

    d = derive.compute(_readings(25, 15), CFG_HA, demand="heating")
    assert d.faults["wrong_direction"] is True


def test_wrong_direction_suppressed_during_changeover():
    """A heat pump takes time to reverse; don't cry fault mid-changeover."""
    d = derive.compute(_readings(15, 25), CFG_HA, demand="cooling", demand_settled=False)
    assert d.faults["wrong_direction"] is False


def test_wrong_direction_quiet_when_air_matches_the_call():
    d = derive.compute(_readings(25, 15), CFG_HA, demand="cooling")
    assert d.faults["wrong_direction"] is False


def test_idle_demand_reports_idle_and_skips_delta_t_bands():
    """Observed live: state=cool, hvac_action=idle on a satisfied house."""
    d = derive.compute(_readings(25, 24), CFG_HA, demand="idle")

    assert d.system_status == "Idle"
    assert d.mode is None
    assert d.faults["abnormal_delta_t"] is False
    assert d.faults["wrong_direction"] is False


def test_off_is_distinguishable_from_idle():
    assert derive.compute(_readings(25, 24), CFG_HA, demand="off").system_status == "Off"


def test_fan_only_demand():
    d = derive.compute(_readings(25, 24), CFG_HA, demand="fan")
    assert d.system_status == "Fan"
    assert d.mode is None


def test_ha_configured_but_unavailable_says_unknown_rather_than_guessing():
    """With the sail switch gone there is not enough local signal to infer."""
    d = derive.compute(_readings(15, 25), CFG_HA, demand=None)

    assert d.system_status == "Unknown"
    assert d.mode_source == "unavailable"
    assert d.faults["ha_unavailable"] is True
    # No band is picked on a guess.
    assert d.faults["abnormal_delta_t"] is False
    assert d.faults["wrong_direction"] is False


def test_mode_source_marks_facts_apart_from_guesses():
    assert derive.compute(_readings(25, 15), CFG_HA, demand="cooling").mode_source == "home_assistant"
    assert derive.compute(_readings(25, 15), CFG).mode_source == "inferred"
    assert derive.compute(_readings(25, 15), CFG_HA).mode_source == "unavailable"


def test_no_airflow_fault_is_off_while_the_sail_switch_is_removed():
    d = derive.compute(_readings(25, 15, fan=False), CFG)
    assert d.faults["no_airflow"] is False

    d = derive.compute(_readings(25, 15, fan=False), CFG_AIRFLOW)
    assert d.faults["no_airflow"] is True


def test_ha_unavailable_not_raised_when_ha_is_simply_not_configured():
    assert derive.compute(_readings(25, 15), CFG).faults["ha_unavailable"] is False
