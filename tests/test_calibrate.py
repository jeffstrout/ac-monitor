"""Tests for ac_monitor.calibrate — raw temperature + two-point fit."""

import pytest

from ac_monitor import calibrate
from ac_monitor import config as cfgmod


def test_raw_celsius_midscale():
    # 1.32 V with the default 15k/3.3V divider -> ~25 °C uncalibrated.
    assert calibrate.raw_celsius(1.32, cfgmod.Thermistors()) == pytest.approx(25.0, abs=0.1)


def test_fit_recovers_line():
    # true = 2*raw + 1  =>  points (known=true, raw): (1,0) and (21,10).
    cal = calibrate.fit([(1.0, 0.0), (21.0, 10.0)])
    assert cal.gain == pytest.approx(2.0)
    assert cal.offset == pytest.approx(1.0)


def test_fit_least_squares_three_points():
    cal = calibrate.fit([(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)])
    assert cal.gain == pytest.approx(1.0)
    assert cal.offset == pytest.approx(0.0)


def test_fit_needs_two_points():
    with pytest.raises(ValueError):
        calibrate.fit([(0.0, 0.0)])


def test_fit_same_raw_rejected():
    with pytest.raises(ValueError):
        calibrate.fit([(0.0, 5.0), (99.0, 5.0)])   # same raw temp -> undetermined
