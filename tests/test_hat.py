"""Tests for ac_monitor.hat — pure conversion + read aggregation with a mock backend."""

import math

import pytest

from ac_monitor import config as cfgmod
from ac_monitor import hat
from ac_monitor.hat import HatError, Readings


def test_volts_to_resistance_midscale():
    # 15k pull-up to 3.3V, thermistor to GND: 1.32V -> 10k.
    assert hat.volts_to_resistance(1.32, 15000, 3.3) == pytest.approx(10000, rel=1e-3)


def test_resistance_to_celsius_at_nominal():
    # At the nominal resistance the Beta equation returns exactly 25 °C.
    assert hat.resistance_to_celsius(10000, 3950, 10000) == pytest.approx(25.0, abs=1e-6)


def test_open_circuit_raises():
    with pytest.raises(HatError):
        hat.volts_to_resistance(3.311, 15000, 3.3)   # > vref => open input
    with pytest.raises(HatError):
        hat.volts_to_resistance(0.0, 15000, 3.3)


def test_volts_to_celsius_applies_calibration():
    therm = cfgmod.Thermistors()  # default gain 1.024, offset -1.20
    # 1.32V -> 25 °C raw -> 1.024*25 - 1.20 = 24.4
    assert hat.volts_to_celsius(1.32, therm, "input_air") == pytest.approx(24.4, abs=0.05)


def test_delta_conversion_has_no_offset():
    # A 10 °C difference is 18 °F (no +32).
    assert hat.delta_to_unit(10.0, "F") == pytest.approx(18.0)
    assert hat.c_to_unit(0.0, "F") == pytest.approx(32.0)


class MockBackend:
    """Returns preset volts per ADC channel and a preset opto value."""

    def __init__(self, adc: dict[int, float], opto: dict[int, int]):
        self.adc = adc
        self.opto = opto

    def read_adc(self, stack, channel):
        if channel not in self.adc:
            raise HatError(f"AD{channel} not detected")
        return self.adc[channel]

    def read_opto(self, stack, channel):
        if channel not in self.opto:
            raise HatError("not detected")
        return self.opto[channel]


def _cfg():
    # As-wired: AD1 output, AD2 input, AD3 suction, AD4 liquid; OPTO-5 fan.
    return cfgmod.from_dict({"units": {"temperature": "F"}})


def test_read_all_happy_path():
    # input (AD2) warmer than output (AD1) -> positive cooling ΔT.
    backend = MockBackend(
        adc={1: 1.60, 2: 1.20, 3: 1.30, 4: 1.10},  # arbitrary but in-range
        opto={5: 1},
    )
    r = hat.read_all(_cfg(), backend)
    assert all(r.health[role] for role in ("output_air", "input_air", "suction_line", "liquid_line"))
    assert r.fan_running is True
    assert r.i2c_ok is True
    # ΔT is reported in the display unit (°F here) = the °C difference × 9/5 (no offset).
    diff_c = r.temps_c["input_air"] - r.temps_c["output_air"]
    assert r.delta_t == pytest.approx(diff_c * 9 / 5, abs=0.15)


def test_read_all_marks_bad_channel_unhealthy():
    backend = MockBackend(adc={1: 1.6, 2: 1.2, 3: 1.3}, opto={5: 0})  # AD4 missing
    r = hat.read_all(_cfg(), backend)
    assert r.health["liquid_line"] is False
    assert r.temps["liquid_line"] is None
    assert r.health["suction_line"] is True   # others still fine
    assert r.fan_running is False


def test_read_all_open_channel_unhealthy():
    backend = MockBackend(adc={1: 3.311, 2: 1.2, 3: 1.3, 4: 1.1}, opto={5: 1})  # AD1 open
    r = hat.read_all(_cfg(), backend)
    assert r.health["output_air"] is False
    # ΔT needs output_air, so it should be absent.
    assert r.delta_t is None


def test_fan_active_low_inverts():
    cfg = cfgmod.from_dict({"sensors": {"digital": {"fan": {"opto_channel": 5, "active_high": False}}}})
    backend = MockBackend(adc={1: 1.6, 2: 1.2, 3: 1.3, 4: 1.1}, opto={5: 0})
    r = hat.read_all(cfg, backend)
    assert r.fan_running is True   # active_low: open contact => running


def test_i2c_all_down():
    backend = MockBackend(adc={}, opto={})
    r = hat.read_all(_cfg(), backend)
    assert r.i2c_ok is False
    assert r.delta_t is None
