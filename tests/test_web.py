"""Tests for the FastAPI app + poller wiring, using a mock HAT backend."""

from fastapi.testclient import TestClient

from ac_monitor import config as cfgmod
from ac_monitor.hat import HatError
from ac_monitor.state import AppState
from ac_monitor.web.app import create_app


class MockBackend:
    def __init__(self, adc, opto):
        self.adc, self.opto = adc, opto

    def read_adc(self, stack, channel):
        if channel not in self.adc:
            raise HatError("not detected")
        return self.adc[channel]

    def read_opto(self, stack, channel):
        if channel not in self.opto:
            raise HatError("not detected")
        return self.opto[channel]


def _client(adc=None, opto=None):
    cfg = cfgmod.from_dict({"units": {"temperature": "F"}, "poll": {"interval_s": 60}})
    state = AppState(config=cfg)
    a = {1: 1.60, 2: 1.20, 3: 1.30, 4: 1.10} if adc is None else adc
    o = {5: 1} if opto is None else opto
    return TestClient(create_app(state, MockBackend(a, o)))


def test_api_state_populated_after_startup():
    with _client() as c:
        s = c.get("/api/state").json()
        assert s["i2c_ok"] is True
        assert s["fan_running"] is True
        assert set(s["temps"]) == {"output_air", "input_air", "suction_line", "liquid_line"}
        assert all(s["health"].values())
        assert s["poll_count"] >= 1
        assert s["delta_t"] is not None
        assert s["toggles"] == {"display_push": False, "mqtt": False}


def test_api_version():
    with _client() as c:
        v = c.get("/api/version").json()
        assert "commit" in v and "built_at" in v


def test_healthz_ok_when_bus_up():
    with _client() as c:
        assert c.get("/healthz").status_code == 200


def test_healthz_degraded_when_bus_down():
    with _client(adc={}, opto={}) as c:
        r = c.get("/healthz")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"
        assert c.get("/api/state").json()["i2c_ok"] is False


def test_dashboard_served():
    with _client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "AC Monitor" in r.text
        assert "/api/state" in r.text   # the page fetches state


def test_partial_sensor_loss_still_serves():
    # Only AD2 connected (like the current bench) — others FAIL but API stays up.
    with _client(adc={2: 1.315}, opto={5: 1}) as c:
        s = c.get("/api/state").json()
        assert s["health"]["input_air"] is True
        assert s["health"]["output_air"] is False
        assert s["i2c_ok"] is True
        assert s["faults"]["sensor_fault"] is True
