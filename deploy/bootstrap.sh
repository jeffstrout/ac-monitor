#!/usr/bin/env bash
#
# AC Monitor appliance bootstrap: fresh Raspberry Pi OS (Bookworm/Trixie) -> running.
# Written for a Pi 5 + Sequent Home Automation HAT, but works on any Pi.
#
# Idempotent — safe to re-run (it skips what's already done). If it enables I2C
# on a fresh install it will ask for one reboot, then finishes on the re-run.
#
# Fetch + run on the Pi:
#   curl -fsSL https://raw.githubusercontent.com/jeffstrout/ac-monitor/main/deploy/bootstrap.sh -o bootstrap.sh
#   bash bootstrap.sh
#
set -euo pipefail

REPO="https://github.com/jeffstrout/ac-monitor.git"
DIR="$HOME/ac-monitor"
CFG="/boot/firmware/config.txt"

say(){ printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

say "AC Monitor bootstrap on $(hostname)"

# 1. Base packages ----------------------------------------------------------
say "Installing base packages (i2c-tools, git, curl)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends i2c-tools git curl ca-certificates

# 2. Enable I2C -------------------------------------------------------------
say "Enabling I2C"
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_i2c 0 || true
fi
if [ -f "$CFG" ] && ! grep -q '^dtparam=i2c_arm=on' "$CFG"; then
  echo 'dtparam=i2c_arm=on' | sudo tee -a "$CFG" >/dev/null
fi
sudo modprobe i2c-dev 2>/dev/null || true

# 3. Docker -----------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  say "Docker already installed"
else
  say "Installing Docker (get.docker.com)"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" || true
fi

# 4. App repo (compose file + docs) -----------------------------------------
say "Fetching the app repo"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone --depth 1 "$REPO" "$DIR"
fi

# 5. The container maps /dev/i2c-1, so it must exist first -------------------
if [ ! -e /dev/i2c-1 ]; then
  say "Reboot required to finish"
  cat <<'EOF'
I2C was just enabled but /dev/i2c-1 isn't present yet (the device-tree change
needs a reboot). Docker and the repo are installed. To finish:

  1) sudo reboot
  2) after it comes back, re-run this same script — it will detect the HAT
     and deploy.
EOF
  exit 0
fi

# 6. Verify the HAT ---------------------------------------------------------
say "I2C buses"
i2cdetect -l || true
say "Scanning bus 1 for the HAT (expect 0x28)"
i2cdetect -y 1 || true
if ! i2cdetect -y 1 2>/dev/null | grep -qw 28; then
  echo "!! HAT not seen at 0x28 on bus 1 — check seating/power."
  echo "!! If it's on a different bus (see 'I2C buses' above), the compose device"
  echo "!! mapping (/dev/i2c-1) needs updating; note the bus number and ask."
fi

# 7. Deploy -----------------------------------------------------------------
say "Deploying (docker compose pull && up -d)"
cd "$DIR/deploy"
[ -f .env ] || cp .env.example .env
sudo docker compose pull
sudo docker compose up -d

# 8. Verify -----------------------------------------------------------------
say "Verifying"
sleep 6
IP="$(hostname -I | awk '{print $1}')"
echo "GET /api/version:"
curl -s "http://127.0.0.1:8000/api/version" \
  || echo "(not answering yet — try: cd $DIR/deploy && sudo docker compose logs ac-monitor)"
echo
say "Done"
echo "Dashboard: http://${IP}:8000"
echo "Logs:      cd $DIR/deploy && sudo docker compose logs -f ac-monitor"
echo "Updates:   merge to main -> Watchtower rolls it out (~20 min)"
