"""Tests for the relay↔opto loopback self-test + its config."""

from ac_monitor import config as cfgmod
from ac_monitor.relay_test import RelayLoopback


class LoopbackBackend:
    """Perfect loopback: the opto follows the relay state."""

    def __init__(self):
        self.relay = False

    def relay_write(self, stack, channel, on):
        self.relay = on

    def read_opto(self, stack, channel):
        return 1 if self.relay else 0


class BrokenBackend:
    """Opto never follows the relay (mismatch)."""

    def relay_write(self, stack, channel, on):
        pass

    def read_opto(self, stack, channel):
        return 0


def test_loopback_pass():
    rl = RelayLoopback(LoopbackBackend(), 0, 5, 5, 15)
    rl.maybe_check(1000.0)                     # first check always runs
    assert rl.result["ok"] is True
    assert rl.result["relay_closed"] is True and rl.result["opto_closed"] is True
    assert rl.checks == 1 and rl.mismatches == 0


def test_loopback_toggles_each_interval():
    b = LoopbackBackend()
    rl = RelayLoopback(b, 0, 5, 5, 15)
    rl.maybe_check(1000.0)
    assert rl.relay_on is True and b.relay is True
    rl.maybe_check(1016.0)                     # >15 s later -> toggles off
    assert rl.relay_on is False and b.relay is False


def test_interval_gating():
    rl = RelayLoopback(LoopbackBackend(), 0, 5, 5, 15)
    rl.maybe_check(1000.0)
    n = rl.checks
    rl.maybe_check(1010.0)                     # 10 s < 15 s -> no-op
    assert rl.checks == n


def test_mismatch_counted():
    rl = RelayLoopback(BrokenBackend(), 0, 5, 5, 15)
    rl.maybe_check(1000.0)                     # relay closed but opto reads open
    assert rl.result["ok"] is False
    assert rl.result["opto_closed"] is False and rl.mismatches == 1


def test_stop_deenergizes():
    b = LoopbackBackend()
    rl = RelayLoopback(b, 0, 5, 5, 15)
    rl.maybe_check(1000.0)
    assert b.relay is True
    rl.stop()
    assert b.relay is False


class FlakyOptoBackend:
    """read_opto raises the first `fails` times, then follows the relay."""

    def __init__(self, fails=1):
        self.relay = False
        self.fails = fails
        self.calls = 0

    def relay_write(self, stack, channel, on):
        self.relay = on

    def read_opto(self, stack, channel):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("bus glitch")
        return 1 if self.relay else 0


class AlwaysRaiseOptoBackend:
    def relay_write(self, stack, channel, on):
        pass

    def read_opto(self, stack, channel):
        raise RuntimeError("not detected")


def test_retry_recovers_from_transient():
    rl = RelayLoopback(FlakyOptoBackend(fails=1), 0, 5, 5, 15)
    rl.maybe_check(1000.0)                     # first read raises, retry succeeds
    assert rl.result["ok"] is True and rl.checks == 1


def test_persistent_error_surfaces_detail():
    rl = RelayLoopback(AlwaysRaiseOptoBackend(), 0, 5, 5, 15)
    rl.maybe_check(1000.0)
    assert rl.result["ok"] is None
    assert "read_opto" in rl.result["error"]   # actual exception surfaced, not a bare flag
    assert rl.checks == 0


def test_config_defaults_off():
    cfg = cfgmod.from_dict({})
    assert cfg.relay_selftest.enabled is False
    assert cfg.relay_selftest.relay_channel == 5
    # A SPARE input: OPTO-5 carries the airflow pressure switch, and looping the
    # relay test onto it would read the switch and report failures that aren't real.
    assert cfg.relay_selftest.opto_channel == 8
    assert cfg.relay_selftest.opto_channel != cfg.digital.fan.opto_channel


def test_config_roundtrip():
    cfg = cfgmod.from_dict(
        {"relay_selftest": {"enabled": True, "relay_channel": 5, "opto_channel": 5, "interval_s": 15}}
    )
    assert cfg.relay_selftest.enabled is True
    reloaded = cfgmod.from_dict(cfgmod.to_dict(cfg))
    assert reloaded.relay_selftest.enabled is True and reloaded.relay_selftest.interval_s == 15
