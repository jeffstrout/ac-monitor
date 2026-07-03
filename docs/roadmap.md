# Roadmap

Phased plan from the current design docs to a deployed system. Each phase is
independently useful and testable.

## Phase 0 — Design ✅ (this repo, now)
- Hardware I/O map, wiring plan, BOM.
- Software architecture, config schema, MQTT/Home Assistant spec.
- **Auto-update scaffolding in place** — Dockerfile, `docker-compose.yml` (with
  Watchtower), and the GHCR publish workflow all committed. The CI build is
  guarded off until the app package exists, so it activates automatically in
  Phase 2. See [auto-update.md](auto-update.md).

## Phase 1 — Bench bring-up
- Assemble Pi + HAT; enable I²C and 1-Wire.
- Verify each DS18B20 enumerates under `/sys/bus/w1/devices` and record ROM ids.
- Confirm the SDP810 responds at `0x25` via `i2cdetect`.
- Confirm the sail switch reads on HAT opto input 1.
- Deliverable: a `--selftest` script that prints one reading from every sensor.

## Phase 2 — Core service
- Implement `sensors/`, `core/poller.py`, `core/derive.py`, config loading.
- Compute ΔT, airflow status, filter ΔP, and faults.
- Add `ac_monitor/__main__.py` + `version.py` — this **unguards the CI build**, so
  the first merge to `main` publishes an image to GHCR and auto-update goes live.
- Deliverable: a foreground process logging full `SystemState` each tick.

## Phase 3 — MQTT + Home Assistant
- `paho-mqtt` client with LWT/availability and reconnect.
- Publish state topics + Home Assistant discovery payloads.
- Deliverable: entities auto-appear in Home Assistant and update live.

## Phase 4 — Web dashboard
- FastAPI `/api/state` + a single-page live dashboard (temps, ΔT, ΔP, airflow, faults).
- Deliverable: browse to `http://<pi>:8000` and watch live readings.

## Phase 5 — Productionize
- Deploy via Docker on the Pi (`deploy/docker-compose.yml`); confirm Watchtower
  auto-update end-to-end (merge a PR → image publishes → Pi updates → `/api/version`
  shows the new build). Alternatively the `systemd` + `install.sh` bare-metal path.
- Graceful degradation on sensor loss.
- Optional: enable the HAT hardware watchdog.
- Deliverable: survives reboot and single-sensor unplug without manual
  intervention, and self-updates from `main`.

## Phase 6 — Thermostat call signals (deferred feature)
- Add 24 VAC pilot relays for **W / Y / G** → HAT opto inputs 2/3/4
  (wiring in [hardware.md §7](hardware.md#7-future-thermostat-call-signals-24-vac)).
- Read call state; enable the `airflow_no_call` fault and per-mode ΔT bands.
- Publish Heat/Cool/Fan binary sensors to Home Assistant.

## Later / nice-to-have
- °F/°C toggle already in config; expose per-entity in dashboard.
- Per-mode expected-ΔT tables (heat vs cool) for smarter fault detection.
- Optional local history (SQLite/InfluxDB) if not relying solely on Home Assistant.
- Optional HVAC control experiments using the HAT relays / 0–10 V outputs (out of current scope).
