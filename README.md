# AC Monitor

HVAC monitoring for a residential air handler, built on a **Raspberry Pi 3B+** and a
**Sequent Microsystems Home Automation HAT**. The system reads duct/refrigerant-line
temperatures and an airflow sail switch, then serves a live web dashboard and publishes
everything to **Home Assistant** over MQTT.

> **Status:** 📐 Design phase. This repository currently contains the hardware design,
> wiring plan, software architecture, and MQTT/Home Assistant integration spec. No
> application code has been written yet — see the [roadmap](docs/roadmap.md).

---

## What it does

- **4× temperature probes** (10 kΩ NTC thermistors on HAT analog inputs) — suction line, liquid line, input air, output air
- **Airflow proof** (sail switch) — confirms air is actually moving in the duct
- **Derived metrics** — air-side ΔT (input − output) and refrigerant line temps
- **Web dashboard** — live readings in the browser
- **Home Assistant** — auto-discovered sensors & binary sensors via MQTT
- **Hands-off updates** — merge a PR and the Pi self-updates via GHCR + Watchtower ([details](docs/auto-update.md))

Planned for a later phase: reading the thermostat's call-for-heat / call-for-cool / fan
signals through the HAT's opto-isolated inputs (see the [roadmap](docs/roadmap.md)).

## Hardware at a glance

| Signal | Sensor | Interface | Terminates on |
|---|---|---|---|
| 4× line/air temps | 10 kΩ NTC thermistor (DROK B3950) | Analog voltage divider | HAT analog inputs AD1–AD4 |
| Airflow proof | Sail switch (dry contact) | Contact closure | HAT opto input 1 |

**Every field signal lands on the Sequent board** — nothing field-wired touches the Pi
header. Earlier revisions used DS18B20 1-Wire probes for temperature and a Setra 265
differential-pressure transmitter; both were dropped (the HAT's 1-Wire bus proved
unreliable, and pressure sensing was cut from scope). See [docs/hardware.md](docs/hardware.md)
and [docs/i2c-lockup.md](docs/i2c-lockup.md).

## Documentation

- **[Hardware & wiring](docs/hardware.md)** — bill of materials, I/O map, wiring, HAT bring-up
- **[Thermistor calibration](docs/calibration.md)** — conversion math + field calibration
- **[I²C lockup investigation](docs/i2c-lockup.md)** — the intermittent HAT bus-lockup issue
- **[Software design](docs/software-design.md)** — architecture, modules, config schema, deployment
- **[MQTT & Home Assistant](docs/mqtt-homeassistant.md)** — topic tree and auto-discovery spec
- **[Auto-update](docs/auto-update.md)** — GHCR + Watchtower hands-off deployment
- **[Roadmap](docs/roadmap.md)** — phased implementation plan
- **[Vendor docs](Hardware%20Documentation/)** — Sequent HAT user guide (the Setra 265 and
  Sensirion SDP8xx datasheets are from superseded pressure-sensor designs)

## Target platform

- Raspberry Pi 3B+, Raspberry Pi OS (64-bit, Bookworm or newer)
- Sequent Microsystems Home Automation HAT (stack level 0) —
  [ioplus-rpi](https://github.com/SequentMicrosystems/ioplus-rpi)
- Python 3.11+

## License

MIT © Jeff Strout — see [LICENSE](LICENSE).
