"""FastAPI app: dashboard + /api/state, /api/version, /healthz.

The poller runs as a background task started in the app lifespan; an initial
synchronous poll populates state so the first page load has data.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Config
from ..hat import HatBackend, IoplusBackend
from ..poller import poll_loop, poll_once
from ..state import AppState
from ..version import get_version

_DASHBOARD = """<!doctype html>
<title>AC Monitor</title>
<style>
 body{font:15px system-ui,sans-serif;margin:2rem;max-width:640px}
 h1{font-size:1.3rem} table{border-collapse:collapse;width:100%}
 td,th{padding:.35rem .6rem;border-bottom:1px solid #ddd;text-align:left}
 .big{font-size:2rem;font-weight:600} .fault{color:#b00} .ok{color:#080}
 .muted{color:#888} .fail{color:#b00}
</style>
<h1>AC Monitor</h1>
<p>Air-side ΔT: <span class="big" id="dt">–</span> <span id="mode" class="muted"></span></p>
<table id="temps"></table>
<p>Fan: <b id="fan">–</b> &nbsp; Bus: <b id="bus">–</b></p>
<p id="faults"></p>
<p class="muted" id="foot"></p>
<script>
async function tick(){
 let s; try{ s = await (await fetch('/api/state')).json(); }catch(e){ return; }
 const u = s.unit;
 document.getElementById('dt').textContent = s.delta_t==null?'–':s.delta_t.toFixed(1)+'°'+u;
 document.getElementById('mode').textContent = s.mode?('('+s.mode+')'):'';
 const rows = Object.entries(s.temps).map(([k,v])=>{
   const ok = s.health[k];
   const val = ok&&v!=null? v.toFixed(1)+'°'+u : '<span class=fail>FAIL</span>';
   return `<tr><td>${k}</td><td>${val}</td></tr>`;}).join('');
 document.getElementById('temps').innerHTML = '<tr><th>Channel</th><th>Temp</th></tr>'+rows;
 document.getElementById('fan').textContent = s.fan_running==null?'FAIL':(s.fan_running?'RUNNING':'IDLE');
 document.getElementById('bus').innerHTML = s.i2c_ok?'<span class=ok>OK</span>':'<span class=fault>DOWN</span>';
 const active = Object.entries(s.faults).filter(([k,v])=>v).map(([k])=>k);
 document.getElementById('faults').innerHTML = active.length? '<span class=fault>Faults: '+active.join(', ')+'</span>':'<span class=ok>No faults</span>';
 const v = s.version||{}; const t = s.last_poll_at? new Date(s.last_poll_at*1000).toLocaleTimeString():'–';
 document.getElementById('foot').textContent = `updated ${t} · poll #${s.poll_count} · build ${v.commit||''}`;
}
tick(); setInterval(tick, 2000);
</script>
"""


def create_app(state: AppState, backend: HatBackend | None = None) -> FastAPI:
    backend = backend or IoplusBackend(timeout_s=state.config.poll.interval_s + 2)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:  # one synchronous poll so the first request has data
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

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _DASHBOARD

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        snap = state.snapshot()
        snap["version"] = get_version()
        return JSONResponse(snap)

    @app.get("/api/version")
    def api_version() -> dict:
        return get_version()

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        ok = state.readings is not None and state.readings.i2c_ok
        return JSONResponse({"status": "ok" if ok else "degraded"}, status_code=200 if ok else 503)

    return app


def build_default_app(config_path: str = "config/config.yaml") -> FastAPI:
    """Convenience for ``uvicorn ac_monitor.web.app:app`` in dev."""
    from .. import config as configmod

    try:
        cfg = configmod.load(config_path)
    except configmod.ConfigError:
        cfg = configmod.from_dict({})
    return create_app(AppState(config=cfg))
