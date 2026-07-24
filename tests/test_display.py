"""Tests for ac_monitor.display — line formatting + push cadence/gating."""

from ac_monitor import config as cfgmod
from ac_monitor import display
from ac_monitor.derive import Derived
from ac_monitor.hat import Readings
from ac_monitor.state import AppState


def _readings():
    r = Readings(unit="F")
    r.temps = {"input_air": 72.5, "output_air": 55.3, "suction_line": 40.1, "liquid_line": 90.2}
    r.health = {k: True for k in r.temps}
    r.fan_running = True
    return r


def test_format_lines_full():
    r = _readings()
    d = Derived(delta_t=17.2)
    lines = display.format_lines(r, d, "F")
    assert lines[0] == "AC MONITOR"
    assert "RET +72.5F" in lines
    assert "SUP +55.3F" in lines
    assert "DT  +17.2F" in lines
    assert "FAN RUN" in lines
    assert len(lines) == 7
    assert all(len(x) <= 24 for x in lines)


def test_format_lines_missing_channel_shows_dashes():
    r = Readings(unit="F")
    r.temps = {"input_air": 72.5}   # only one connected (bench)
    r.health = {"input_air": True}
    r.fan_running = None
    lines = display.format_lines(r, Derived(delta_t=None), "F")
    assert "RET +72.5F" in lines
    assert "SUP --" in lines
    assert "DT  --" in lines
    assert "FAN --" in lines


def test_format_lines_negative_temp_keeps_minus():
    r = Readings(unit="F")
    r.temps = {"suction_line": -5.3}
    r.health = {"suction_line": True}
    r.fan_running = True
    lines = display.format_lines(r, Derived(delta_t=-3.1), "F")
    assert "SUC -5.3F" in lines      # negative keeps its minus
    assert "DT  -3.1F" in lines


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, base_url, slot, lines, timeout=4.0):
        self.calls.append((base_url, slot, lines))
        return True


def _state(enabled, refresh_s=30):
    cfg = cfgmod.from_dict({"display": {"enabled": enabled, "slot": 2, "refresh_s": refresh_s}})
    st = AppState(config=cfg)
    st.readings = _readings()
    st.derived = Derived(delta_t=17.2)
    return st


def test_no_push_when_disabled():
    rec = _Recorder()
    assert display.maybe_push(_state(False), 1000.0, rec) is False
    assert rec.calls == []


def test_push_when_enabled_and_due():
    st, rec = _state(True), _Recorder()
    assert display.maybe_push(st, 1000.0, rec) is True
    assert len(rec.calls) == 1
    assert rec.calls[0][1] == 2                      # slot 2
    assert st.last_display_push == 1000.0


def test_respects_refresh_interval():
    st, rec = _state(True, refresh_s=30), _Recorder()
    display.maybe_push(st, 1000.0, rec)              # first push
    assert display.maybe_push(st, 1010.0, rec) is False   # 10 s < 30 s -> skip
    assert display.maybe_push(st, 1031.0, rec) is True    # 31 s -> push again
    assert len(rec.calls) == 2


def test_no_push_without_readings():
    cfg = cfgmod.from_dict({"display": {"enabled": True}})
    st = AppState(config=cfg)   # no readings yet
    assert display.maybe_push(st, 1000.0, _Recorder()) is False
