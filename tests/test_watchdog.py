"""Tests for the HAT watchdog petter + config wiring."""

from ac_monitor import config as cfgmod
from ac_monitor.watchdog import Watchdog


class FakeWdBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.period = None
        self.pets = 0

    def set_watchdog_period(self, stack, seconds):
        if self.fail:
            raise RuntimeError("bus jammed")
        self.period = seconds

    def pet_watchdog(self, stack):
        if self.fail:
            raise RuntimeError("bus jammed")
        self.pets += 1


def test_pet_sets_period_once_then_reloads():
    b = FakeWdBackend()
    wd = Watchdog(b, stack=0, period_s=120)
    assert wd.pet() is True
    assert b.period == 120 and b.pets == 1
    assert wd.pet() is True
    assert b.pets == 2                      # period set once, timer reloaded each pet


def test_pet_failure_rearms():
    b = FakeWdBackend(fail=True)
    wd = Watchdog(b, stack=0, period_s=120)
    assert wd.pet() is False
    assert wd._armed is False               # will re-set period on the next successful pet


def test_config_watchdog_defaults_off():
    cfg = cfgmod.from_dict({})
    assert cfg.watchdog.enabled is False
    assert cfg.watchdog.period_s == 120


def test_config_watchdog_roundtrip():
    cfg = cfgmod.from_dict({"watchdog": {"enabled": True, "period_s": 60}})
    assert cfg.watchdog.enabled is True and cfg.watchdog.period_s == 60
    reloaded = cfgmod.from_dict(cfgmod.to_dict(cfg))
    assert reloaded.watchdog.enabled is True and reloaded.watchdog.period_s == 60
