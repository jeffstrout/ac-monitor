# Host hardening (Raspberry Pi)

This appliance is **Wi-Fi-only** — hardwire Ethernet is not available. Pis
using `brcmfmac` can stop answering ping/SSH while Docker and the AC Monitor
app keep running. These host units make the next failure debuggable and
self-recovering.

This is **not** the Sequent HAT I²C watchdog (`ac_monitor/watchdog.py`). That
pets the HAT from inside the container. The gateway watchdog is a **host**
systemd timer that reboots the Pi when the LAN default gateway is unreachable.

| Piece | What it does |
| --- | --- |
| Persistent `journald` | Keeps logs across reboots (`SystemMaxUse=200M`) |
| Gateway watchdog | Pings the default gateway every minute; reboots after **5** consecutive failures (~5 minutes) |

## Install (on the Pi)

Watchtower does **not** install these units. From a git checkout of this repo:

```bash
sudo ./deploy/host/install-host-hardening.sh
```

The script is idempotent (safe to re-run).

## Verify

```bash
systemctl status gateway-watchdog.timer
cat /var/lib/gateway-watchdog/fail_count    # should be 0 when healthy
journalctl --list-boots                    # previous boots appear after a reboot
sudo tail -f /var/log/gateway-watchdog.log # only written on failures / recovery
```

## Rollback

```bash
sudo systemctl disable --now gateway-watchdog.timer
sudo rm -f /etc/systemd/system/gateway-watchdog.service \
           /etc/systemd/system/gateway-watchdog.timer \
           /usr/local/sbin/gateway-watchdog.sh \
           /etc/systemd/journald.conf.d/persistent.conf
sudo systemctl daemon-reload
```

Powersave should stay off (NetworkManager `wifi.powersave = 2`); the install
script does not change Wi-Fi config.
