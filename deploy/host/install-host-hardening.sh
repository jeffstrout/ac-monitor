#!/bin/bash
# Install persistent journald + gateway watchdog on an AC Monitor Pi.
# Run on the Pi as a sudo-capable user from a checkout of this repo:
#   sudo ./deploy/host/install-host-hardening.sh
#
# Idempotent — safe to re-run. This is a host-side install (systemd units +
# journald drop-in). Watchtower does not apply it; the Docker image is unchanged.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "→ Persistent journal"
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
install -d /etc/systemd/journald.conf.d
install -m 644 "$ROOT/journald-persistent.conf" /etc/systemd/journald.conf.d/persistent.conf
systemctl restart systemd-journald

echo "→ Gateway watchdog"
install -m 755 "$ROOT/gateway-watchdog.sh" /usr/local/sbin/gateway-watchdog.sh
install -m 644 "$ROOT/gateway-watchdog.service" /etc/systemd/system/gateway-watchdog.service
install -m 644 "$ROOT/gateway-watchdog.timer" /etc/systemd/system/gateway-watchdog.timer
systemctl daemon-reload
systemctl enable --now gateway-watchdog.timer
systemctl start gateway-watchdog.service || true

echo "→ Verify"
systemctl is-enabled gateway-watchdog.timer
systemctl status gateway-watchdog.timer --no-pager | head -12 || true
journalctl --list-boots | head -3 || true
echo "Done. This appliance is Wi-Fi-only; the gateway watchdog is the recovery path for a wedged brcmfmac stack."
