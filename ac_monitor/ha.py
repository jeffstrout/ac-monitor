"""Read the thermostat's reported action from Home Assistant.

Why this exists: system mode used to be *inferred* from the sail switch plus the
sign of air-side ΔT, which reasons in a circle — mode came from ΔT's sign, then
ΔT was validated against the band that mode selected. A system blowing warm air
during a cooling call was silently reclassified as heating and passed. Reading
the thermostat's own ``hvac_action`` breaks that circle. See
docs/ha-mode-source.md.

We read ``hvac_action`` (what it is *doing*), never ``state`` (``hvac_mode`` —
what it is *set to*). Observed 2026-07-28: ``state: "cool"`` with
``hvac_action: "idle"`` on a satisfied house. Using ``state`` would have reported
"Cooling" while nothing was running.

``urllib`` with a short timeout, matching display.py — no extra dependency, and
the fetch must never stall the poll loop.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

# hvac_action -> the system_status we report.
ACTION_TO_STATUS = {
    "heating": "Heating",
    "cooling": "Cooling",
    "fan": "Fan",
    "idle": "Idle",
    "off": "Off",
}
# Actions that mean the equipment is actively conditioning air.
CONDITIONING = ("heating", "cooling")


@dataclass
class HaReading:
    """One parsed observation of the climate entity."""

    action: str | None = None        # hvac_action, lowercased
    fan_mode: str | None = None      # "on" | "auto" — independent of heat/cool
    last_reported: float | None = None   # epoch seconds, from HA

    @property
    def valid(self) -> bool:
        return self.action in ACTION_TO_STATUS


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def parse_state(payload: dict | None) -> HaReading:
    """Pure: turn an HA ``/api/states/<entity>`` body into a reading.

    Returns an invalid reading rather than raising — a thermostat that does not
    report ``hvac_action`` must degrade, not crash the poll.
    """
    if not isinstance(payload, dict):
        return HaReading()
    attrs = payload.get("attributes") or {}
    action = attrs.get("hvac_action")
    fan_mode = attrs.get("fan_mode")

    # last_reported moves on every write; last_changed only when the state
    # itself changes, and a thermostat can legitimately sit in one state for
    # hours (observed 29 min apart on a healthy entity). Using last_changed
    # here would flag a working system as stale.
    reported = payload.get("last_reported") or payload.get("last_updated")

    return HaReading(
        action=str(action).lower() if isinstance(action, str) else None,
        fan_mode=str(fan_mode).lower() if isinstance(fan_mode, str) else None,
        last_reported=_epoch(reported if isinstance(reported, str) else None),
    )


def fetch_state(cfg, *, opener=urllib.request.urlopen) -> dict | None:
    """GET the entity. Returns the parsed body, or None on any failure.

    ``opener`` is injected in tests, so nothing here needs Home Assistant.
    """
    ha = cfg.homeassistant
    if not ha.enabled or not ha.base_url or not ha.token or not ha.entity_id:
        return None
    url = f"{ha.base_url.rstrip('/')}/api/states/{ha.entity_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {ha.token}",
            "Accept": "application/json",
        },
    )
    try:
        with opener(req, timeout=ha.timeout_s) as resp:  # noqa: S310 (host from config)
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        # Includes 401 (bad token) and 404 (wrong entity_id). All of them mean
        # the same thing to the caller: no authoritative demand this tick.
        return None


class HaSource:
    """Tracks the latest demand plus how long since a successful fetch.

    Holds the small amount of state ``derive.compute`` must not: staleness, and
    when the action last changed (so a heat-pump changeover doesn't trip
    ``wrong_direction`` while the reversing valve is still moving).
    """

    def __init__(self) -> None:
        self.reading = HaReading()
        self.last_ok_at: float | None = None
        self.action_since: float | None = None
        self.error: str | None = None

    def poll(self, cfg, now: float, *, opener=urllib.request.urlopen) -> None:
        if not cfg.homeassistant.enabled:
            self.error = None
            return
        body = fetch_state(cfg, opener=opener)
        reading = parse_state(body)
        if not reading.valid:
            # Unreachable, bad token, wrong entity, or a thermostat that does
            # not report hvac_action. Keep the last good value; available()
            # decides when it has aged out.
            self.error = "unreachable" if body is None else "no hvac_action"
            return
        if reading.action != self.reading.action:
            self.action_since = now
        self.reading = reading
        self.last_ok_at = now
        self.error = None

    def available(self, cfg, now: float) -> bool:
        if not cfg.homeassistant.enabled or self.last_ok_at is None:
            return False
        return (now - self.last_ok_at) <= cfg.homeassistant.stale_after_s

    def settled(self, cfg, now: float) -> bool:
        """False while a changeover is still in progress."""
        if self.action_since is None:
            return True
        return (now - self.action_since) >= cfg.homeassistant.changeover_settle_s

    def demand(self, cfg, now: float) -> str | None:
        """The authoritative action, or None when we cannot vouch for it."""
        return self.reading.action if self.available(cfg, now) else None
