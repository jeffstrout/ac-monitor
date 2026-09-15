#!/bin/bash
# Reboot if default gateway is unreachable for FAIL_LIMIT consecutive checks.
# Intended for Wi-Fi-only AC Monitor Pis where brcmfmac can wedge inbound
# connectivity while Docker and the app keep running.
#
# This is a *host* unit (systemd). It is unrelated to the Sequent HAT I²C
# petter in ac_monitor/watchdog.py.
set -euo pipefail

FAIL_LIMIT="${FAIL_LIMIT:-5}"
STATE_DIR=/var/lib/gateway-watchdog
STATE_FILE="$STATE_DIR/fail_count"
LOG=/var/log/gateway-watchdog.log
LOCK=/run/gateway-watchdog.lock

mkdir -p "$STATE_DIR"
exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

log() { echo "$(date -Is) $*" | tee -a "$LOG" >/dev/null; }

GW=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')
GW="${GW:-192.168.0.1}"

count=0
if [ -f "$STATE_FILE" ]; then
  count=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
fi
case "$count" in
  ''|*[!0-9]*) count=0 ;;
esac

if ping -c 1 -W 3 "$GW" >/dev/null 2>&1; then
  if [ "$count" -gt 0 ]; then
    log "OK: $GW reachable again (was fail_count=$count)"
  fi
  echo 0 > "$STATE_FILE"
  exit 0
fi

count=$((count + 1))
echo "$count" > "$STATE_FILE"
log "FAIL: cannot ping $GW (fail_count=$count/$FAIL_LIMIT)"

if [ "$count" -ge "$FAIL_LIMIT" ]; then
  log "REBOOT: gateway unreachable for $FAIL_LIMIT consecutive checks"
  sync
  /sbin/reboot
fi
