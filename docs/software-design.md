# Software Design

Design for the AC Monitor application. This is the plan the implementation will follow;
no code exists yet.

## 1. Goals & non-goals

**Goals**
- Poll all sensors on a fixed interval and expose the readings + derived metrics.
- Serve a lightweight live web dashboard.
- Publish to MQTT with Home Assistant auto-discovery.
- Run unattended as a `systemd` service, survive reboots, and degrade gracefully if a
  single sensor is unplugged.

**Non-goals (for now)**
- Controlling the HVAC equipment (relays/0–10 V outputs stay unused).
- Long-term historical storage/graphing — leave that to Home Assistant / InfluxDB.

## 2. Technology stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Sequent ships a Python library; rich sensor ecosystem |
| HAT opto inputs | Sequent `SMioplus` library | Official driver for the Home Automation HAT |
| 1-Wire temps | Linux `w1` sysfs (`/sys/bus/w1/devices`) | Kernel-native, no extra driver |
| Pressure sensor | `smbus2` (raw I²C) or `sensirion-i2c-sdp` | SDP810 is plain I²C |
| MQTT | `paho-mqtt` | De-facto standard, HA-friendly |
| Web | FastAPI + Uvicorn | Async, tiny, serves API + static dashboard |
| Config | YAML (`pyyaml`) | Human-editable sensor mapping/calibration |
| Process mgmt | `systemd` | Standard on Raspberry Pi OS |

> Library note: the exact `SMioplus` function names (e.g. reading an opto channel) should
> be confirmed against the installed version at implementation time — the
> [ioplus-rpi repo](https://github.com/SequentMicrosystems/ioplus-rpi) is the source of truth.

## 3. Module layout (planned)

```
ac_monitor/
├── __init__.py
├── config.py            # load & validate config.yaml -> typed settings
├── sensors/
│   ├── onewire.py       # DS18B20 discovery + read by ROM id
│   ├── pressure.py      # SDP810 I2C read (pressure + temp)
│   └── digital.py       # HAT opto inputs (sail switch; future call signals)
├── core/
│   ├── poller.py        # async loop; reads all sensors each tick
│   ├── model.py         # Reading / SystemState dataclasses
│   └── derive.py        # delta-T, airflow status, fault detection
├── mqtt/
│   ├── client.py        # paho wrapper, connect/reconnect, LWT
│   └── discovery.py     # Home Assistant MQTT discovery payloads
├── web/
│   ├── app.py           # FastAPI app: /api/state, /api/version, /healthz, dashboard
│   └── static/          # single-page dashboard (HTML/CSS/JS)
├── version.py           # reads APP_COMMIT / APP_BUILD_TIME baked into the image
└── __main__.py          # wire everything together; run poller + web + mqtt
```

`__main__.py` is what the Docker image runs (`python -m ac_monitor`), which is
also what makes the [auto-update pipeline](auto-update.md) go live.

## 4. Data model

```
Reading        = { key, value, unit, timestamp, healthy: bool }
SystemState    = {
    temps:     { return_air, supply_air, coil, spare }   # °C (or °F per config)
    pressure_pa, pressure_inh2o, sensor_temp             # from SDP810
    airflow:   bool                                      # sail switch
    derived:   { delta_t, filter_loading_pct?, faults[] }
    updated_at
}
```

## 5. Derived metrics & fault logic

- **Coil ΔT** = `return_air − supply_air`. The headline HVAC health number.
- **Airflow proof** = sail switch state (boolean).
- **Filter/coil ΔP** = SDP810 reading, reported in Pa and inH₂O.
- **Faults** (each a named boolean the dashboard/MQTT expose):
  - `no_airflow` — sail switch open for longer than a debounce window.
  - `airflow_no_call` *(enabled once thermostat signals are added)* — air moving but no
    W/Y/G call, or vice-versa.
  - `abnormal_delta_t` — ΔT outside a configurable heating/cooling band (e.g. cooling ΔT
    should be ~15–22 °F; too low → low charge/airflow, too high → restricted airflow).
  - `high_filter_dp` — ΔP above a configurable threshold → change the filter.
  - `sensor_fault` — any probe/bus read failing.

Thresholds live in `config.yaml` so they can be tuned to this specific system.

## 6. Poll loop

- Single async task ticks every `poll_interval_s` (default 5 s).
- Each tick reads all sensors concurrently; a failing sensor marks its reading
  `healthy: false` rather than crashing the loop.
- After each tick: recompute derived state → push to the web layer (in-memory latest
  state) → publish changed values to MQTT.
- Sail switch is debounced (default 3 s) to ignore momentary vane flutter.

## 7. Configuration schema

See [`config/config.example.yaml`](../config/config.example.yaml). Highlights:

- `units.temperature`: `C` or `F`.
- `sensors.onewire`: map each DS18B20 ROM id → role (`return_air`, etc.).
- `sensors.pressure`: I²C bus/address, part range, tubing polarity.
- `sensors.digital`: HAT stack level + opto channel for the sail switch.
- `mqtt`: broker host/port/credentials, base topic, HA discovery prefix.
- `thresholds`: ΔT band, filter ΔP limit, debounce windows.
- `web`: bind host/port.

## 8. Deployment

Two supported paths:

**A. Docker + auto-update (recommended)** — see [auto-update.md](auto-update.md).
The Pi pulls a prebuilt multi-arch image from GHCR and self-updates via Watchtower
whenever a PR merges to `main`. Files: [`deploy/Dockerfile`](../deploy/Dockerfile),
[`deploy/docker-compose.yml`](../deploy/docker-compose.yml),
[`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml).
The container runs `privileged` for I²C + 1-Wire access; `/data` holds the config
and persisted state across updates.

**B. Bare-metal systemd (alternative)** — install into a virtualenv at
`/opt/ac-monitor`; a `deploy/ac-monitor.service` unit runs `python -m ac_monitor`
with `Restart=on-failure` after `network-online.target`; an `install.sh` enables
I²C + 1-Wire, creates the venv, installs deps, and enables the service. (No
hands-off updates on this path — you'd `git pull` + restart yourself.)

**Build provenance / `/api/version`.** The image bakes `APP_COMMIT` and
`APP_BUILD_TIME` (from CI) into the environment; `version.py` reads them and the
web layer exposes `GET /api/version` so you can confirm which build is running
after an auto-update.

Optional on either path: enable the HAT hardware watchdog for unattended
reliability (documented in the Sequent user guide).

## 9. Testing strategy

- **Unit**: `derive.py` fault logic and unit conversions with synthetic readings.
- **Sensor mocks**: fake `onewire`/`pressure`/`digital` backends so the poller, web API,
  and MQTT discovery can be tested off-Pi.
- **On-Pi smoke test**: a `--selftest` flag that reads each sensor once and prints a table.
