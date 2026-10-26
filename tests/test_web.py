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
    # display disabled so the background poller makes no network push in tests.
    cfg = cfgmod.from_dict(
        {"units": {"temperature": "F"}, "poll": {"interval_s": 60}, "display": {"enabled": False}}
    )
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
        assert s["toggles"] == {"display_push": False, "mqtt": False, "relay_test": False}


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


def test_api_health_matches_the_appliance_contract():
    """/api/health is the standard spelling (homelab-standards)."""
    with _client() as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # The contract requires these alongside status.
        assert "version" in body
        assert isinstance(body["uptime_seconds"], int)


def test_api_health_reports_degraded_when_bus_down():
    """Alive but not doing its job — liveness alone would call this healthy."""
    with _client(adc={}, opto={}) as c:
        r = c.get("/api/health")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"


def test_healthz_alias_matches_api_health():
    """The deprecated alias must not drift from the endpoint it aliases."""
    with _client() as c:
        old, new = c.get("/healthz").json(), c.get("/api/health").json()
        assert old["status"] == new["status"]
        assert old.keys() == new.keys()


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


# --- #75: client_id via POST /api/mqtt/config ---------------------------------

def _mqtt_client(tmp_path):
    cfg = cfgmod.from_dict(
        {"units": {"temperature": "F"}, "poll": {"interval_s": 60}, "display": {"enabled": False}}
    )
    path = tmp_path / "config.yaml"
    state = AppState(config=cfg, config_path=str(path))
    return TestClient(create_app(state, MockBackend({1: 1.6, 2: 1.2, 3: 1.3, 4: 1.1}, {5: 1}))), state, path


def test_mqtt_config_sets_and_persists_client_id(tmp_path):
    c, state, path = _mqtt_client(tmp_path)
    with c:
        r = c.post("/api/mqtt/config", json={"client_id": "ac-monitor-pi"})
        assert r.status_code == 200
        body = r.json()
        assert body["client_id"] == "ac-monitor-pi"
        assert body["effective_client_id"] == "ac-monitor-pi"
        assert "password" not in body
    assert cfgmod.load(path).mqtt.client_id == "ac-monitor-pi"


def test_mqtt_config_absent_client_id_unchanged(tmp_path):
    c, state, _ = _mqtt_client(tmp_path)
    state.config.mqtt.client_id = "keep-me"
    with c:
        r = c.post("/api/mqtt/config", json={"port": 1884})
        assert r.status_code == 200 and r.json()["client_id"] == "keep-me"


def test_mqtt_config_reset_client_id(tmp_path):
    c, state, path = _mqtt_client(tmp_path)
    with c:
        for reset in ("", None):
            state.config.mqtt.client_id = "ac-monitor-pi"
            r = c.post("/api/mqtt/config", json={"client_id": reset})
            assert r.status_code == 200
            assert r.json()["client_id"] == ""
            assert r.json()["effective_client_id"].startswith("ac-monitor-")
    assert cfgmod.load(path).mqtt.client_id == ""


def test_mqtt_config_rejects_bad_client_id(tmp_path):
    c, state, _ = _mqtt_client(tmp_path)
    with c:
        for bad in ("has space", "a/b", "wild+", "hash#", "x" * 65, "ünï"):
            r = c.post("/api/mqtt/config", json={"client_id": bad})
            assert r.status_code == 422, bad
    assert state.config.mqtt.client_id == ""
