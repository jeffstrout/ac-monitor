# AC Monitor

HVAC monitoring for a residential air handler, built on a **Raspberry Pi 3B+** and a
**Sequent Microsystems Home Automation HAT**. The system reads duct temperatures,
differential (filter/coil) pressure, and an airflow sail switch, then serves a live web
dashboard and publishes everything to **Home Assistant** over MQTT.

> **Status:** 📐 Design phase. This repository currently contains the hardware design,
> wiring plan, software architecture, and MQTT/Home Assistant integration spec. No
> application code has been written yet — see the [roadmap](docs/roadmap.md).

---

## What it does

- **4× temperature probes** (DS18B20, 1-Wire) — return air, supply air, coil/outdoor, spare
- **Differential pressure** (Sensirion SDP810, I²C) — filter/coil pressure drop
- **Airflow proof** (sail switch) — confirms air is actually moving in the duct
- **Derived metrics** — coil ΔT (return − supply), filter loading, airflow-vs-call faults
- **Web dashboard** — live readings in the browser
- **Home Assistant** — auto-discovered sensors & binary sensors via MQTT
- **Hands-off updates** — merge a PR and the Pi self-updates via GHCR + Watchtower ([details](docs/auto-update.md))

Planned for a later phase: reading the thermostat's call-for-heat / call-for-cool / fan
signals through the HAT's opto-isolated inputs (see the [roadmap](docs/roadmap.md)).

## Hardware at a glance

| Signal | Sensor | Interface | Terminates on |
|---|---|---|---|
| 4× duct/coil temps | DS18B20 | 1-Wire (multidrop) | HAT 1-Wire port |
| Airflow proof | Sail switch (dry contact) | Contact closure | HAT opto input 1 |
| Differential pressure | Sensirion SDP810 | I²C | Pi I²C header* |

\* The SDP810 is a digital I²C sensor and cannot terminate on the HAT's screw
terminals — it shares the Pi's I²C bus with the HAT. See [docs/hardware.md](docs/hardware.md).

## Documentation

- **[Hardware & wiring](docs/hardware.md)** — bill of materials, I/O map, wiring, 1-Wire & I²C setup
- **[Software design](docs/software-design.md)** — architecture, modules, config schema, deployment
- **[MQTT & Home Assistant](docs/mqtt-homeassistant.md)** — topic tree and auto-discovery spec
- **[Auto-update](docs/auto-update.md)** — GHCR + Watchtower hands-off deployment
- **[Roadmap](docs/roadmap.md)** — phased implementation plan
- **[Vendor docs](Hardware%20Documentation/)** — Sequent HAT user guide, Sensirion SDP8xx datasheet

## Target platform

- Raspberry Pi 3B+, Raspberry Pi OS (64-bit, Bookworm or newer)
- Sequent Microsystems Home Automation HAT (stack level 0) —
  [ioplus-rpi](https://github.com/SequentMicrosystems/ioplus-rpi)
- Python 3.11+

## License

MIT © Jeff Strout — see [LICENSE](LICENSE).
