"""Relay↔opto loopback self-test (also a relay-activity stress test).

Toggles a relay every ``interval_s`` and confirms the wired opto input follows:
relay closed → opto reads closed (1). Reports the latest relay/opto states and
whether they match. Runs in the poller only when enabled in config.
"""

from __future__ import annotations

import logging
import time

_log = logging.getLogger("ac_monitor.relay_test")

_SETTLE_S = 0.5   # let the relay + bus settle after switching before reading the opto


class RelayLoopback:
    def __init__(self, backend, stack: int, relay_channel: int, opto_channel: int, interval_s: float):
        self.backend = backend
        self.stack = stack
        self.relay_channel = relay_channel
        self.opto_channel = opto_channel
        self.interval_s = interval_s
        self.relay_on = False
        self.last_toggle = 0.0
        self.result: dict | None = None
        self.checks = 0
        self.mismatches = 0

    def maybe_check(self, now: float) -> None:
        """Toggle + verify if the interval has elapsed; else no-op."""
        if self.last_toggle and (now - self.last_toggle) < self.interval_s:
            return
        self.relay_on = not self.relay_on
        opto_closed = None
        err = None
        try:
            self.backend.relay_write(self.stack, self.relay_channel, self.relay_on)
        except Exception as e:  # pragma: no cover - relay write rarely fails on its own
            err = f"relay_write: {e}"
        else:
            # The relay switching briefly disturbs the I2C read, so settle then
            # retry once before giving up.
            for _ in range(2):
                time.sleep(_SETTLE_S)
                try:
                    opto_closed = self.backend.read_opto(self.stack, self.opto_channel) == 1
                    err = None
                    break
                except Exception as e:
                    err = f"read_opto: {e}"

        if err is None:
            ok = opto_closed == self.relay_on
            self.checks += 1
            if not ok:
                self.mismatches += 1
            self.result = {
                "relay_closed": self.relay_on, "opto_closed": opto_closed, "ok": ok,
                "checks": self.checks, "mismatches": self.mismatches, "checked_at": now,
            }
        else:
            _log.warning("relay loopback check failed: %s", err)
            self.result = {
                "relay_closed": self.relay_on, "opto_closed": None, "ok": None, "error": err,
                "checks": self.checks, "mismatches": self.mismatches, "checked_at": now,
            }
        self.last_toggle = now

    def stop(self) -> None:
        """Leave the relay de-energized on shutdown (best effort)."""
        try:
            self.backend.relay_write(self.stack, self.relay_channel, False)
        except Exception:  # pragma: no cover
            pass
