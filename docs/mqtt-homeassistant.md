# MQTT & Home Assistant Integration

The service publishes all readings to MQTT and registers itself with Home Assistant via
**MQTT Discovery**, so entities appear automatically with no manual HA YAML.

## 1. Conventions

- **Base topic:** `ac_monitor` (configurable).
- **Discovery prefix:** `homeassistant` (HA default, configurable).
- **Device:** all entities are grouped under one HA device, `AC Monitor`, identified by
  the Pi's hostname/machine-id.
- **Availability (LWT):** the MQTT client sets a Last Will on
  `ac_monitor/status` = `offline`; on connect it publishes `online`. Every entity
  references this topic so HA shows the whole device as unavailable if the Pi drops.
- **Availability is self-healing (#73).** Retained `online` is re-published:
  - on every (re)connect (`on_connect`),
  - every `mqtt.announce_interval_s` (default 300 s) while connected,
  - when Home Assistant announces itself on `homeassistant/status` = `online`
    (discovery is re-sent too),
  - when we see our own `ac_monitor/status` flip to `offline` while connected
    (a stale LWT from a previous session).
- **Stable client id.** `mqtt.client_id` (default `ac-monitor-<hostname>`). A
  random per-boot id let a pre-reboot session linger on the broker and fire its
  retained LWT `offline` ~90 s *after* the new process announced `online`,
  leaving HA unavailable indefinitely (2026-10-25 incident, #73). With a stable
  id the broker takes the old session over at CONNECT time.
  Set it remotely (#75) with `POST /api/mqtt/config` `{"client_id": "ac-monitor-pi"}`:
  1–64 chars of `A-Z a-z 0-9 . _ -`; `""` or `null` resets to the default; omit
  the field to leave it unchanged. It is persisted to `/data/config.yaml`, and if
  MQTT is connected the publisher reconnects on the next poll (retained
  `offline` → disconnect → connect with the new id → `online` + discovery).
  Pin it in Docker: the default hostname is the container id, which changes on
  every Watchtower recreate.
- **Connection state** is tracked from `on_connect` / `on_disconnect` and exposed
  in `GET /api/state` as `mqtt: {enabled, connected, client_id, last_announce_at}`.
- **Publishing:** a single retained JSON state message plus per-entity state topics
  (JSON is convenient for the dashboard; per-entity topics keep HA discovery simple).

## 2. Topic tree

```
ac_monitor/
├── status                       # "online" / "offline"  (retained, LWT)
├── state                        # full SystemState as JSON (retained)
├── temperature/
│   ├── suction_line             # °C/°F  (refrigerant suction line)
│   ├── liquid_line              #        (refrigerant liquid line)
│   ├── input_air                #        (return air into the coil)
│   └── output_air               #        (supply air out of the coil)
├── airflow                      # "ON"/"OFF" (OPTO-5 pressure switch)
└── derived/
    ├── delta_t                  # air-side ΔT = input_air - output_air
    └── fault/<name>             # "ON"/"OFF" per fault (no_airflow, abnormal_delta_t, ...)
```

## 3. Home Assistant entities

| Entity | HA type | Source topic | Device class / unit |
|---|---|---|---|
| Suction Line Temp | sensor | `temperature/suction_line` | `temperature` |
| Liquid Line Temp | sensor | `temperature/liquid_line` | `temperature` |
| Input Air Temp | sensor | `temperature/input_air` | `temperature` |
| Output Air Temp | sensor | `temperature/output_air` | `temperature` |
| Air ΔT | sensor | `derived/delta_t` | `temperature` (Δ) |
| Airflow | binary_sensor | `airflow` | `running` / `moving` |
| No-Airflow Fault | binary_sensor | `derived/fault/no_airflow` | `problem` |
| Airflow Mismatch | binary_sensor | `derived/fault/airflow_mismatch` | `problem` |
| Abnormal ΔT | binary_sensor | `derived/fault/abnormal_delta_t` | `problem` |
| ΔT Not Developing | binary_sensor | `derived/fault/delta_t_not_developing` | `problem` |
| Wrong Direction | binary_sensor | `derived/fault/wrong_direction` | `problem` |
| Home Assistant Unavailable | binary_sensor | `derived/fault/ha_unavailable` | `problem` |
| Sensor Fault | binary_sensor | `derived/fault/sensor_fault` | `problem` |

**Airflow Mismatch** and **ΔT Not Developing** are the two worth an actual
notification: both mean the equipment is running and not delivering, and both
also drive `system_status` to `Error` on the dashboard. See
[ha-mode-source.md](ha-mode-source.md#faults).

*(Thermostat call binary sensors — Heat/Cool/Fan — get added when that phase lands.)*

## 4. Discovery payload example

Published at startup and re-sent on reconnect / HA birth (retained) to
`homeassistant/sensor/ac_monitor/input_air/config`:

```json
{
  "name": "Input Air Temp",
  "unique_id": "ac_monitor_input_air",
  "state_topic": "ac_monitor/temperature/input_air",
  "unit_of_measurement": "°F",
  "device_class": "temperature",
  "availability_topic": "ac_monitor/status",
  "device": {
    "identifiers": ["ac_monitor_pi"],
    "name": "AC Monitor",
    "manufacturer": "DIY",
    "model": "RPi 3B+ + Sequent Home Automation HAT"
  }
}
```

And a binary sensor at `homeassistant/binary_sensor/ac_monitor/airflow/config`:

```json
{
  "name": "Airflow",
  "unique_id": "ac_monitor_airflow",
  "state_topic": "ac_monitor/airflow",
  "payload_on": "ON",
  "payload_off": "OFF",
  "device_class": "running",
  "availability_topic": "ac_monitor/status",
  "device": { "identifiers": ["ac_monitor_pi"], "name": "AC Monitor" }
}
```

## 5. Suggested Home Assistant use

- **Dashboard card** grouping the four temps + air ΔT + airflow.
- **Automations / alerts:**
  - No airflow while a call is active *(once call signals exist)* → notify "possible
    blower/belt failure."
  - Abnormal cooling ΔT → notify "check charge / airflow."
  - Warm suction line or very hot liquid line → notify "check refrigerant charge."
- **History/graphing** comes for free once the entities exist.
