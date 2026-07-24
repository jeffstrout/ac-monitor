# syntax=docker/dockerfile:1
#
# Single-container image for AC Monitor. Runs the Python service that polls the
# sensors, serves the web dashboard + API, and publishes to MQTT.
#
# Build this on CI (GitHub Actions) or a capable machine, NOT on the Pi 3B+.
# The Pi only runs the slim final image and auto-updates via Watchtower
# (see deploy/docker-compose.yml). python:3.11-slim-bookworm is multi-arch, so
# the same Dockerfile produces a Pi-native (arm64) image.
#
# Build context is the repo root:  docker build -f deploy/Dockerfile .

# --- stage 1: compile the Sequent `ioplus` CLI ------------------------------
# The app reads the HAT by shelling out to `ioplus` (the interface verified on
# the bench). It's a small C program, not a pip package, so we build it here and
# copy just the binary into the slim runtime.
FROM python:3.11-slim-bookworm AS ioplus-builder
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && git clone --depth 1 https://github.com/SequentMicrosystems/ioplus-rpi.git /src \
    && make -C /src install

# --- stage 2: runtime -------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app

# The compiled ioplus CLI (used via subprocess by ac_monitor/hat.py).
COPY --from=ioplus-builder /usr/local/bin/ioplus /usr/local/bin/ioplus

# Build provenance baked into the image so the running app can report which
# build it is (GET /api/version). CI passes these on every push to main; they
# default to "dev"/"unknown" for local builds.
ARG GIT_SHA=dev
ARG BUILD_TIME=unknown
ENV APP_COMMIT=$GIT_SHA
ENV APP_BUILD_TIME=$BUILD_TIME
LABEL org.opencontainers.image.source="https://github.com/jeffstrout/ac-monitor" \
      org.opencontainers.image.revision="$GIT_SHA" \
      org.opencontainers.image.created="$BUILD_TIME"

# tzdata so the dashboard/logs can honor a local timezone via TZ (slim base is
# UTC-only). i2c-tools is handy for in-container debugging of the HAT over I2C.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata i2c-tools \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    CONFIG_FILE=/data/config.yaml

# Install Python deps first (cached unless requirements change).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application package (created in Phase 2 — see docs/software-design.md).
COPY ac_monitor ./ac_monitor

# config.yaml + persisted state survive updates/reboots
VOLUME ["/data"]
# web dashboard + REST API
EXPOSE 8000

CMD ["python", "-m", "ac_monitor"]
