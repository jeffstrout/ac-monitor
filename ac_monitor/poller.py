"""Background poll loop: read the HAT, derive, update shared state.

The HAT read is blocking (it shells out to ``ioplus``), so it runs in a worker
thread to avoid blocking the event loop. A read that raises never kills the
loop — a bad tick just leaves the previous state and is retried next interval.
"""

from __future__ import annotations

import asyncio
import time

from . import derive, display
from .hat import HatBackend, IoplusBackend, read_all
from .state import AppState


def poll_once(state: AppState, backend: HatBackend) -> None:
    """One synchronous read+derive+update. Safe to call from a thread."""
    now = time.time()
    readings = read_all(state.config, backend)
    # Debounce the sail switch: the derived state, dashboard, MQTT, and the
    # no_airflow fault all use the debounced value, not the raw flutter.
    readings.fan_running = state.fan_debouncer.update(readings.fan_running, now)
    derived = derive.compute(readings, state.config)
    state.update(readings, derived, now)


async def poll_loop(
    state: AppState,
    backend: HatBackend | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    backend = backend or IoplusBackend(timeout_s=state.config.poll.interval_s + 2)
    interval = state.config.poll.interval_s
    while stop is None or not stop.is_set():
        try:
            await asyncio.to_thread(poll_once, state, backend)
        except Exception:  # pragma: no cover - defensive; poll_once already tolerates read errors
            state.consecutive_errors += 1
        try:
            await asyncio.to_thread(display.maybe_push, state, time.time())
        except Exception:  # pragma: no cover - a display push must never break the loop
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval) if stop else await asyncio.sleep(interval)
        except asyncio.TimeoutError:
            pass
