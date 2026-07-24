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

FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app

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

VOLUME ["/data"]      # config.yaml + persisted state survive updates/reboots
EXPOSE 8000           # web dashboard + REST API

CMD ["python", "-m", "ac_monitor"]
