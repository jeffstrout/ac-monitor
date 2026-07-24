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


CFG = cfgmod.from_dict({})


def test_sensor_fault_on_unhealthy_channel():
    r = _readings(25, 12, health={"input_air": True, "output_air": False, "fan": True})
    d = derive.compute(r, CFG)
    assert d.faults["sensor_fault"] is True


def test_no_airflow_when_fan_idle():
    d = derive.compute(_readings(25, 12, fan=False), CFG)
    assert d.faults["no_airflow"] is True


def test_cooling_in_band_no_fault():
    # ΔT = 25-12 = 13 °C = 23.4 °F ... just outside 15-22 -> abnormal. Use 22 -> 9 F? pick in-band:
    # input 25, output 15 -> ΔT 10 C = 18 F, inside 15-22 cooling band.
    d = derive.compute(_readings(25, 15), CFG)
    assert d.mode == "cooling"
    assert d.faults["abnormal_delta_t"] is False


def test_cooling_out_of_band_fault():
    # input 25, output 24 -> ΔT 1 C = 1.8 F, below cooling_min 15 -> abnormal.
    d = derive.compute(_readings(25, 24), CFG)
    assert d.mode == "cooling"
    assert d.faults["abnormal_delta_t"] is True


def test_heating_mode_detected():
    # output warmer than input -> negative ΔT -> heating.
    d = derive.compute(_readings(20, 40), CFG)  # ΔT = -20 C = -36 F -> |36| in 25-70 band
    assert d.mode == "heating"
    assert d.faults["abnormal_delta_t"] is False


def test_no_abnormal_when_fan_off():
    d = derive.compute(_readings(25, 24, fan=False), CFG)
    assert d.faults["abnormal_delta_t"] is False   # not evaluated when air isn't moving
    assert d.faults["no_airflow"] is True


def test_any_fault_property():
    d = derive.compute(_readings(25, 15), CFG)
    assert d.any_fault is False
