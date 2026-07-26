"""Tests for ac_monitor.display — line formatting + push cadence/gating."""

from ac_monitor import config as cfgmod
from ac_monitor import display
from ac_monitor.derive import Derived
from ac_monitor.hat import Readings
from ac_monitor.state import AppState


def _readings():
    r = Readings(unit="F")
    r.temps = {"input_air": 72.4, "output_air": 55.3, "suction_line": 40.1, "liquid_line": 90.2}
    r.health = {k: True for k in r.temps}
    r.fan_running = True
    return r


def test_format_lines_full():
    lines = display.format_lines(_readings(), Derived(delta_t=17.2, system_status="Cooling"), "F")
    assert lines[0] == "HVAC MONITOR"
    assert len(lines) == 7 and all(len(x) <= 24 for x in lines)
    body = "\n".join(lines)
    assert "°" not in body      # °F units removed
    assert "." not in body      # decimals removed
    ret_row = next(l for l in lines if l.startswith("RET"))
    assert "RET +72" in ret_row and "SUC +40" in ret_row   # RET/SUC same (left/right) row
    sup_row = next(l for l in lines if l.startswith("SUP"))
    assert "SUP +55" in sup_row and "LIQ +90" in sup_row
    assert lines[5] == "DELTA T +17"        # line 6: Delta T, centered on push
    assert lines[6] == "SYSTEM COOLING"     # line 7: system status, centered on push


def test_format_lines_missing_channel_shows_dashes():
    r = Readings(unit="F")
    r.temps = {"input_air": 72.4}   # only one connected (bench)
    r.health = {"input_air": True}
    r.fan_running = None
    lines = display.format_lines(r, Derived(delta_t=None), "F")
    ret_row = next(l for l in lines if l.startswith("RET"))
    assert "RET +72" in ret_row and "SUC --" in ret_row
    sup_row = next(l for l in lines if l.startswith("SUP"))
    assert "SUP --" in sup_row and "LIQ --" in sup_row
    assert lines[5] == "DELTA T --"
    assert lines[6] == "SYSTEM IDLE"        # default status when derived is idle


def test_format_lines_negative_temp_keeps_minus():
    r = Readings(unit="F")
    r.temps = {"suction_line": -5.3}
    r.health = {"suction_line": True}
    r.fan_running = True
    lines = display.format_lines(r, Derived(delta_t=-3.1, system_status="Heating"), "F")
    assert any("SUC -5" in l for l in lines)      # -5.3 -> -5, minus kept
    assert lines[5] == "DELTA T -3"
    assert lines[6] == "SYSTEM HEATING"


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
