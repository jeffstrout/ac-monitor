"""Tests for the OPTO-5 sail-switch debounce (ac_monitor.debounce + poller wiring)."""

from ac_monitor import config as cfgmod
from ac_monitor.debounce import Debouncer
from ac_monitor.hat import HatError
from ac_monitor.poller import poll_once
from ac_monitor.state import AppState


def test_first_reading_accepted_immediately():
    d = Debouncer(5)
    assert d.update(True, 0.0) is True


def test_flutter_within_window_ignored():
    d = Debouncer(5)
    assert d.update(True, 0) is True
    assert d.update(False, 1) is True     # brief drop, only 0 s held
    assert d.update(True, 2) is True       # back before the window -> cancel
    assert d.update(False, 3) is True      # drops again; timer restarts
    assert d.update(False, 4) is True      # held 1 s (< 5) -> still True


def test_change_accepted_after_window():
    d = Debouncer(5)
    d.update(True, 0)
    d.update(False, 1)                      # pending False from t=1
    assert d.update(False, 5) is True      # 4 s held -> not yet
    assert d.update(False, 6) is False     # 5 s held -> accept False


def test_none_holds_last_value():
    d = Debouncer(5)
    d.update(True, 0)
    assert d.update(None, 1) is True        # read failed -> hold last
    assert d.update(None, 100) is True


def test_return_to_accepted_restarts_timer():
    d = Debouncer(5)
    d.update(True, 0)
    d.update(False, 1)                      # pending False
    d.update(True, 2)                      # back to True -> cancel pending
    d.update(False, 3)                     # new pending from t=3
    assert d.update(False, 7) is True      # only 4 s -> still True
    assert d.update(False, 8) is False     # 5 s -> False


def test_appstate_builds_debouncer_from_config():
    st = AppState(config=cfgmod.from_dict({"poll": {"fan_debounce_s": 5}}))
    assert st.fan_debouncer.seconds == 5.0


class _FlipBackend:
    """Valid thermistor volts; opto flips 1 -> 0 on successive reads."""

    def __init__(self):
        self._opto = [1, 0, 0, 0]
        self._i = 0

    def read_adc(self, stack, channel):
        return {1: 1.6, 2: 1.2, 3: 1.3, 4: 1.1}[channel]

    def read_opto(self, stack, channel):
        v = self._opto[min(self._i, len(self._opto) - 1)]
        self._i += 1
        return v


def test_poll_once_debounces_flutter():
    # Two polls happen within milliseconds (well under the 5 s window), so a
    # raw flip on the second read must not change the debounced fan state.
    st = AppState(config=cfgmod.from_dict({"poll": {"fan_debounce_s": 5, "interval_s": 60}}))
    b = _FlipBackend()
    poll_once(st, b)
    assert st.readings.fan_running is True          # first reading accepted
    poll_once(st, b)
    assert st.readings.fan_running is True          # raw dropped, but debounced holds
