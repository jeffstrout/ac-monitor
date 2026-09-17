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

On the live appliance browse to **http://acmonitor.strout.us**
(`192.168.0.69`) for the dashboard/control panel, and confirm the running
build with `curl http://acmonitor.strout.us/api/version`. On a fresh Pi
before DNS/reservation is set, use `http://<pi-ip>` instead.

## 4. Make the GHCR image public (first time only)

So the Pi pulls without credentials, set the container package to public:
GitHub → your profile → **Packages** → `ac-monitor` → **Package settings** →
**Change visibility → Public**. (The repo is already public; the package
visibility is separate.)

## 5. Auto-update

Watchtower (in the compose file) polls GHCR ~every 20 min and recreates the
container when a newer `:latest` is published — so **merging a PR to `main`
rolls out to the Pi hands-off**. Verify a rollout landed with `/api/version`.

## 6. Host hardening (Wi-Fi reliability)

This appliance is **Wi-Fi-only** (hardwire Ethernet is not available). Pis that
run Wi-Fi only can wedge the `brcmfmac` stack so ping/SSH fail (ARP shows
"Host is down") while Docker and the app keep running. Install persistent
journals plus a gateway reboot watchdog from this repo — that is the recovery
path:

```bash
cd ~/ac-monitor
git pull
sudo ./deploy/host/install-host-hardening.sh
```

Watchtower does **not** apply this. Host units live on the Pi, not in the
container image; run the install script from a checkout after merge (and again
if the units change). The script is idempotent.

Details and verify commands: [deploy/host/README.md](deploy/host/README.md).

Quick checks:

```bash
systemctl status gateway-watchdog.timer
cat /var/lib/gateway-watchdog/fail_count          # 0 when the gateway answers
journalctl --list-boots                          # previous boots after a reboot
sudo tail /var/log/gateway-watchdog.log          # failures / recovery / reboot
```

The watchdog pings the default gateway every minute and reboots only after
**five** consecutive failures (~five minutes), so brief blips do not loop-reboot.

This is separate from the optional HAT I²C watchdog in §7 (`ac_monitor/watchdog.py`).

## 7. HAT hardware watchdog (optional)

For unattended reliability when the *app* hangs, enable the Sequent HAT
watchdog so a hang auto-recovers via power cycle. In the web control panel or
`config.yaml`:

```yaml
watchdog:
  enabled: true
  period_s: 120
```

⚠ Verify it actually recovers the I²C **lockup** on your hardware before relying
on it — see [docs/i2c-lockup.md](docs/i2c-lockup.md). (If lockups persist, a
Pi 5 — different I²C silicon — likely eliminates them at the source.)

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard / SSH unreachable; ARP "Host is down"; Docker still running if you can see the box locally | Wi-Fi stack likely wedged — power-cycle; then install [host hardening](deploy/host/README.md) so the gateway watchdog reboots after ~5 minutes of lost gateway |
| Device didn't auto-update | Check `docker compose logs watchtower`; force with `docker compose pull && docker compose up -d`; confirm `/api/version` |
| `docker: permission denied` | You skipped the log-out/in after `usermod -aG docker` |
| Page unreachable from another device but the Pi is up | Use the Pi's IP; check `docker compose ps` shows `Up` |
