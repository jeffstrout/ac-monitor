"""Tests for ac_monitor.config — schema loading, validation, round-trip save."""

from pathlib import Path

import pytest

from ac_monitor import config as cfgmod
from ac_monitor.config import Config, ConfigError

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


def test_example_config_loads():
    cfg = cfgmod.load(EXAMPLE)
    assert cfg.temperature_unit in ("C", "F")
    # As-wired mapping (docs/appliance-plan.md §1).
    assert cfg.thermistors.channels["output_air"] == 1
    assert cfg.thermistors.channels["input_air"] == 2
    assert cfg.thermistors.channels["suction_line"] == 3
    assert cfg.thermistors.channels["liquid_line"] == 4
    assert cfg.digital.fan.opto_channel == 5


def test_defaults_when_empty():
    cfg = cfgmod.from_dict({})
    assert cfg.thermistors.beta == 3950.0
    assert cfg.thermistors.channels["output_air"] == 1
    assert cfg.display.slot == 2
    assert cfg.display.enabled is True     # display push on by default
    assert cfg.mqtt.enabled is False
    # The OPTO-5 pressure switch is fitted, so a fresh seed matches the hardware.
    assert cfg.airflow.enabled is True


def test_develop_thresholds_default_to_the_commissioned_values():
    dt = cfgmod.from_dict({}).thresholds.delta_t
    assert dt.cooling_develop_f == 10.0
    assert dt.heating_develop_f == -20.0
    assert dt.develop_after_s == 120.0


def test_develop_thresholds_round_trip(tmp_path):
    cfg = cfgmod.from_dict(
        {"thresholds": {"delta_t": {"cooling_develop_f": 12, "heating_develop_f": -18,
                                    "develop_after_s": 90}},
         "airflow": {"enabled": True, "prove_after_s": 45}}
    )
    p = tmp_path / "config.yaml"
    cfgmod.save(cfg, p)
    back = cfgmod.load(p)

    assert back.thresholds.delta_t.cooling_develop_f == 12.0
    assert back.thresholds.delta_t.heating_develop_f == -18.0
    assert back.thresholds.delta_t.develop_after_s == 90.0
    assert back.airflow.prove_after_s == 45.0


def test_develop_threshold_signs_are_enforced():
    """A sign error would invert the check silently — a system delivering nothing
    would read as healthy."""
    with pytest.raises(ConfigError, match="cooling_develop_f"):
        cfgmod.from_dict({"thresholds": {"delta_t": {"cooling_develop_f": -10}}})
    with pytest.raises(ConfigError, match="heating_develop_f"):
        cfgmod.from_dict({"thresholds": {"delta_t": {"heating_develop_f": 20}}})


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        cfgmod.load("/no/such/config.yaml")


def test_bad_unit_rejected():
    with pytest.raises(ConfigError):
        cfgmod.from_dict({"units": {"temperature": "K"}})


def test_duplicate_channel_rejected():
    with pytest.raises(ConfigError, match="assigned to both"):
        cfgmod.from_dict(
            {"sensors": {"thermistors": {"channels": {"input_air": 1, "output_air": 1}}}}
        )


def test_unknown_role_rejected():
    with pytest.raises(ConfigError, match="unknown role"):
        cfgmod.from_dict({"sensors": {"thermistors": {"channels": {"attic_air": 2}}}})


def test_missing_air_probe_rejected():
    # Only output_air -> no ΔT possible.
    with pytest.raises(ConfigError, match="input_air"):
        cfgmod.from_dict({"sensors": {"thermistors": {"channels": {"output_air": 1}}}})


def test_opto_channel_range():
    with pytest.raises(ConfigError):
        cfgmod.from_dict({"sensors": {"digital": {"fan": {"opto_channel": 9}}}})


def test_mqtt_enabled_needs_host():
    with pytest.raises(ConfigError, match="mqtt.host"):
        cfgmod.from_dict({"mqtt": {"enabled": True}})


def test_per_channel_calibration_override():
    cfg = cfgmod.from_dict(
        {
            "sensors": {
                "thermistors": {
                    "channel_calibration": {"input_air": {"gain": 1.01, "offset": -0.5}}
                }
            }
        }
    )
    ic = cfg.thermistors.calibration_for("input_air")
    assert (ic.gain, ic.offset) == (1.01, -0.5)
    # A channel without an override falls back to the shared calibration.
    sc = cfg.thermistors.calibration_for("suction_line")
    assert sc is cfg.thermistors.calibration


def test_round_trip_save_load(tmp_path):
    cfg = cfgmod.load(EXAMPLE)
    cfg.mqtt.enabled = True
    cfg.mqtt.host = "192.168.1.10"
    cfg.thermistors.channel_calibration["input_air"] = cfgmod.Calibration(1.03, -0.9)
    out = tmp_path / "config.yaml"
    cfgmod.save(cfg, out)

    reloaded = cfgmod.load(out)
    assert reloaded.mqtt.enabled is True
    assert reloaded.mqtt.host == "192.168.1.10"
    ic = reloaded.thermistors.calibration_for("input_air")
    assert (round(ic.gain, 3), round(ic.offset, 3)) == (1.03, -0.9)


def test_legacy_sail_debounce_key_accepted():
    # Back-compat: old configs used poll.sail_debounce_s.
    cfg = cfgmod.from_dict({"poll": {"sail_debounce_s": 7}})
    assert cfg.poll.fan_debounce_s == 7.0
