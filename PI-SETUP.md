# Raspberry Pi setup — AC Monitor appliance

One-time bootstrap for the dedicated Pi. After this, the app runs in Docker,
auto-starts on reboot, and self-updates from GitHub (Watchtower) — you don't
build or install Python on the Pi.

## 0. Prerequisites

- Raspberry Pi OS **64-bit** (Bookworm+), on a Pi with the 40-pin header.
- Sequent **Home Automation HAT** seated at **stack level 0** (all DIP switches OFF).
- Wiring per [docs/hardware.md](docs/hardware.md): thermistors on AD1–AD4, sail switch on OPTO-5.

## 1. Enable I²C + bring-up tools

```bash
sudo raspi-config nonint do_i2c 0
sudo apt update && sudo apt install -y i2c-tools git
sudo reboot
```

Optional but recommended — the Sequent CLI, for manual bring-up/diagnostics:

```bash
git clone https://github.com/SequentMicrosystems/ioplus-rpi.git
cd ioplus-rpi && sudo make install
```

Verify the HAT before deploying:

```bash
i2cdetect -y 1        # card answers at 0x28
ioplus 0 board        # HW/FW/temp/voltage
```

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"     # then log out/in
```

## 3. Deploy

The Pi **pulls** a prebuilt multi-arch image from GHCR (it never builds — a 3B+
is too small). Get the compose file and start it:

```bash
git clone https://github.com/jeffstrout/ac-monitor.git
cd ac-monitor/deploy
cp .env.example .env         # optional: set TZ, HOST_PORT, etc.
docker compose pull
docker compose up -d
```

`restart: unless-stopped` makes it **auto-start on reboot**. Config + state live
in the `ac-monitor-data` volume (`/data/config.yaml`) and survive updates; on
first run a default `config.yaml` (the as-wired mapping) is seeded — edit it from
the web control panel.

Browse to **http://<pi-ip>:8000** for the dashboard/control panel, and confirm
the running build with `curl http://<pi-ip>:8000/api/version`.

## 4. Make the GHCR image public (first time only)

So the Pi pulls without credentials, set the container package to public:
GitHub → your profile → **Packages** → `ac-monitor` → **Package settings** →
**Change visibility → Public**. (The repo is already public; the package
visibility is separate.)

## 5. Auto-update

Watchtower (in the compose file) polls GHCR ~every 20 min and recreates the
container when a newer `:latest` is published — so **merging a PR to `main`
rolls out to the Pi hands-off**. Verify a rollout landed with `/api/version`.

## 6. HAT hardware watchdog (optional)

For unattended reliability, enable the HAT watchdog so a hang auto-recovers via
power cycle. In the web control panel or `config.yaml`:

```yaml
watchdog:
  enabled: true
  period_s: 120
```

⚠ Verify it actually recovers the I²C **lockup** on your hardware before relying
on it — see [docs/i2c-lockup.md](docs/i2c-lockup.md). (If lockups persist, a
Pi 5 — different I²C silicon — likely eliminates them at the source.)
