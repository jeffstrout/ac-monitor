"""HAT hardware-watchdog petter.

When enabled, the poller pets the Sequent HAT watchdog every tick. If the app
or the Pi hangs and stops petting, the HAT removes power from the Pi and
restores it (a full power cycle) — the recovery path for an unattended
appliance. See docs/i2c-lockup.md.

Note: the watchdog is petted over I²C, so a *jammed* bus makes the pet fail,
which is exactly what should let the timer expire and fire. Whether the fire
also clears the card's own latch is unverified — enable and test on hardware.
"""

from __future__ import annotations


class Watchdog:
    def __init__(self, backend, stack: int, period_s: int):
        self.backend = backend
        self.stack = stack
        self.period_s = period_s
        self._armed = False

    def pet(self) -> bool:
        """Set the period (first call) and reload the timer. Returns False and
        re-arms on the next call if the HAT can't be reached (e.g. bus jammed)."""
        try:
            if not self._armed:
                self.backend.set_watchdog_period(self.stack, self.period_s)
                self._armed = True
            self.backend.pet_watchdog(self.stack)
            return True
        except Exception:
            self._armed = False
            return False
