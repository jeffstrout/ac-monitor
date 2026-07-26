"""Read the Sequent Home Automation HAT: thermistor temps (AD1-AD4) + the
airflow sail switch (OPTO-5).

The live read shells out to the Sequent ``ioplus`` CLI (``ioplus <stack> adcrd
<ch>`` / ``optrd <ch>``) — the interface we've verified on the bench — behind a
small backend Protocol so the conversion/aggregation logic is fully testable
off-Pi with a mock. The volts->°C math is pure functions.

See docs/hardware.md (I/O map, bring-up) and docs/calibration.md (conversion).
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from .config import Config, Thermistors

# Kelvin at 25 °C, the thermistor reference point.
_T0_K = 298.15


class HatError(RuntimeError):
    """A HAT read failed (bus lockup, card not detected, bad value)."""


# --- pure conversion ---------------------------------------------------------

def volts_to_resistance(volts: float, r_pullup: float, vref: float) -> float:
    """Thermistor resistance from the divider voltage (pull-up ``r_pullup`` to
    ``vref``, thermistor to GND). Raises on an open/shorted reading."""
    if not (0.0 < volts < vref):
        raise HatError(
            f"reading {volts:.3f} V out of range (0..{vref}); open circuit or bad channel"
        )
    return r_pullup * volts / (vref - volts)


def resistance_to_celsius(resistance: float, beta: float, r_nominal: float) -> float:
    """NTC Beta equation: resistance -> °C."""
    inv_t = 1.0 / _T0_K + (1.0 / beta) * math.log(resistance / r_nominal)
    return 1.0 / inv_t - 273.15


def volts_to_celsius(volts: float, therm: Thermistors, role: str) -> float:
    """Full chain for one channel: volts -> resistance -> °C -> calibrated."""
    r = volts_to_resistance(volts, therm.r_pullup, therm.vref)
    raw_c = resistance_to_celsius(r, therm.beta, therm.r_nominal)
    cal = therm.calibration_for(role)
    return cal.gain * raw_c + cal.offset


def c_to_unit(celsius: float, unit: str) -> float:
    return celsius if unit.upper() == "C" else celsius * 9.0 / 5.0 + 32.0


def delta_to_unit(delta_c: float, unit: str) -> float:
    """Convert a temperature *difference* (no 32° offset)."""
    return delta_c if unit.upper() == "C" else delta_c * 9.0 / 5.0


# --- backends ----------------------------------------------------------------

class HatBackend(Protocol):
    def read_adc(self, stack: int, channel: int) -> float: ...
    def read_opto(self, stack: int, channel: int) -> int: ...


class IoplusBackend:
    """Live backend — shells out to the ``ioplus`` CLI with a timeout so a
    jammed I²C bus can't hang the poller."""

    def __init__(self, timeout_s: float = 5.0, binary: str = "ioplus"):
        self.timeout_s = timeout_s
        self.binary = binary

    def _run(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as e:
            raise HatError(f"{self.binary} not installed") from e
        except subprocess.TimeoutExpired as e:
            raise HatError(f"{self.binary} {' '.join(args)} timed out (bus jammed?)") from e
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0 or "not detected" in out.lower() or not out:
            raise HatError(f"{self.binary} {' '.join(args)}: {out or 'no output'}")
        return out

    def read_adc(self, stack: int, channel: int) -> float:
        return float(self._run(str(stack), "adcrd", str(channel)))

    def read_opto(self, stack: int, channel: int) -> int:
        return int(float(self._run(str(stack), "optrd", str(channel))))

    def relay_write(self, stack: int, channel: int, on: bool) -> None:
        self._run(str(stack), "relwr", str(channel), "1" if on else "0")

    def relay_read(self, stack: int, channel: int) -> int:
        return int(float(self._run(str(stack), "relrd", str(channel))))

    def set_watchdog_period(self, stack: int, seconds: int) -> None:
        self._run(str(stack), "wdtpwr", str(seconds))

    def pet_watchdog(self, stack: int) -> None:
        """Reload the HAT watchdog (arms it on the first call)."""
        self._run(str(stack), "wdtr")


# --- aggregation -------------------------------------------------------------

@dataclass
class Readings:
    unit: str
    temps: dict[str, float | None] = field(default_factory=dict)   # display unit, calibrated
    temps_c: dict[str, float | None] = field(default_factory=dict)  # °C, calibrated
    volts: dict[str, float | None] = field(default_factory=dict)    # raw ADC volts (diagnostic)
    fan_running: bool | None = None
    delta_t: float | None = None                                    # display unit, input - output
    health: dict[str, bool] = field(default_factory=dict)

    @property
    def i2c_ok(self) -> bool:
        """True if at least one channel read succeeded (bus is alive)."""
        return any(self.health.values())


def read_all(cfg: Config, backend: HatBackend) -> Readings:
    """Read every configured channel. A failed read marks that channel
    unhealthy (value ``None``) rather than raising, so one bad channel or a
    transient bus hiccup doesn't take the whole poll down."""
    therm = cfg.thermistors
    unit = cfg.temperature_unit
    r = Readings(unit=unit)

    for role, ch in therm.channels.items():
        try:
            v = backend.read_adc(therm.hat_stack_level, ch)
            c = volts_to_celsius(v, therm, role)
            r.volts[role] = round(v, 4)
            r.temps_c[role] = round(c, 2)
            r.temps[role] = round(c_to_unit(c, unit), 1)
            r.health[role] = True
        except (HatError, ValueError):
            r.volts[role] = None
            r.temps_c[role] = None
            r.temps[role] = None
            r.health[role] = False

    fan = cfg.digital.fan
    try:
        raw = backend.read_opto(cfg.digital.hat_stack_level, fan.opto_channel)
        closed = raw == 1
        r.fan_running = closed if fan.active_high else not closed
        r.health["fan"] = True
    except (HatError, ValueError):
        r.fan_running = None
        r.health["fan"] = False

    ic, oc = r.temps_c.get("input_air"), r.temps_c.get("output_air")
    if ic is not None and oc is not None:
        r.delta_t = round(delta_to_unit(ic - oc, unit), 1)

    return r


# --- CLI selftest ------------------------------------------------------------

def selftest(cfg: Config, backend: HatBackend | None = None) -> Readings:
    backend = backend or IoplusBackend(timeout_s=cfg.poll.interval_s + 2)
    r = read_all(cfg, backend)
    u = r.unit
    print(f"AC Monitor HAT selftest  (stack {cfg.thermistors.hat_stack_level}, °{u})")
    print("-" * 52)
    for role, ch in cfg.thermistors.channels.items():
        ok = r.health.get(role)
        val = f"{r.temps[role]:>6.1f} °{u}" if ok else "  FAIL"
        volt = f"{r.volts[role]:.3f} V" if ok else "  --  "
        print(f"  AD{ch}  {role:<12} {volt:>9}  {val}")
    fan = "RUNNING" if r.fan_running else ("IDLE" if r.fan_running is not None else "FAIL")
    print(f"  OPTO-{cfg.digital.fan.opto_channel} fan          {fan}")
    if r.delta_t is not None:
        print(f"  ΔT (input - output) = {r.delta_t:.1f} °{u}")
    print("-" * 52)
    print("bus:", "OK" if r.i2c_ok else "NOT DETECTED")
    return r


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Read the HAT once and print a table.")
    ap.add_argument("--config", default="config/config.yaml", help="path to config.yaml")
    ap.add_argument("--selftest", action="store_true", help="print one reading of every sensor")
    args = ap.parse_args(argv)

    from . import config as configmod

    try:
        cfg = configmod.load(args.config)
    except configmod.ConfigError:
        cfg = configmod.from_dict({})  # fall back to defaults for a bare bench test
    r = selftest(cfg)
    return 0 if r.i2c_ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
