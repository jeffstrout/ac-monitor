"""Tests for ac_monitor.ha — reading the thermostat's action from Home Assistant.

Nothing here touches Home Assistant: parsing is pure and the HTTP opener is
injected, mirroring test_display.py.
"""

import json
import urllib.error

from ac_monitor import config as cfgmod
from ac_monitor import ha

# Real payload shape, captured from climate.home on 2026-07-28.
COOLING = {
    "entity_id": "climate.home",
    "state": "cool",
    "attributes": {
        "hvac_modes": ["off", "heat", "cool", "heat_cool"],
        "fan_modes": ["on", "auto"],
        "current_temperature": 70,
        "temperature": 72,
        "target_temp_high": None,
        "target_temp_low": None,
        "fan_mode": "auto",
        "hvac_action": "cooling",
        "friendly_name": "Home",
    },
    "last_changed": "2026-07-28T15:25:50.729560+00:00",
    "last_reported": "2026-07-28T15:31:18.829262+00:00",
    "last_updated": "2026-07-28T15:31:18.829262+00:00",
}


def _cfg(**over):
    base = {"enabled": True, "base_url": "http://ha", "token": "tok",
            "entity_id": "climate.home"}
    base.update(over)
    return cfgmod.from_dict({"homeassistant": base})


class _Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(body):
    def _open(req, timeout=None):
        return _Resp(body)
    return _open


# --- parsing ----------------------------------------------------------------

def test_parses_hvac_action_and_fan_mode():
    r = ha.parse_state(COOLING)
    assert r.action == "cooling"
    assert r.fan_mode == "auto"
    assert r.valid is True


def test_reads_hvac_action_not_state():
    """The observed counterexample: mode `cool`, action `idle`.

    Sourcing from `state` would report "Cooling" on a satisfied house with
    nothing running.
    """
    payload = json.loads(json.dumps(COOLING))
    payload["state"] = "cool"
    payload["attributes"]["hvac_action"] = "idle"

    r = ha.parse_state(payload)
    assert r.action == "idle"
    assert ha.ACTION_TO_STATUS[r.action] == "Idle"


def test_uses_last_reported_not_last_changed():
    """last_changed can be hours old on a healthy entity."""
    r = ha.parse_state(COOLING)
    changed = ha._epoch(COOLING["last_changed"])
    reported = ha._epoch(COOLING["last_reported"])
    assert r.last_reported == reported
    assert r.last_reported > changed


def test_missing_hvac_action_is_invalid_not_an_error():
    payload = {"state": "cool", "attributes": {"fan_mode": "auto"}}
    r = ha.parse_state(payload)
    assert r.valid is False
    assert r.action is None


def test_garbage_payload_does_not_raise():
    assert ha.parse_state(None).valid is False
    assert ha.parse_state([]).valid is False
    assert ha.parse_state({}).valid is False


# --- fetching ---------------------------------------------------------------

def test_fetch_returns_none_when_disabled():
    cfg = _cfg(enabled=False)
    assert ha.fetch_state(cfg, opener=_opener(COOLING)) is None


def test_fetch_returns_none_without_a_token():
    cfg = _cfg(token="")
    assert ha.fetch_state(cfg, opener=_opener(COOLING)) is None


def test_fetch_sends_bearer_token_to_the_entity_url():
    seen = {}

    def _open(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        return _Resp(COOLING)

    cfg = _cfg()
    assert ha.fetch_state(cfg, opener=_open)["state"] == "cool"
    assert seen["url"] == "http://ha/api/states/climate.home"
    assert seen["auth"] == "Bearer tok"
    assert seen["timeout"] == cfg.homeassistant.timeout_s


def test_fetch_swallows_transport_errors():
    """401, 404, timeout, DNS failure — all mean 'no demand this tick'."""
    def _boom(req, timeout=None):
        raise urllib.error.HTTPError("http://ha", 401, "Unauthorized", {}, None)

    assert ha.fetch_state(_cfg(), opener=_boom) is None


# --- HaSource: staleness, settling, demand ----------------------------------

def test_source_reports_demand_after_a_good_poll():
    cfg, src = _cfg(), ha.HaSource()
    src.poll(cfg, now=100.0, opener=_opener(COOLING))

    assert src.demand(cfg, 100.0) == "cooling"
    assert src.available(cfg, 100.0) is True
    assert src.error is None


def test_demand_goes_none_once_the_last_good_fetch_ages_out():
    cfg, src = _cfg(stale_after_s=60), ha.HaSource()
    src.poll(cfg, now=100.0, opener=_opener(COOLING))

    assert src.demand(cfg, 155.0) == "cooling"   # still inside the window
    assert src.demand(cfg, 200.0) is None        # aged out
    assert src.available(cfg, 200.0) is False


def test_failed_poll_keeps_the_last_value_until_it_ages_out():
    def _boom(req, timeout=None):
        raise OSError("unreachable")

    cfg, src = _cfg(stale_after_s=60), ha.HaSource()
    src.poll(cfg, now=100.0, opener=_opener(COOLING))
    src.poll(cfg, now=110.0, opener=_boom)

    assert src.error == "unreachable"
    assert src.demand(cfg, 110.0) == "cooling"   # one blip must not blind us
    assert src.demand(cfg, 200.0) is None


def test_a_thermostat_without_hvac_action_is_reported_distinctly():
    cfg, src = _cfg(), ha.HaSource()
    src.poll(cfg, now=100.0, opener=_opener({"state": "cool", "attributes": {}}))

    assert src.error == "no hvac_action"
    assert src.demand(cfg, 100.0) is None


def test_changeover_is_unsettled_until_the_delay_elapses():
    cfg = _cfg(changeover_settle_s=180)
    src = ha.HaSource()
    src.poll(cfg, now=100.0, opener=_opener(COOLING))

    heating = json.loads(json.dumps(COOLING))
    heating["attributes"]["hvac_action"] = "heating"
    src.poll(cfg, now=200.0, opener=_opener(heating))

    assert src.settled(cfg, 250.0) is False   # valve still moving
    assert src.settled(cfg, 400.0) is True


def test_disabled_source_never_calls_the_opener():
    calls = []

    def _open(req, timeout=None):
        calls.append(1)
        return _Resp(COOLING)

    cfg = _cfg(enabled=False)
    ha.HaSource().poll(cfg, now=100.0, opener=_open)
    assert calls == []
