"""Debounce a boolean input (the OPTO-5 sail switch).

The sail-switch vane flutters as airflow varies, so the raw ``optrd`` value
bounces. This accepts a *change* only after the new value has held for
``seconds``; brief flutter is ignored.

- The first non-``None`` reading is accepted immediately (no startup delay).
- ``None`` means the read failed — hold the last accepted value rather than
  dropping to a false state.

Stateful, so it lives here / in the poller, not in the pure ``hat.read_all``
(which always reports the instantaneous raw state).
"""

from __future__ import annotations


class Debouncer:
    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self.state: bool | None = None
        self._pending: bool | None = None
        self._pending_since: float = 0.0

    def update(self, raw: bool | None, now: float) -> bool | None:
        """Feed the raw reading + a monotonic-ish timestamp; return the
        debounced state."""
        if raw is None:                      # read failed -> hold last accepted
            self._pending = None
            return self.state
        if self.state is None:               # first good reading -> accept now
            self.state = raw
            self._pending = None
            return self.state
        if raw == self.state:                # matches accepted -> cancel any pending
            self._pending = None
            return self.state
        # raw differs from the accepted state: start/continue the pending timer.
        if self._pending != raw:
            self._pending = raw
            self._pending_since = now
        if now - self._pending_since >= self.seconds:
            self.state = raw
            self._pending = None
        return self.state
