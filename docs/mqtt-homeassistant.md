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
├── airflow                      # "ON"/"OFF" (sail switch)
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
| Abnormal ΔT | binary_sensor | `derived/fault/abnormal_delta_t` | `problem` |
| Sensor Fault | binary_sensor | `derived/fault/sensor_fault` | `problem` |

*(Thermostat call binary sensors — Heat/Cool/Fan — get added when that phase lands.)*

## 4. Discovery payload example

Published once at startup (retained) to
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
