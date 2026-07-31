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


# Default deployment: no Home Assistant, airflow sensor fitted (OPTO-5).
CFG = cfgmod.from_dict({})
CFG_AIRFLOW = cfgmod.from_dict({"airflow": {"enabled": True}})   # explicit, same thing

# A unit with no airflow sensor — the checks that depend on one must degrade,
# not fault.
CFG_NO_AIRFLOW = cfgmod.from_dict({"airflow": {"enabled": False}})

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


def test_no_airflow_fault_is_gated_on_having_a_sensor():
    """A unit with no airflow sensor fitted must degrade, not fault."""
    d = derive.compute(_readings(25, 15, fan=False), CFG_NO_AIRFLOW)
    assert d.faults["no_airflow"] is False

    d = derive.compute(_readings(25, 15, fan=False), CFG_AIRFLOW)
    assert d.faults["no_airflow"] is True


def test_ha_unavailable_not_raised_when_ha_is_simply_not_configured():
    assert derive.compute(_readings(25, 15), CFG).faults["ha_unavailable"] is False


# --- running, but not delivering (issue #64) ---------------------------------
#
# Two checks that need BOTH authoritative demand and sensed airflow. Both are
# timed from the start of the call: equipment gets its spin-up and warm-up before
# being accused of failing.

GRACE = 61.0        # past airflow.prove_after_s (60)
DEVELOPED = 121.0   # past thresholds.delta_t.develop_after_s (120)


def test_airflow_mismatch_when_a_call_is_active_and_the_blower_is_not_turning():
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="cooling", demand_for_s=GRACE
    )
    assert d.faults["airflow_mismatch"] is True
    assert d.system_status == "Error"


def test_airflow_mismatch_holds_off_during_blower_spin_up():
    """A furnace delays the blower 30-90 s; faulting at t=0 would fire every cycle."""
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="heating", demand_for_s=5.0
    )
    assert d.faults["airflow_mismatch"] is False
    assert d.system_status == "Heating"


def test_airflow_mismatch_fires_on_fan_only_call():
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="fan", demand_for_s=GRACE
    )
    assert d.faults["airflow_mismatch"] is True


def test_airflow_mismatch_fires_when_the_fan_is_set_to_run_continuously():
    """hvac_action alone would miss a dead blower in the mode people leave the
    thermostat in for circulation — the gap docs/ha-mode-source.md called out."""
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="idle", fan_mode="on",
        demand_for_s=GRACE,
    )
    assert d.faults["airflow_mismatch"] is True


def test_airflow_mismatch_quiet_when_idle_and_fan_on_auto():
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="idle", fan_mode="auto",
        demand_for_s=GRACE,
    )
    assert d.faults["airflow_mismatch"] is False
    assert d.system_status == "Idle"


def test_airflow_mismatch_quiet_when_the_blower_is_turning():
    d = derive.compute(
        _readings(25, 15, fan=True), CFG_HA, demand="cooling", demand_for_s=GRACE
    )
    assert d.faults["airflow_mismatch"] is False


def test_airflow_mismatch_is_not_raised_by_a_failed_opto_read():
    """fan_running None is a sensor problem, not an HVAC one."""
    r = _readings(25, 24, fan=None, health={"input_air": True, "output_air": True, "fan": False})
    d = derive.compute(r, CFG_HA, demand="cooling", demand_for_s=GRACE)

    assert d.faults["airflow_mismatch"] is False
    assert d.faults["sensor_fault"] is True


def test_airflow_mismatch_needs_a_sensor():
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_NO_AIRFLOW, demand="cooling", demand_for_s=GRACE
    )
    assert d.faults["airflow_mismatch"] is False


def test_delta_t_not_developing_on_a_cooling_call_that_delivers_nothing():
    # input 25 °C, output 22 °C -> ΔT +5.4 °F, short of the +10 floor.
    d = derive.compute(_readings(25, 22), CFG_HA, demand="cooling", demand_for_s=DEVELOPED)

    assert d.faults["delta_t_not_developing"] is True
    assert d.system_status == "Error"
    # The mode source is untouched — the thermostat still says what it says.
    assert d.mode == "cooling"
    assert d.mode_source == "home_assistant"


def test_delta_t_not_developing_on_a_heating_call_that_delivers_nothing():
    # output 5 °C warmer than input -> ΔT -9 °F, short of the -20 ceiling.
    d = derive.compute(_readings(20, 25), CFG_HA, demand="heating", demand_for_s=DEVELOPED)
    assert d.faults["delta_t_not_developing"] is True


def test_delta_t_developed_is_quiet():
    # cooling: 25 -> 17 °C is ΔT +14.4 °F, past +10.
    d = derive.compute(_readings(25, 17), CFG_HA, demand="cooling", demand_for_s=DEVELOPED)
    assert d.faults["delta_t_not_developing"] is False
    assert d.system_status == "Cooling"

    # heating: output 15 °C warmer -> ΔT -27 °F, past -20.
    d = derive.compute(_readings(20, 35), CFG_HA, demand="heating", demand_for_s=DEVELOPED)
    assert d.faults["delta_t_not_developing"] is False


def test_delta_t_gets_the_full_window_before_it_is_judged():
    """ΔT starts at zero on every cycle; the check exists to wait it out."""
    d = derive.compute(_readings(25, 25), CFG_HA, demand="cooling", demand_for_s=30.0)
    assert d.faults["delta_t_not_developing"] is False
    assert d.system_status == "Cooling"


def test_delta_t_not_developing_skipped_when_the_call_cannot_be_timed():
    """No clock means no timed check — never a guess."""
    d = derive.compute(_readings(25, 25), CFG_HA, demand="cooling", demand_for_s=None)
    assert d.faults["delta_t_not_developing"] is False
    assert d.faults["airflow_mismatch"] is False


def test_neither_check_fires_without_authoritative_demand():
    """HA unreachable: "no call" and "a call we cannot see" must not look alike."""
    d = derive.compute(_readings(25, 25, fan=False), CFG_HA, demand=None, demand_for_s=DEVELOPED)

    assert d.faults["airflow_mismatch"] is False
    assert d.faults["delta_t_not_developing"] is False
    assert d.system_status == "Unknown"


def test_delta_t_not_developing_ignores_idle_and_fan_calls():
    for action in ("idle", "off", "fan"):
        d = derive.compute(_readings(25, 25), CFG_HA, demand=action, demand_for_s=DEVELOPED)
        assert d.faults["delta_t_not_developing"] is False, action


def test_error_status_does_not_disturb_the_reported_mode():
    """The escalation is the headline only — /api/state's HA block is unchanged."""
    d = derive.compute(
        _readings(25, 24, fan=False), CFG_HA, demand="cooling", demand_for_s=DEVELOPED
    )
    assert d.system_status == "Error"
    assert d.mode == "cooling"
    assert d.mode_source == "home_assistant"
