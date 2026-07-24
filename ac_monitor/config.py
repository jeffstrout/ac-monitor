"""Configuration loading, validation, and round-trip save for AC Monitor.

Loads ``config.yaml`` into typed settings and can write it back, so the web
control panel can persist calibration edits and runtime toggles. The schema
mirrors ``config/config.example.yaml`` (see docs/appliance-plan.md §3, §7).

Design notes:
- Temperatures come from 10 kΩ NTC thermistors on HAT analog inputs AD1-AD4;
  each channel has a shared Beta/pull-up conversion plus an optional per-channel
  two-point ``gain``/``offset`` correction (the control panel's ice/boiling
  capture helpers write these).
- Unknown keys in the YAML are ignored (forward-compatible); missing keys fall
  back to the defaults below. Validation raises ``ConfigError`` with a clear
  message rather than a bare KeyError/TypeError.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

VALID_ROLES = ("output_air", "input_air", "suction_line", "liquid_line")
NUM_OPTO_CHANNELS = 8
NUM_ADC_CHANNELS = 8


class ConfigError(ValueError):
    """Raised when a config file is present but invalid."""


@dataclass
class Calibration:
    """Two-point linear correction applied after the Beta conversion:
    ``true_C = gain * reading_C + offset``."""

    gain: float = 1.024
    offset: float = -1.20


@dataclass
class Thermistors:
    hat_stack_level: int = 0
    beta: float = 3950.0
    r_nominal: float = 10000.0   # ohms at 25 C
    r_pullup: float = 15000.0    # HAT internal analog-input pull-up
    vref: float = 3.3            # pull-up rail (measured ~3.31)
    # Shared calibration, used for any channel without its own override.
    calibration: Calibration = field(default_factory=Calibration)
    # role -> AD channel (1..8).
    channels: dict[str, int] = field(
        default_factory=lambda: {
            "output_air": 1,
            "input_air": 2,
            "suction_line": 3,
            "liquid_line": 4,
        }
    )
    # Optional per-channel calibration overrides (role -> Calibration). The
    # control panel's capture helpers populate these.
    channel_calibration: dict[str, Calibration] = field(default_factory=dict)

    def calibration_for(self, role: str) -> Calibration:
        """The calibration for a role: its own override if present, else shared."""
        return self.channel_calibration.get(role, self.calibration)


@dataclass
class FanInput:
    opto_channel: int = 5        # OPTO-5: fan running/idle
    active_high: bool = True     # running => contact closed => active


@dataclass
class Digital:
    hat_stack_level: int = 0
    fan: FanInput = field(default_factory=FanInput)


@dataclass
class DeltaTThresholds:
    cooling_min_f: float = 15.0
    cooling_max_f: float = 22.0
    heating_min_f: float = 25.0
    heating_max_f: float = 70.0


@dataclass
class Thresholds:
    delta_t: DeltaTThresholds = field(default_factory=DeltaTThresholds)


@dataclass
class Display:
    """Split-flap display push (see docs/appliance-plan.md §5)."""

    enabled: bool = False
    base_url: str = "http://192.168.0.17:8080"
    slot: int = 2                # POST /api/screens/<slot>
    refresh_s: int = 30


@dataclass
class Mqtt:
    enabled: bool = False
    host: str = ""
    port: int = 1883
    username: str = ""
    password: str = ""
    base_topic: str = "ac_monitor"
    discovery_prefix: str = "homeassistant"
    retain: bool = True


@dataclass
class Poll:
    interval_s: float = 5.0
    fan_debounce_s: float = 3.0


@dataclass
class Web:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class Config:
    temperature_unit: str = "F"          # "C" or "F"
    poll: Poll = field(default_factory=Poll)
    thermistors: Thermistors = field(default_factory=Thermistors)
    digital: Digital = field(default_factory=Digital)
    thresholds: Thresholds = field(default_factory=Thresholds)
    display: Display = field(default_factory=Display)
    mqtt: Mqtt = field(default_factory=Mqtt)
    web: Web = field(default_factory=Web)


# --- loading -----------------------------------------------------------------

def _calibration_from(raw: dict[str, Any] | None) -> Calibration:
    raw = raw or {}
    return Calibration(
        gain=float(raw.get("gain", Calibration.gain)),
        offset=float(raw.get("offset", Calibration.offset)),
    )


def _thermistors_from(raw: dict[str, Any]) -> Thermistors:
    channels = {str(role): int(ch) for role, ch in (raw.get("channels") or {}).items()}
    channel_cal = {
        str(role): _calibration_from(c)
        for role, c in (raw.get("channel_calibration") or {}).items()
    }
    return Thermistors(
        hat_stack_level=int(raw.get("hat_stack_level", 0)),
        beta=float(raw.get("beta", Thermistors.beta)),
        r_nominal=float(raw.get("r_nominal", Thermistors.r_nominal)),
        r_pullup=float(raw.get("r_pullup", Thermistors.r_pullup)),
        vref=float(raw.get("vref", Thermistors.vref)),
        calibration=_calibration_from(raw.get("calibration")),
        channels=channels or Thermistors().channels,
        channel_calibration=channel_cal,
    )


def _digital_from(raw: dict[str, Any]) -> Digital:
    fan_raw = raw.get("fan") or {}
    return Digital(
        hat_stack_level=int(raw.get("hat_stack_level", 0)),
        fan=FanInput(
            opto_channel=int(fan_raw.get("opto_channel", FanInput.opto_channel)),
            active_high=bool(fan_raw.get("active_high", True)),
        ),
    )


def from_dict(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from a parsed YAML mapping, filling defaults."""
    data = data or {}
    units = data.get("units") or {}
    poll = data.get("poll") or {}
    thresh = (data.get("thresholds") or {}).get("delta_t") or {}
    disp = data.get("display") or {}
    mqtt = data.get("mqtt") or {}
    web = data.get("web") or {}
    sensors = data.get("sensors") or {}

    cfg = Config(
        temperature_unit=str(units.get("temperature", "F")).upper(),
        poll=Poll(
            interval_s=float(poll.get("interval_s", Poll.interval_s)),
            fan_debounce_s=float(
                poll.get("fan_debounce_s", poll.get("sail_debounce_s", Poll.fan_debounce_s))
            ),
        ),
        thermistors=_thermistors_from(sensors.get("thermistors") or {}),
        digital=_digital_from(sensors.get("digital") or {}),
        thresholds=Thresholds(
            delta_t=DeltaTThresholds(
                cooling_min_f=float(thresh.get("cooling_min_f", 15.0)),
                cooling_max_f=float(thresh.get("cooling_max_f", 22.0)),
                heating_min_f=float(thresh.get("heating_min_f", 25.0)),
                heating_max_f=float(thresh.get("heating_max_f", 70.0)),
            )
        ),
        display=Display(
            enabled=bool(disp.get("enabled", False)),
            base_url=str(disp.get("base_url", Display.base_url)),
            slot=int(disp.get("slot", Display.slot)),
            refresh_s=int(disp.get("refresh_s", Display.refresh_s)),
        ),
        mqtt=Mqtt(
            enabled=bool(mqtt.get("enabled", False)),
            host=str(mqtt.get("host", "")),
            port=int(mqtt.get("port", 1883)),
            username=str(mqtt.get("username", "")),
            password=str(mqtt.get("password", "")),
            base_topic=str(mqtt.get("base_topic", "ac_monitor")),
            discovery_prefix=str(mqtt.get("discovery_prefix", "homeassistant")),
            retain=bool(mqtt.get("retain", True)),
        ),
        web=Web(host=str(web.get("host", "0.0.0.0")), port=int(web.get("port", 8000))),
    )
    validate(cfg)
    return cfg


