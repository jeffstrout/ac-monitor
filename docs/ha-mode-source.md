# Taking system mode from Home Assistant

**Status:** design, nothing implemented.
**Entity:** `climate.home` on Home Assistant at `192.168.0.105:8123`.
**Confirmed 2026-07-28:** the entity reports `hvac_action`. Design is valid; see
*Confirmed payload* below for what the real response changed.
**Scope change 2026-07-28:** the **sail switch is being removed** pending a
replacement sensor. See *Running without airflow proof* — it has real
consequences for the fallback.

Today `system_status` is *inferred* from the sail switch plus the sign of air-side
ΔT. This replaces the inference with the thermostat's own reported action, read
from Home Assistant — while keeping the sail switch, which answers a different
question.

---

## Why: the current logic is circular

The interesting reason for this change isn't accuracy, it's that
`derive.compute` currently reasons in a circle:

```python
if delta_f >= 0:  d.mode = "cooling";  check ΔT against the cooling band
else:             d.mode = "heating";  check ΔT against the heating band
```

Mode is inferred from the sign of ΔT, and then ΔT is validated against the band
selected by that mode. **A system blowing warm air while the thermostat calls for
cooling is silently reclassified as "heating", checked against heating
thresholds, and passes.** Stuck reversing valve, heat strips wrongly energised,
crossed sensors — all invisible.

An authoritative demand signal breaks the circle. That is the real win: not a
better label, but a class of fault becoming detectable at all.

---

## Signals

| Signal | Source | Question | Status |
|---|---|---|---|
| **Demand** | HA `climate.home` → `hvac_action` | what is it being *told* to do? | ✅ |
| **Achieved** | thermistors → ΔT | is it *working*? | ✅ |
| **Actual** | sail switch on OPTO-5 | is air *actually* moving? | ⛔ **removed, pending a replacement sensor** |

## Running without airflow proof

The sail switch is being removed for now. This is a deliberate, temporary
trade — recorded here so the cost is visible rather than discovered later.

**What is lost.** Airflow proof answers a question neither of the other two
signals can: *did the blower actually run?* Demand-without-airflow —

> thermostat calls for cooling, no air moving → **blower failed, belt broken,
> or filter blocked**

— was the highest-value fault available to this appliance, and it is not
detectable without it. `no_airflow` and `airflow_mismatch` are both off the table
until a sensor is back.

**What it does to the fallback.** This is the part that matters most. The
original design degraded to ΔT inference when HA was unreachable, and that
inference used `fan_running` to know whether air was moving. Without it, ΔT ≈ 0
is ambiguous:

- system off → ΔT ≈ 0
- blower running with no heat/cool call → ΔT ≈ 0

Identical readings, different states. So **HA stops being an authoritative source
with a backstop and becomes the only source of system state.** Acceptable for a
home HVAC monitor; it should still be a decision rather than a surprise.

**Consequence:** when HA is unavailable, do **not** guess. Suppress the ΔT-band
faults, report `system_status: "Unknown"`, raise `ha_unavailable`, and say so on
the panel. A confident wrong answer is worse than an honest gap.

**Design for its return.** Gate the airflow logic behind `airflow.enabled: false`
rather than deleting it, and keep its tests running. When the replacement sensor
arrives this becomes a config flag plus wiring, not a rewrite. OPTO-5 stays
documented in `docs/hardware.md` as unwired-but-reserved.

---

## What to read

**`hvac_action`**, the attribute — `heating` / `cooling` / `idle` / `fan` / `off`.

