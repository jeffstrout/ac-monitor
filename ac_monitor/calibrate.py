"""Thermistor calibration helpers for the control panel's capture flow.

The user holds a probe at a known temperature (ice = 0 °C, boiling = altitude-
adjusted, e.g. ~99.4 °C in Tyler TX) and captures the reading. Each capture
records the *uncalibrated* Beta temperature for that channel; two or more
captures fit the two-point linear correction ``true_C = gain * raw_C + offset``
that the reader then applies.
"""

from __future__ import annotations

from .config import Calibration, Thermistors
from .hat import resistance_to_celsius, volts_to_resistance


def raw_celsius(volts: float, therm: Thermistors) -> float:
    """Uncalibrated Beta temperature (°C) for a channel voltage — the value the
    calibration corrects."""
    r = volts_to_resistance(volts, therm.r_pullup, therm.vref)
    return resistance_to_celsius(r, therm.beta, therm.r_nominal)


def fit(points: list[tuple[float, float]]) -> Calibration:
    """Least-squares fit of ``true_C = gain * raw_C + offset`` from capture
    points ``(known_c, raw_c)``. Needs ≥2 points at distinct raw temperatures.
    With exactly two points this is the two-point solution."""
    if len(points) < 2:
        raise ValueError("need at least two capture points")
    xs = [raw for _known, raw in points]   # x = raw (uncalibrated) °C
    ys = [known for known, _raw in points]  # y = true °C
    n = len(points)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("capture points are at the same temperature; spread them out")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    gain = sxy / sxx
    offset = my - gain * mx
    return Calibration(gain=round(gain, 5), offset=round(offset, 4))
