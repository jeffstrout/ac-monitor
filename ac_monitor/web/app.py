"""FastAPI app: dashboard + control panel + JSON API.

Routes:
  GET  /                     dashboard + control panel
  GET  /api/state            latest readings + derived + toggles
  GET  /api/version          build provenance
  GET  /api/calibration      per-channel gain/offset + capture points
  GET  /healthz              200 if the bus is up, else 503
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
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import calibrate
from ..config import Calibration
from ..hat import HatBackend, HatError, IoplusBackend
from ..poller import poll_loop, poll_once
from ..state import AppState
from ..version import get_version
from .page import DASHBOARD


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

    app = FastAPI(title="AC Monitor", lifespan=lifespan)

    def _require_role(role: str) -> None:
        if role not in state.config.thermistors.channels:
            raise HTTPException(status_code=400, detail=f"unknown channel role: {role}")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        snap = state.snapshot()
        snap["version"] = get_version()
        return JSONResponse(snap)

    @app.get("/api/version")
    def api_version() -> dict:
        return get_version()

    @app.get("/api/calibration")
    def api_calibration() -> dict:
        return _calibration_view(state)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        ok = state.readings is not None and state.readings.i2c_ok
        return JSONResponse({"status": "ok" if ok else "degraded"}, status_code=200 if ok else 503)

    @app.post("/api/toggle/display")
    def toggle_display() -> dict:
        state.config.display.enabled = not state.config.display.enabled
        state.persist()
        return {"display_push": state.config.display.enabled}

    @app.post("/api/toggle/mqtt")
    def toggle_mqtt() -> dict:
        if not state.config.mqtt.enabled and not state.config.mqtt.host:
            raise HTTPException(status_code=409, detail="set the MQTT broker host first")
        state.config.mqtt.enabled = not state.config.mqtt.enabled
        state.persist()
        return {"mqtt": state.config.mqtt.enabled}

    @app.post("/api/mqtt/config")
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

    @app.post("/api/calibrate/capture")
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

    @app.post("/api/calibrate/manual")
    def calibrate_manual(req: ManualCalReq) -> dict:
        _require_role(req.role)
        state.config.thermistors.channel_calibration[req.role] = Calibration(req.gain, req.offset)
        state.persist()
        return {"role": req.role, "gain": req.gain, "offset": req.offset}

    @app.post("/api/calibrate/reset")
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