**Not** `hvac_mode` (the entity's `state`), which is only what the thermostat is
*set* to. A thermostat set to `cool` that is currently idle between cycles must
not read as "Cooling".

⚠️ **Not every thermostat reports `hvac_action`.** Confirm first:

```bash
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://192.168.0.105:8123/api/states/climate.home | python3 -m json.tool
```

If `attributes.hvac_action` is absent, this design does not apply as written —
falling back to `state` would report demand that isn't happening, which is worse
than today's inference. Stop and reconsider rather than substituting it.

### Confirmed payload (2026-07-28)

```jsonc
{
  "state": "heat_cool",                 // hvac_mode — auto changeover
  "attributes": {
    "hvac_action": "cooling",           // ← what we read
    "fan_mode": "auto",                 // fan_modes: ["on", "auto"]
    "current_temperature": 70,
    "target_temp_high": 70,             // dual setpoint;
    "target_temp_low": 65,              // "temperature" is null in this mode
    "current_humidity": 48.0
  },
  "last_changed":  "2026-07-28T14:48:48Z",
  "last_reported": "2026-07-28T15:17:42Z"
}
```

Three things this settled:

**The system runs in `heat_cool` (auto changeover).** This is the strongest
argument for the whole change: in auto mode the equipment chooses heating or
cooling on its own, which is precisely where inferring direction from the sign of
ΔT is most likely to be wrong. It also confirms `state` is useless as a mode
source — `heat_cool` says nothing about what is happening now.

**`fan_mode` exists**, and it resolves an open question below: the blower can be
forced on independently of any heat/cool call. See the corrected
`airflow_mismatch` condition.

**Staleness must key off `last_reported`, not `last_changed`.** In the sample
they are 29 minutes apart on a perfectly healthy entity — `last_changed` only
moves when the state itself changes, and a thermostat can legitimately sit in one
state for hours. Keying staleness on it would flag a working system as
unavailable.

---

## Mapping

| `hvac_action` | `system_status` | demand |
|---|---|---|
| `cooling` | Cooling | cooling |
| `heating` | Heating | heating |
| `fan` | Fan | fan |
| `idle` | Idle | none |
| `off` | Off | none |
| *(unavailable)* | *fall back to inference* | inferred |

`Off` is new — today's logic cannot distinguish "thermostat is off" from "idle
between cycles."

---

## Faults

Keep the existing three; `abnormal_delta_t` changes meaning because it now picks
its band from **demand** rather than from the sign of ΔT.

| Fault | Condition | Note |
|---|---|---|
| `sensor_fault` | any channel unreadable | unchanged |
| `no_airflow` | ~~sail switch open past debounce~~ | ⛔ **disabled** — no airflow sensor |
| `abnormal_delta_t` | ΔT outside the band **for the demanded mode** | no longer circular; **gated on demand**, since `fan_running` is gone |
| **`airflow_mismatch`** | ~~airflow demanded and sail switch open~~ | ⛔ **deferred** — needs an airflow sensor |
| **`wrong_direction`** | demand cooling but ΔT heating, or vice versa | only detectable with authoritative demand |
| **`ha_unavailable`** | HA enabled but unreachable or stale | degraded, not broken |

**When the airflow sensor returns**, "airflow demanded" is not the same as
"heating or cooling." With `fan_modes: ["on", "auto"]` the blower can be forced
to run with no call for heat or cool, so the condition is:

```python
airflow_demanded = hvac_action in ("heating", "cooling", "fan") or fan_mode == "on"
```

Using only `hvac_action` would miss a failed blower whenever the fan is set to
run continuously — a silent gap in exactly the mode people leave thermostats in
for air circulation. Recorded now so it isn't rediscovered later.

**`abnormal_delta_t` needs a new gate.** It is currently gated on `fan_running`;
with that gone, gate it on `hvac_action in ("heating", "cooling")` — arguably
better anyway, since ΔT is only meaningful during an actual call. When HA is
unavailable there is no gate, so the check is suppressed entirely.

`wrong_direction` needs a deadband and a settle delay — a heat pump takes time to
reverse, so the check must not fire during a legitimate changeover. Suggest
ignoring the first N seconds after `hvac_action` changes.

---

## Degrading, not depending

ac-monitor senses entirely on-device today. Reading demand from HA introduces a
dependency on a box that can be down — and this appliance's whole job is to keep
watching when things go wrong.

**Rules:**

- HA unreachable, timed out, or stale beyond `stale_after_s` → **report
  `system_status: "Unknown"`, suppress the ΔT-band faults, and raise
  `ha_unavailable`.** With the sail switch gone there is no longer enough local
  signal to infer state honestly, so do not guess.
- Never block the poll loop. The HAT read is the critical path; the HA fetch gets
  a short timeout and its own failure path, exactly as `display.push` does.
- Surface the source on the panel and in `/api/state` — an operator must be able
  to tell "the thermostat says cooling" from "we guessed cooling."

The architecture direction also inverts here: in the fleet plan ac-monitor is a
*producer* into HA's state plane. This makes it a consumer too. That is
acceptable for one authoritative upstream signal, but it is a real coupling and
should not become a habit.

---

## Don't echo HA's own data back to it

ac-monitor publishes `system_status` to HA over MQTT. Once that value *comes
from* HA, republishing it creates a round trip: HA's own thermostat state,
laundered through a sensor, reappearing as a second entity that can disagree with
the first during a fetch failure.

**Rule:** when `mode_source == home_assistant`, suppress the `system_status`
discovery/state publish. Keep publishing ΔT, temperatures, airflow and faults —
those are genuinely ours.

---

## Transport

**`urllib` with a short timeout**, called through `asyncio.to_thread` from the
poll loop. This is exactly what `display.py` already does — it notes "no extra
dependency" — and it keeps `requirements.txt` at four entries.

Rejected for now:

- **MQTT.** Architecturally the better fit, and ac-monitor already has a paho
  client — but blocked: **there is no broker on the LAN**
  (jeffstrout/homelab-standards#3). Revisit once one exists; an HA automation
  publishing `hvac_action` to a topic would remove the polling entirely.
- **HA WebSocket API.** Push-based and lower latency, but a persistent connection
  and reconnect logic for a value that changes every few minutes is not worth it
  against a 5 s poll.

---

## Config

```yaml
homeassistant:
  enabled: false                          # off unless configured, like mqtt
  base_url: "http://192.168.0.105:8123"
  token: ""                               # long-lived access token
  entity_id: "climate.home"
  timeout_s: 3                            # must not stall the poll loop
  stale_after_s: 60                       # vs last_reported, NOT last_changed
  changeover_settle_s: 180                # suppress wrong_direction after a change

airflow:
  enabled: false                          # no sensor fitted; OPTO-5 unwired.
                                          # Flip to true when one is back — the
                                          # logic and its tests are retained.
```

**`token` is a secret.** It must be redacted by `GET /api/config` (#43) and must
never reach the dashboard, the logs, or MQTT. Entered through the control panel
like the MQTT credentials, stored in `/data/config.yaml`.

---

## Implementation sketch

New `ac_monitor/ha.py`, following the repo's existing shape — pure functions
around an injectable boundary, so the tests need no Home Assistant:

```python
def parse_hvac_action(payload: dict) -> str | None:   # pure
def fetch_state(cfg, *, opener=urllib.request.urlopen) -> dict | None
class HaSource:            # caches last good value + timestamp, tracks staleness
    def current(self) -> tuple[str | None, str]        # (action, source)
```

`derive.compute(readings, cfg)` gains an optional `demand: str | None` argument
and stays **pure and stateless** — that property is why this module is easy to
test and must survive.

## Tests

Mirror `test_display.py`: pure parsing tested directly, fetching tested with an
injected opener. Cases that matter —

- `hvac_action` present / absent / entity missing / HA returns 401
- each action maps to the right `system_status`
- **`abnormal_delta_t` picks its band from demand, not from ΔT's sign** — the
  regression this whole change exists to prevent
- `wrong_direction` fires on a genuine mismatch and **not** during changeover settle
- `airflow_mismatch` fires on demand-without-airflow
- HA unavailable → falls back to inference, raises `ha_unavailable`, and the panel
  reports the source
- the token never appears in `/api/state`, `/api/config`, or a log line

## Rollout

1. Confirm `climate.home` reports `hvac_action` (above). **Blocking.**
2. Ship with `enabled: false`; behaviour is unchanged until configured.
3. Run both in parallel first — log inferred vs reported mode for a few days
   before letting HA drive faults. If they disagree often, the thresholds or the
   probe placement need attention, and that is worth knowing before the new
   faults start firing.

## Open questions

*(Resolved 2026-07-28: yes, `climate.home` exposes `fan_mode` separately — folded
into the `airflow_mismatch` condition above.)*

- Should `airflow_mismatch` alert immediately or after N consecutive polls? A
  blower takes a few seconds to spin up after a call starts.
- If HA is unavailable for a long period, should `ha_unavailable` escalate, or
  stay a quiet degraded state? It is not an HVAC fault.
