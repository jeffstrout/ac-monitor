"""Push readings to the split-flap display (Info Split-Flap mode, slot 2).

Uses the verified ``POST /api/screens/<slot>`` API (see docs/i2c-lockup.md and
the split-flap repo). Formatting is a pure function; the HTTP push uses stdlib
``urllib`` (no extra dependency) with a short timeout so a slow/absent display
never stalls the poller. Pushed on the display's own cadence (``refresh_s``),
not every poll.
"""

from __future__ import annotations

import json
import urllib.request

from .hat import Readings
from .derive import Derived

# Split-flap board is 24 cols; screen content is the top rows (bottom row is the
# display's own auto date/time). We push a compact 7-line screen.


def _t(readings: Readings | None, role: str, unit: str) -> str:
    v = readings.temps.get(role) if readings else None
    return f"{v:.1f}{unit}" if v is not None else "--"


def format_lines(readings: Readings | None, derived: Derived | None, unit: str) -> list[str]:
    dt = derived.delta_t if derived else None
    fan = None if readings is None else readings.fan_running
    fan_txt = "RUN" if fan else ("IDLE" if fan is not None else "--")
    return [
        "AC MONITOR",
        f"RET {_t(readings, 'input_air', unit)}",
        f"SUP {_t(readings, 'output_air', unit)}",
        f"DT  {f'{dt:.1f}{unit}' if dt is not None else '--'}",
        f"SUC {_t(readings, 'suction_line', unit)}",
        f"LIQ {_t(readings, 'liquid_line', unit)}",
        f"FAN {fan_txt}",
    ]


def push(base_url: str, slot: int, lines: list[str], timeout: float = 4.0) -> bool:
    """POST the lines to the split-flap slot. Returns True on HTTP 200."""
    body = json.dumps({"lines": lines, "align": "center"}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/screens/{slot}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed host from config)
            return getattr(resp, "status", resp.getcode()) == 200
    except Exception:
        return False


def maybe_push(state, now: float, push_fn=push) -> bool:
    """Push to the display if enabled and its refresh interval has elapsed."""
    d = state.config.display
    if not d.enabled or state.readings is None:
        return False
    if state.last_display_push is not None and (now - state.last_display_push) < d.refresh_s:
        return False
    lines = format_lines(state.readings, state.derived, state.config.temperature_unit)
    ok = push_fn(d.base_url, d.slot, lines)
    state.last_display_push = now
    return ok
