"""Tests for the poll loop's wiring — that what derive needs actually reaches it.

The fault logic itself is tested in test_derive.py against explicit arguments.
What that cannot catch is the seam: ``poll_once`` must hand derive the thermostat's
fan_mode and the elapsed-call clock, or the "running but not delivering" checks
silently never fire on the real appliance. Nothing here touches Home Assistant —
the source is pre-loaded with the answer.
"""

import time

from ac_monitor import config as cfgmod
from ac_monitor import ha
from ac_monitor.poller import poll_once
from ac_monitor.state import AppState


class _Backend:
    """Valid thermistor volts; the opto reads whatever the test wants."""

    def __init__(self, opto: int, adc=None):
        self._opto = opto
        self._adc = adc or {1: 1.60, 2: 1.20, 3: 1.30, 4: 1.10}

    def read_adc(self, stack, channel):
        return self._adc[channel]

    def read_opto(self, stack, channel):
        return self._opto


class _FrozenHa(ha.HaSource):
    """A HaSource that never fetches: the thermostat's answer, pre-loaded and
    fresh, with the call already running for ``running_for_s``."""

    def __init__(self, action, fan_mode="auto", running_for_s=300.0):
        super().__init__()
        now = time.time()
        self.reading = ha.HaReading(action=action, fan_mode=fan_mode, last_reported=now)
        self.last_ok_at = now
        self.action_since = now - running_for_s

    def poll(self, cfg, now, **kw):
        self.last_ok_at = now


def _cfg(**over):
    base = {
        "poll": {"interval_s": 60},
        "display": {"enabled": False},
        "homeassistant": {"enabled": True, "token": "t", "base_url": "http://ha"},
    }
    base.update(over)
    return cfgmod.from_dict(base)


def test_poll_once_reports_error_when_a_call_is_active_and_air_is_not_moving():
    st = AppState(config=_cfg(), ha=_FrozenHa("cooling"))
    poll_once(st, _Backend(opto=0))

    assert st.readings.fan_running is False
    assert st.derived.faults["airflow_mismatch"] is True
    assert st.derived.system_status == "Error"
    assert st.snapshot()["system_status"] == "Error"


def test_poll_once_passes_the_thermostats_fan_mode_through():
    """fan_mode "on" with no heat/cool call still demands a turning blower."""
    st = AppState(config=_cfg(), ha=_FrozenHa("idle", fan_mode="on"))
    poll_once(st, _Backend(opto=0))

    assert st.derived.faults["airflow_mismatch"] is True


def test_poll_once_passes_the_elapsed_call_clock_through():
    """Same no-airflow reading, but the call only just started — spin-up grace."""
    st = AppState(config=_cfg(), ha=_FrozenHa("cooling", running_for_s=5.0))
    poll_once(st, _Backend(opto=0))

    assert st.derived.faults["airflow_mismatch"] is False
    assert st.derived.system_status == "Cooling"


def test_poll_once_flags_a_cooling_call_that_never_develops_delta_t():
    # AD2 (input_air) and AD1 (output_air) nearly equal -> ΔT ~ 0 after 5 minutes
    # of cooling: air is moving and nothing is being cooled.
    st = AppState(config=_cfg(), ha=_FrozenHa("cooling"))
    poll_once(st, _Backend(opto=1, adc={1: 1.20, 2: 1.21, 3: 1.30, 4: 1.10}))

    assert st.readings.fan_running is True
    assert st.derived.faults["delta_t_not_developing"] is True
    assert st.derived.system_status == "Error"
    # The thermostat's own answer is untouched by the escalation.
    assert st.derived.mode == "cooling"
    assert st.derived.mode_source == "home_assistant"


def test_poll_once_is_quiet_on_a_healthy_cooling_cycle():
    # AD1 (output/supply) colder than AD2 (return): ΔT +18 °F, in band and well
    # past the +10 develop floor.
    st = AppState(config=_cfg(), ha=_FrozenHa("cooling"))
    poll_once(st, _Backend(opto=1, adc={1: 1.55, 2: 1.20, 3: 1.30, 4: 1.10}))

    assert st.readings.delta_t == 18.0
    assert st.derived.any_fault is False
    assert st.derived.system_status == "Cooling"