def load(path: str | Path) -> Config:
    """Load and validate a config file. Missing file -> :class:`ConfigError`."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:  # pragma: no cover - passthrough
        raise ConfigError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    return from_dict(data)


# --- validation --------------------------------------------------------------

def validate(cfg: Config) -> None:
    """Raise :class:`ConfigError` on any out-of-range / inconsistent setting."""
    if cfg.temperature_unit not in ("C", "F"):
        raise ConfigError(f"units.temperature must be 'C' or 'F', got {cfg.temperature_unit!r}")
    if cfg.poll.interval_s <= 0:
        raise ConfigError("poll.interval_s must be > 0")

    t = cfg.thermistors
    if not (0 <= t.hat_stack_level <= 7):
        raise ConfigError("thermistors.hat_stack_level must be 0..7")
    if t.beta <= 0 or t.r_nominal <= 0 or t.r_pullup <= 0 or t.vref <= 0:
        raise ConfigError("thermistors beta/r_nominal/r_pullup/vref must all be > 0")

    seen: dict[int, str] = {}
    for role, ch in t.channels.items():
        if role not in VALID_ROLES:
            raise ConfigError(
                f"thermistors.channels: unknown role {role!r} (expected one of {VALID_ROLES})"
            )
        if not (1 <= ch <= NUM_ADC_CHANNELS):
            raise ConfigError(f"thermistors.channels.{role}: AD channel must be 1..{NUM_ADC_CHANNELS}")
        if ch in seen:
            raise ConfigError(
                f"thermistors.channels: AD{ch} assigned to both {seen[ch]!r} and {role!r}"
            )
        seen[ch] = role

    # ΔT needs both air probes.
    for needed in ("input_air", "output_air"):
        if needed not in t.channels:
            raise ConfigError(f"thermistors.channels must include {needed!r} (needed for ΔT)")

    for role in t.channel_calibration:
        if role not in t.channels:
            raise ConfigError(
                f"thermistors.channel_calibration.{role}: no such channel role configured"
            )

    fan_ch = cfg.digital.fan.opto_channel
    if not (1 <= fan_ch <= NUM_OPTO_CHANNELS):
        raise ConfigError(f"digital.fan.opto_channel must be 1..{NUM_OPTO_CHANNELS}")

    if not (1 <= cfg.display.slot <= 6):
        raise ConfigError("display.slot must be 1..6")
    if cfg.mqtt.enabled and not cfg.mqtt.host:
        raise ConfigError("mqtt.enabled is true but mqtt.host is empty")


# --- saving (for the control panel to persist edits) -------------------------

def to_dict(cfg: Config) -> dict[str, Any]:
    """Serialize back to the on-disk YAML shape (round-trips with :func:`from_dict`)."""
    return {
        "units": {"temperature": cfg.temperature_unit},
        "poll": {"interval_s": cfg.poll.interval_s, "fan_debounce_s": cfg.poll.fan_debounce_s},
        "sensors": {
            "thermistors": {
                "hat_stack_level": cfg.thermistors.hat_stack_level,
                "beta": cfg.thermistors.beta,
                "r_nominal": cfg.thermistors.r_nominal,
                "r_pullup": cfg.thermistors.r_pullup,
                "vref": cfg.thermistors.vref,
                "calibration": asdict(cfg.thermistors.calibration),
                "channels": dict(cfg.thermistors.channels),
                "channel_calibration": {
                    role: asdict(c) for role, c in cfg.thermistors.channel_calibration.items()
                },
            },
            "digital": {
                "hat_stack_level": cfg.digital.hat_stack_level,
                "fan": asdict(cfg.digital.fan),
            },
        },
        "thresholds": {"delta_t": asdict(cfg.thresholds.delta_t)},
        "display": asdict(cfg.display),
        "mqtt": asdict(cfg.mqtt),
        "web": asdict(cfg.web),
    }


def save(cfg: Config, path: str | Path) -> None:
    """Validate and atomically write ``cfg`` to ``path`` as YAML."""
    validate(cfg)
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(to_dict(cfg), sort_keys=False))
    tmp.replace(p)
