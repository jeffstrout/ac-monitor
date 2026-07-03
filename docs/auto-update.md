# Hands-off Auto-Update

AC Monitor uses the same self-updating deployment pattern as
[jeffstrout/split-flap](https://github.com/jeffstrout/split-flap): once it's
running on the Pi, **merging a PR to `main` rolls out to the device
automatically** — no SSH, no manual `git pull`, no rebuild on the Pi.

## How it works

```
   You merge a PR  ─▶  GitHub Actions            ─▶  GHCR (image registry)
   into `main`         builds a multi-arch image     ghcr.io/jeffstrout/
                       and pushes :latest            ac-monitor:latest
                                                              │
                                                              │ Watchtower on the Pi
                                                              │ polls every ~20 min
                                                              ▼
                              running image is now STALE  ─▶  pull + recreate
                              vs the new :latest              the container
```

1. **Build & publish — [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml)**
   On every push to `main` (which is what a merged PR produces), CI builds a
   `linux/amd64` + `linux/arm64` image and pushes it to GHCR as
   `ghcr.io/jeffstrout/ac-monitor:latest` (plus a `sha-<short>` tag). The git SHA
   and build timestamp are baked into the image as `APP_COMMIT` / `APP_BUILD_TIME`.

2. **Detect staleness & update — [`deploy/docker-compose.yml`](../deploy/docker-compose.yml)**
   [Watchtower](https://containrrr.dev/watchtower/) runs alongside the app on the
   Pi. It polls GHCR on `WATCHTOWER_POLL_INTERVAL` (default 1200 s ≈ 20 min). When
   the digest of `:latest` differs from the running container's image, it pulls
   the new image and recreates the container in place. The `/data` volume
   (config + persisted state) survives the swap. `WATCHTOWER_LABEL_ENABLE` scopes
   it to only the `ac-monitor` container.

3. **Confirm what's running — `GET /api/version`**
   The app serves its build provenance so you can verify an update landed:
   ```json
   { "commit": "9f3c1a2", "build_time": "2026-07-03T18:04:11Z" }
   ```

## "Stale" = running image older than the published one

Watchtower compares image **digests**, not timestamps, so there's no clock
dependency — if the `:latest` tag on GHCR points at a newer image than the one
the container was started from, it's considered stale and gets updated. Pushing
to `main` is the only thing that moves `:latest`.

## First-time setup on the Pi

Prerequisites: Docker + the Docker Compose plugin, and I²C + 1-Wire enabled on
the host (see [hardware.md](hardware.md)). Then:

```bash
git clone https://github.com/jeffstrout/ac-monitor.git
cd ac-monitor/deploy
cp .env.example .env            # optional: set TZ, HOST_PORT, poll interval
# put your real config at the volume path (see docs/software-design.md §7)
docker compose pull && docker compose up -d
```

From then on the display self-updates. To force an immediate update instead of
waiting for the poll:

```bash
cd ac-monitor/deploy
docker compose pull && docker compose up -d
```

## Hardware access note

Unlike split-flap (pure software), AC Monitor talks to real hardware, so the
container runs `privileged` to reach the I²C bus (`/dev/i2c-1`, used by both the
HAT and the SDP810) and the host's `/sys/bus/w1` tree (DS18B20 probes). On a
dedicated monitoring Pi this is an acceptable trade-off; it can be tightened to
explicit `devices:`/bind-mounts later.

## Status

The deploy scaffolding (this doc, the workflow, the compose file, the Dockerfile)
is in place now. The CI image build is **guarded** — it is skipped until the
`ac_monitor/` Python package exists (Phase 2), so the workflow stays green in the
meantime. Once the app and its `/api/version` endpoint land, auto-update is live
with no further wiring. See the [roadmap](roadmap.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Device didn't auto-update | `docker compose logs watchtower`; force with `docker compose pull && docker compose up -d`; confirm the running build at `/api/version` |
| Watchtower logs "client version 1.25 is too old" | Ensure `DOCKER_API_VERSION` is set on the watchtower service (it is, default 1.40) |
| `/api/version` shows the new build but behavior is unchanged | A browser tab may be caching the old dashboard — reload it |
| CI shows no image published | Expected until `ac_monitor/__main__.py` exists (build is guarded) |
