"""Tests for the control-panel endpoints: toggles, MQTT config, calibration capture."""

from fastapi.testclient import TestClient

from ac_monitor import config as cfgmod
from ac_monitor.hat import HatError
from ac_monitor.state import AppState
from ac_monitor.web.app import create_app


class MockBackend:
    def read_adc(self, stack, channel):
        return {1: 1.60, 2: 1.20, 3: 1.30, 4: 1.10}[channel]

    def read_opto(self, stack, channel):
        return 1


def _state(tmp_path):
    cfg = cfgmod.from_dict(
        {"units": {"temperature": "F"}, "poll": {"interval_s": 60}, "display": {"enabled": False}}
    )
    return AppState(config=cfg, config_path=str(tmp_path / "config.yaml"))


def test_toggle_display_persists(tmp_path):
    state = _state(tmp_path)
    with TestClient(create_app(state, MockBackend())) as c:
        assert c.post("/api/toggle/display").json()["display_push"] is True
        assert state.config.display.enabled is True
        assert cfgmod.load(state.config_path).display.enabled is True   # persisted to disk


def test_toggle_mqtt_requires_host(tmp_path):
    with TestClient(create_app(_state(tmp_path), MockBackend())) as c:
        assert c.post("/api/toggle/mqtt").status_code == 409


def test_mqtt_config_then_enable(tmp_path):
    state = _state(tmp_path)
    with TestClient(create_app(state, MockBackend())) as c:
        c.post("/api/mqtt/config", json={"host": "192.168.1.10", "port": 1883})
        r = c.post("/api/toggle/mqtt")
        assert r.status_code == 200 and r.json()["mqtt"] is True
        assert cfgmod.load(state.config_path).mqtt.host == "192.168.1.10"


def test_capture_two_points_fits_and_persists(tmp_path):
    state = _state(tmp_path)
    with TestClient(create_app(state, MockBackend())) as c:
        # Simulate the probe at two known temps by setting the live volts.
        state.readings.volts["suction_line"] = 2.27       # cold (~ice)
        c.post("/api/calibrate/capture", json={"role": "suction_line", "known_c": 0.0})
        state.readings.volts["suction_line"] = 0.14       # hot (~boiling)
        r = c.post("/api/calibrate/capture", json={"role": "suction_line", "known_c": 99.4}).json()
        assert "calibration" in r
        assert "suction_line" in state.config.thermistors.channel_calibration
        assert "suction_line" in cfgmod.load(state.config_path).thermistors.channel_calibration


def test_capture_unknown_role_400(tmp_path):
    with TestClient(create_app(_state(tmp_path), MockBackend())) as c:
        assert c.post("/api/calibrate/capture", json={"role": "attic", "known_c": 0}).status_code == 400


def test_manual_then_reset(tmp_path):
    state = _state(tmp_path)
    with TestClient(create_app(state, MockBackend())) as c:
        c.post("/api/calibrate/manual", json={"role": "input_air", "gain": 1.05, "offset": -0.5})
        assert state.config.thermistors.channel_calibration["input_air"].gain == 1.05
        c.post("/api/calibrate/reset", json={"role": "input_air"})
        assert "input_air" not in state.config.thermistors.channel_calibration


def test_calibration_view(tmp_path):
    with TestClient(create_app(_state(tmp_path), MockBackend())) as c:
        v = c.get("/api/calibration").json()
        assert set(v) == {"output_air", "input_air", "suction_line", "liquid_line"}
        assert "gain" in v["input_air"] and "captures" in v["input_air"]


def test_capture_missing_reading_409(tmp_path):
    state = _state(tmp_path)
    with TestClient(create_app(state, MockBackend())) as c:
        state.readings.volts["suction_line"] = None
        r = c.post("/api/calibrate/capture", json={"role": "suction_line", "known_c": 0.0})
        assert r.status_code == 409
