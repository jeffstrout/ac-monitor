# AC Monitor Appliance — Build Plan

Plan for the first running application: a **dedicated appliance** on a Raspberry Pi 3B+ +
Sequent Home Automation HAT that reads the sensors, serves a small web control panel, pushes
readings to a split-flap display, and publishes to MQTT — deployed with hands-off
auto-update, mirroring the [split-flap](https://github.com/jeffstrout/split-flap) project.

> **Scope:** this is a plan, not code. It collapses roadmap Phases 2–5 into one focused
> appliance. Nothing else runs on the Pi.

## 1. I/O map (as-wired)

| Channel | Terminal | Signal | Role |
|---|---|---|---|
| Analog 1 | `AD1` | supply air temp | `output_air` |
| Analog 2 | `AD2` | return air temp | `input_air` |
| Analog 3 | `AD3` | suction line temp | `suction_line` |
| Analog 4 | `AD4` | liquid line temp | `liquid_line` |
| Opto 5 | `OPTO-5` | fan running / idle | `fan` |

Headline metric: **air-side ΔT = input − output = AD2 − AD1** (positive in cooling).

Each thermistor wires `ADx` → that connector's `GND` (pin 1); the HAT's internal 15 kΩ
pull-up forms the divider. Conversion + calibration: see [calibration.md](calibration.md).
*(Open: confirm whether OPTO-5 is the sail switch relocated or a thermostat G/fan signal.)*

## 2. Raspberry Pi base config (one-time)

The appliance assumes this host setup. Captured here so a rebuild is repeatable; the runtime
app talks to the HAT over I²C, but `ioplus` is the tool for bring-up and manual testing.

**OS:** Raspberry Pi OS 64-bit (Bookworm or newer). HAT at **stack level 0** (all DIP
switches OFF).

**1. Enable I²C** (the HAT is driven entirely over I²C):
```bash
sudo raspi-config nonint do_i2c 0     # enable I2C
sudo apt update && sudo apt install -y i2c-tools git
sudo reboot
```

**2. Install Sequent `ioplus` (for bring-up + manual testing):**
```bash
git clone https://github.com/SequentMicrosystems/ioplus-rpi.git
cd ioplus-rpi && sudo make install
```

**3. Verify the HAT is alive:**
```bash
pinctrl get 2,3          # SDA/SCL must read hi/hi (lo/lo = jammed bus)
i2cdetect -y 1           # HAT answers at 0x28
ioplus 0 board           # prints HW/FW/temp/voltage
```

**4. Test each sensor with `ioplus` (no app needed):**
```bash
ioplus 0 adcrd 1         # AD1 output-air thermistor (volts)
ioplus 0 adcrd 2         # AD2 input-air
ioplus 0 adcrd 3         # AD3 suction line
ioplus 0 adcrd 4         # AD4 liquid line
ioplus 0 optrd 5         # OPTO-5 fan (1 = closed/running, 0 = open/idle)
ioplus 0 optrd           # all 8 opto inputs as a bitmask
```
Convert a thermistor voltage to °C with the [calibration.md](calibration.md) math to sanity
-check against room temperature. Full bring-up sequence + failure signatures: [hardware.md](hardware.md).

**5. Docker** (the appliance runs as a container — see §4):
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER          # re-login after this
```

**6. Known risk — I²C bus lockup.** The HAT intermittently latches the bus and needs a power
cycle (see [i2c-lockup.md](i2c-lockup.md)). Mitigations for the appliance: the HAT hardware
watchdog (auto power-cycle on hang) and graceful read-failure handling in the app (§6). A
lower I²C baud may help: `dtparam=i2c_arm_baudrate=10000` in `/boot/firmware/config.txt`.

## 3. Application architecture (Python 3.11, FastAPI)

```
ac_monitor/
├── __main__.py     # start web server + background poller  (python -m ac_monitor)
├── config.py       # load/save config.yaml on the /data volume
├── hat.py          # read AD1–AD4 (volts→°C) + OPTO-5 via ioplus/SMioplus; --selftest
├── display.py      # push readings to split-flap slot 2
├── mqtt_out.py     # publish state + Home Assistant discovery + LWT availability
├── state.py        # in-memory latest readings + runtime toggles
├── version.py      # APP_COMMIT / APP_BUILD_TIME → /api/version
└── web/
    ├── app.py      # FastAPI: control panel, /api/state, /api/version, /healthz
    └── static/     # single-page control panel (HTML/CSS/JS)
```

The **poller** ticks every `poll_interval_s` (default 5 s): read all channels concurrently,
convert + calibrate, recompute ΔT + faults, update in-memory state, then (if enabled) push to
the display and publish to MQTT. A failing HAT read marks that channel `healthy: false`
instead of crashing the loop.

## 4. Deployment (split-flap pattern — dedicated appliance)

Mirrors split-flap's production setup ([auto-update.md](auto-update.md) already scaffolds it):

- **GHCR + Watchtower.** CI builds a multi-arch image on merge to `main` and publishes to
  `ghcr.io/jeffstrout/ac-monitor:latest`; Watchtower on the Pi polls (~20 min) and recreates
  the container when a newer image lands. The Pi **pulls**, never builds (a 3B+ can't).
- **Public GHCR image** so the Pi pulls with no credentials. No secrets are in the image —
  they live only in the Pi's `/data/config.yaml`.
- **`restart: unless-stopped`** → the app auto-starts on reboot.
- **`/data` volume** persists `config.yaml` (calibration + toggles) across updates/reboots;
  `.env` for host overrides (broker, TZ, display IP).
- **`privileged: true`** for I²C device access (dedicated Pi, acceptable).
- **`/api/version`** reports the running build (confirm an auto-update landed).
- **`PI-SETUP.md`** documents the one-time bootstrap (mirrors split-flap's).

Writing `ac_monitor/__main__.py` **unguards the CI build**, so the pipeline goes live on the
first merge.

## 5. Web control panel (like split-flap's `/setup`)

Single page served by FastAPI:

- **Live readings** — 4 temps, ΔT, fan status, per-channel health, I²C status.
- **Toggle — display push → slot 2** at `192.168.0.17` (`POST /api/screens/2`). On/off,
  persisted.
- **Toggle — MQTT output** on/off, persisted.
- **Calibration editor — per-channel gain/offset with capture helpers.** For each of AD1–AD4:
  editable `gain`/`offset`, plus **"Capture in ice (0 °C)"** and **"Capture at boiling
  (100 °C, altitude-adjusted)"** buttons that record the live raw volts at that known
  temperature and compute the two-point correction for that channel automatically. Manual
  fine-tune afterward. Saved to `/data/config.yaml`.
- **`/api/version`** shown in the footer.

## 6. I²C-lockup resilience

Because the HAT can latch its bus (see [i2c-lockup.md](i2c-lockup.md)):

- Each HAT read is guarded (timeout + try/except); a failure flags the channel unhealthy and
  is surfaced on the dashboard/display, not fatal.
- MQTT availability uses an LWT so Home Assistant shows the device offline if the app or Pi
  drops.
- **Enable the HAT hardware watchdog** on the appliance: it power-cycles the Pi+HAT if the app
  stops petting it, turning a lockup into a self-healing reboot. The app pets it each poll
  tick. (Must be verified against the actual lockup — see i2c-lockup.md.)

## 7. Build sequence (one PR per step)

1. **Config + I/O map** — `config.py` + `config.example.yaml` for the as-wired mapping. *(map already updated)*
2. **`hat.py` + `--selftest`** — read all channels calibrated; verify on the Pi against `ioplus`.
3. **Poller + `/api/state` + minimal dashboard** — live readings in a browser.
4. **Control panel** — the two toggles + calibration editor with capture helpers.
5. **`display.py`** — push to slot 2 + its toggle.
6. **`mqtt_out.py`** — publish + HA discovery + its toggle.
7. **Dockerize + unguard CI + Watchtower + `PI-SETUP.md` + HAT watchdog.**

## 8. Open items to confirm

- **OPTO-5 semantics** — sail switch relocated to OPTO-5, or a thermostat **G/fan** signal via
  a 24 VAC pilot relay? Affects the `fan` role and the `no_airflow` fault. (hardware.md §5/§6
  + wiring diagram update once confirmed.)
- **MQTT broker** — host/port/credentials (`.env` + control panel).
- **Cadences** — poll interval (5 s?) and display-refresh interval (30 s?).
- **Image/repo visibility** — public GHCR image (repo stays private) vs. public repo.
- **Filter loading** — dropped with the pressure sensor; reintroduce later (air-temp spread or
  a re-added ΔP sensor) or leave out.
