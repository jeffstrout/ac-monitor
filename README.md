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

- **4× temperature probes** (DS18B20, 1-Wire) — suction line, liquid line, input air, output air
- **Differential pressure** (Setra 265 transmitter, 4–20 mA) — filter/coil pressure drop
- **Airflow proof** (sail switch) — confirms air is actually moving in the duct
- **Derived metrics** — air-side ΔT (input − output), refrigerant line temps, filter loading
- **Web dashboard** — live readings in the browser
- **Home Assistant** — auto-discovered sensors & binary sensors via MQTT
- **Hands-off updates** — merge a PR and the Pi self-updates via GHCR + Watchtower ([details](docs/auto-update.md))

Planned for a later phase: reading the thermostat's call-for-heat / call-for-cool / fan
signals through the HAT's opto-isolated inputs (see the [roadmap](docs/roadmap.md)).

## Hardware at a glance

| Signal | Sensor | Interface | Terminates on |
|---|---|---|---|
| 4× line/air temps | DS18B20 | 1-Wire (multidrop) | HAT 1-Wire port |
| Airflow proof | Sail switch (dry contact) | Contact closure | HAT opto input 1 |
| Differential pressure | Setra 265 transmitter | 4–20 mA → ADC (via 150 Ω sense R) | HAT analog input 1 |

**Every field signal lands on the Sequent board.** An earlier revision used an I²C
pressure sensor that had to hang off the Pi header; it's been replaced by the analog
Setra 265, so nothing field-wired touches the Pi header. See [docs/hardware.md](docs/hardware.md).

## Documentation

- **[Hardware & wiring](docs/hardware.md)** — bill of materials, I/O map, wiring, 1-Wire & I²C setup
- **[Software design](docs/software-design.md)** — architecture, modules, config schema, deployment
- **[MQTT & Home Assistant](docs/mqtt-homeassistant.md)** — topic tree and auto-discovery spec
- **[Auto-update](docs/auto-update.md)** — GHCR + Watchtower hands-off deployment
- **[Roadmap](docs/roadmap.md)** — phased implementation plan
- **[Vendor docs](Hardware%20Documentation/)** — Sequent HAT user guide + Setra Model 265
  datasheet (the Sensirion SDP8xx datasheet is from the superseded I²C design)

## Target platform

- Raspberry Pi 3B+, Raspberry Pi OS (64-bit, Bookworm or newer)
- Sequent Microsystems Home Automation HAT (stack level 0) —
  [ioplus-rpi](https://github.com/SequentMicrosystems/ioplus-rpi)
- Python 3.11+

## License

MIT © Jeff Strout — see [LICENSE](LICENSE).
