"""FastAPI app: dashboard + control panel + JSON API.

Routes:
  GET  /                     dashboard + control panel
  GET  /api/docs             the shared fleet API reference page
  GET  /api/state            latest readings + derived + toggles
  GET  /api/version          build provenance
  GET  /api/calibration      per-channel gain/offset + capture points
  GET  /api/health           200 if the bus is up, else 503 (contract endpoint)
  GET  /healthz              deprecated alias for /api/health
  POST /api/toggle/display   flip display-push on/off
  POST /api/toggle/mqtt      flip MQTT output on/off
  POST /api/mqtt/config      set broker host/port/user/pass
  POST /api/calibrate/capture  record a reading at a known temperature
  POST /api/calibrate/manual   set gain/offset directly
  POST /api/calibrate/reset    clear a channel's calibration

The poller runs as a background task started in the app lifespan.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import calibrate
from ..config import Calibration
from ..hat import HatBackend, HatError, IoplusBackend
from ..poller import poll_loop, poll_once
from ..state import AppState
from ..version import get_version
from . import api_docs
from .page import DASHBOARD


_STARTED_AT = time.time()


class CaptureReq(BaseModel):
    role: str
    known_c: float


class ManualCalReq(BaseModel):
    role: str
    gain: float
    offset: float


class RoleReq(BaseModel):
    role: str


class MqttCfgReq(BaseModel):
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None


def _calibration_view(state: AppState) -> dict:
    t = state.config.thermistors
    out = {}
    for role in t.channels:
        cal = t.calibration_for(role)
        out[role] = {
            "gain": cal.gain,
            "offset": cal.offset,
            "custom": role in t.channel_calibration,
            "captures": state.captures.get(role, []),
        }
    return out


def create_app(state: AppState, backend: HatBackend | None = None) -> FastAPI:
    backend = backend or IoplusBackend(timeout_s=state.config.poll.interval_s + 2)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await asyncio.to_thread(poll_once, state, backend)
        except Exception:
            pass
        stop = asyncio.Event()
        task = asyncio.create_task(poll_loop(state, backend, stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()

    # The description and tags are not decoration: /api/docs renders the shared
    # fleet reference page from this schema (homelab-standards#10), so anything
    # not declared here is missing from the docs. Swagger used to hide that
    # behind its own chrome — the app declared a bare title= and 13 untagged,
    # unsummarised routes.
    app = FastAPI(
        title="AC Monitor",
        version="1.0.0",
        description=(
            "Reads four thermistors on a Raspberry Pi HAT and reports air-side "
            "**ΔT** — return air minus supply air — which is the number that "
            "tells you whether an air conditioner is actually cooling.\n\n"
            "Temperatures are served in the unit the panel is configured for; "
            "calibration always speaks **Celsius**. All endpoints are "
            "unauthenticated and intended for use on a trusted LAN."
        ),
        openapi_tags=[
            {
                "name": "telemetry",
                "description": "Current readings, derived state and faults.",
            },
            {
                "name": "outputs",
                "description": (
                    "Toggle where readings are published and configure the broker. "
                    "Changes persist across restarts."
                ),
            },
            {
                "name": "calibration",
                "description": (
                    "Fit each channel's gain and offset from known-temperature "
                    "captures. Two well-separated points fit a channel."
                ),
            },
            {"name": "health", "description": "Liveness and build provenance."},
        ],
        lifespan=lifespan,
    )

    def _require_role(role: str) -> None:
        if role not in state.config.thermistors.channels:
            raise HTTPException(status_code=400, detail=f"unknown channel role: {role}")

    # Shared design tokens (homelab-standards), vendored into the image rather
    # than fetched — the LAN guarantees nothing, including itself.
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> str:
        return DASHBOARD

    @app.get("/api/state", tags=["telemetry"], summary="Everything the panel renders")
    def api_state() -> JSONResponse:
        snap = state.snapshot()
        snap["version"] = get_version()
        return JSONResponse(snap)

    @app.get("/api/version", tags=["health"], summary="Running build")
    def api_version() -> dict:
        return get_version()

    @app.get("/api/calibration", tags=["calibration"], summary="Per-channel gain, offset and captures")
    def api_calibration() -> dict:
        return _calibration_view(state)

    @app.get("/api/health", tags=["health"], summary="Health check")
    def health() -> JSONResponse:
        """Health per the homelab appliance contract.

        `degraded` matters: the app can be serving traffic while the I2C bus is
        down, which means it is alive but not doing its job. Liveness alone would
        report that as healthy.
        """
        ok = state.readings is not None and state.readings.i2c_ok
        return JSONResponse(
            {
                "status": "ok" if ok else "degraded",
                "version": get_version().get("commit", "dev"),
                "uptime_seconds": round(time.time() - _STARTED_AT),
                "i2c_ok": bool(state.readings and state.readings.i2c_ok),
            },
            status_code=200 if ok else 503,
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        """Deprecated alias for /api/health — kept so existing probes don't break."""
        return health()

    @app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
    def api_reference() -> str:
        """The fleet's shared API reference page.

        What the shell header's `API docs` link points at on every appliance.
        FastAPI's `/docs` stays mounted for interactive use, but it fetches
        swagger-ui from a CDN and renders an empty shell on a LAN with no route
        out — which is when you reach for it, in a plant room, because
        something is wrong (jeffstrout/homelab-standards#7).

        Rendered from `app.openapi()`, so it cannot drift from the routes.
        """
        return api_docs.render(
            app, version=get_version(), back=("/", "Control panel")
        )

    @app.post("/api/toggle/display", tags=["outputs"], summary="Toggle the split-flap push")
    def toggle_display() -> dict:
        state.config.display.enabled = not state.config.display.enabled
        state.persist()
        return {"display_push": state.config.display.enabled}

    @app.post("/api/toggle/mqtt", tags=["outputs"], summary="Toggle MQTT publishing")
    def toggle_mqtt() -> dict:
        if not state.config.mqtt.enabled and not state.config.mqtt.host:
            raise HTTPException(status_code=409, detail="set the MQTT broker host first")
        state.config.mqtt.enabled = not state.config.mqtt.enabled
        state.persist()
        return {"mqtt": state.config.mqtt.enabled}

    @app.post("/api/toggle/relaytest", tags=["outputs"], summary="Toggle the relay self-test")
    def toggle_relaytest() -> dict:
        state.config.relay_selftest.enabled = not state.config.relay_selftest.enabled
        state.persist()
        return {"relay_test": state.config.relay_selftest.enabled}

    @app.post("/api/mqtt/config", tags=["outputs"], summary="Set the MQTT broker")
    def mqtt_config(req: MqttCfgReq) -> dict:
        m = state.config.mqtt
        if req.host is not None:
            m.host = req.host
        if req.port is not None:
            m.port = req.port
        if req.username is not None:
            m.username = req.username
        if req.password is not None:
            m.password = req.password
        state.persist()
        return {"host": m.host, "port": m.port, "username": m.username, "enabled": m.enabled}

    @app.post("/api/calibrate/capture", tags=["calibration"], summary="Capture a known-temperature point (°C)")
    def calibrate_capture(req: CaptureReq) -> dict:
        _require_role(req.role)
        v = state.readings.volts.get(req.role) if state.readings else None
        if v is None:
            raise HTTPException(status_code=409, detail=f"{req.role} has no current reading")
        try:
            raw_c = calibrate.raw_celsius(v, state.config.thermistors)
        except HatError as e:
            raise HTTPException(status_code=409, detail=str(e))
        pts = state.captures.setdefault(req.role, [])
        pts.append((round(req.known_c, 3), round(raw_c, 3)))
        result: dict = {"role": req.role, "captures": pts}
        if len(pts) >= 2:
            try:
                cal = calibrate.fit(pts)
            except ValueError as e:
                result["error"] = str(e)
            else:
                state.config.thermistors.channel_calibration[req.role] = cal
                state.persist()
                result["calibration"] = {"gain": cal.gain, "offset": cal.offset}
        return result

    @app.post("/api/calibrate/manual", tags=["calibration"], summary="Set gain and offset directly")
    def calibrate_manual(req: ManualCalReq) -> dict:
        _require_role(req.role)
        state.config.thermistors.channel_calibration[req.role] = Calibration(req.gain, req.offset)
        state.persist()
        return {"role": req.role, "gain": req.gain, "offset": req.offset}

    @app.post("/api/calibrate/reset", tags=["calibration"], summary="Drop a channel's calibration")
    def calibrate_reset(req: RoleReq) -> dict:
        _require_role(req.role)
        state.captures.pop(req.role, None)
        state.config.thermistors.channel_calibration.pop(req.role, None)
        state.persist()
        cal = state.config.thermistors.calibration
        return {"role": req.role, "gain": cal.gain, "offset": cal.offset, "custom": False}

    return app


def build_default_app(config_path: str = "config/config.yaml") -> FastAPI:
    """Convenience for ``uvicorn ac_monitor.web.app:build_default_app --factory``."""
    from .. import config as configmod

    try:
        cfg = configmod.load(config_path)
        path = config_path
    except configmod.ConfigError:
        cfg = configmod.from_dict({})
        path = None
    return create_app(AppState(config=cfg, config_path=path))
