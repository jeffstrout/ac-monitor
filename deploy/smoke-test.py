#!/usr/bin/env python3
"""End-to-end smoke test for the AC Monitor REST API (issue #22).

Unit tests exercise the app with a fake HAT backend. This exercises the *running
appliance* — real I2C, real config file, real persistence — which is the half
that has never been checked automatically.

Run it on the Pi, or against any reachable instance:

    python3 deploy/smoke-test.py                      # http://localhost:8080
    python3 deploy/smoke-test.py http://192.168.0.42:8080

Standard library only, so it runs on the Pi with nothing installed.

SAFETY
------
This test writes. It flips both toggles and rewrites the MQTT broker config, so
it captures the current values first and restores them at the end — including
when an assertion fails, via try/finally.

Calibration *writes* are behind ``--calibration`` and off by default, because
``POST /api/calibrate/reset`` discards capture points that cannot be
reconstructed from the API. With the flag, gain/offset are restored via
``/api/calibrate/manual``; the capture points are still lost, and the script
says so before touching anything.

Exit status is 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 10

_passed = 0
_failed: list[str] = []


def _c(code: str, text: str) -> str:
    return text if not sys.stdout.isatty() else f"\033[{code}m{text}\033[0m"


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _passed
    if ok:
        _passed += 1
        print(f"  {_c('32', 'ok')}   {name}")
    else:
        _failed.append(name)
        print(f"  {_c('31', 'FAIL')} {name}" + (f"\n         {detail}" if detail else ""))
    return ok


def request(base: str, path: str, method: str = "GET", body: dict | None = None):
    """Return (status, parsed_json_or_text). Never raises for HTTP errors —
    a 503 from /api/health is a valid outcome this test asserts on."""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    except Exception as e:  # connection refused, DNS, timeout
        return 0, str(e)
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def section(title: str) -> None:
    print(f"\n{_c('1', title)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?", default="http://localhost:8080", help="appliance base URL")
    ap.add_argument(
        "--calibration",
        action="store_true",
        help="also test calibration writes (DESTRUCTIVE: discards capture points)",
    )
    args = ap.parse_args()
    base = args.base

    print(f"AC Monitor API smoke test → {base}")

    # ---- reachability ------------------------------------------------------
    section("Reachability")
    status, state = request(base, "/api/state")
    if not check("GET /api/state reachable", status == 200, f"status={status} body={state}"):
        print("\nNothing else can be checked. Is the appliance running?")
        return 1

    # ---- read-only surface -------------------------------------------------
    section("Read-only endpoints")
    for key in ("temps", "delta_t", "faults", "toggles", "health", "i2c_ok", "unit"):
        check(f"/api/state carries `{key}`", key in state, f"keys={sorted(state)}")
    check(
        "/api/state fan is present (may be null while debouncing)",
        "fan_running" in state,
    )

    status, version = request(base, "/api/version")
    check("GET /api/version → 200", status == 200)
    check(
        "/api/version reports a commit",
        isinstance(version, dict) and bool(version.get("commit")),
        f"got {version}",
    )
    if isinstance(version, dict) and version.get("commit") == "dev":
        print("         note: commit is 'dev' — a local build, not a CI image")

    status, cal = request(base, "/api/calibration")
    check("GET /api/calibration → 200", status == 200)
    channels = sorted(cal) if isinstance(cal, dict) else []
    check("/api/calibration returns channels", bool(channels), f"got {cal}")
    if channels:
        first = cal[channels[0]]
        for key in ("gain", "offset", "custom", "captures"):
            check(f"calibration[{channels[0]}] has `{key}`", key in first)

    # ---- health contract ---------------------------------------------------
    section("Health contract")
    status, health = request(base, "/api/health")
    i2c_ok = bool(state.get("i2c_ok"))
    check(
        f"GET /api/health → {200 if i2c_ok else 503} (bus {'up' if i2c_ok else 'down'})",
        status == (200 if i2c_ok else 503),
        f"status={status}, i2c_ok={i2c_ok}",
    )
    check(
        "/api/health status matches the bus",
        isinstance(health, dict) and health.get("status") == ("ok" if i2c_ok else "degraded"),
        f"got {health}",
    )
    hz_status, hz = request(base, "/healthz")
    check("GET /healthz (deprecated alias) mirrors /api/health", hz_status == status and hz == health)

    # ---- the shared API reference page ------------------------------------
    section("API docs")
    status, page = request(base, "/api/docs")
    check("GET /api/docs → 200", status == 200)
    check(
        "/api/docs makes no external requests",
        isinstance(page, str) and not any(h in page for h in ("cdn.", "jsdelivr", "googleapis")),
        "found a reference to an external host — it will break offline",
    )

    # ---- writes, with restore ---------------------------------------------
    section("Toggles (round-trip, restored)")
    original = dict(state.get("toggles") or {})
    mqtt_before = None
    try:
        for name, key in (("display", "display_push"), ("mqtt", "mqtt")):
            before = original.get(key)
            status, res = request(base, f"/api/toggle/{name}", "POST", {})
            if status == 409:
                check(f"POST /api/toggle/{name} → 409 (needs a broker host first)", True)
                continue
            check(f"POST /api/toggle/{name} → 200", status == 200, f"status={status} body={res}")
            _, after = request(base, "/api/state")
            check(
                f"/api/toggle/{name} actually flipped {key}",
                (after.get("toggles") or {}).get(key) == (not before),
                f"{before} -> {(after.get('toggles') or {}).get(key)}",
            )

        section("MQTT config (round-trip, restored)")
        _, cfg_state = request(base, "/api/state")
        mqtt_before = (cfg_state.get("mqtt") or {}) if isinstance(cfg_state.get("mqtt"), dict) else None
        probe = {"host": "127.0.0.1", "port": 18831, "username": "smoketest", "password": "x"}
        status, res = request(base, "/api/mqtt/config", "POST", probe)
        check("POST /api/mqtt/config → 200", status == 200, f"status={status} body={res}")
        check(
            "/api/mqtt/config echoes what it stored",
            isinstance(res, dict) and res.get("host") == probe["host"] and res.get("port") == probe["port"],
            f"got {res}",
        )
        check(
            "/api/mqtt/config does not echo the password back",
            isinstance(res, dict) and "password" not in res,
            f"got {res} — credentials must not be readable over the API",
        )

        if args.calibration:
            section("Calibration writes (DESTRUCTIVE)")
            role = channels[0] if channels else None
            if not role:
                check("a channel to calibrate", False, "no channels reported")
            else:
                before_cal = cal[role]
                status, res = request(
                    base, "/api/calibrate/manual", "POST",
                    {"role": role, "gain": 1.0, "offset": 0.0},
                )
                check(f"POST /api/calibrate/manual({role}) → 200", status == 200, f"body={res}")
                _, after_cal = request(base, "/api/calibration")
                check(
                    "manual calibration persisted",
                    abs(after_cal[role]["gain"] - 1.0) < 1e-9,
                    f"got {after_cal[role]}",
                )
                status, res = request(base, "/api/calibrate/reset", "POST", {"role": role})
                check(f"POST /api/calibrate/reset({role}) → 200", status == 200, f"body={res}")
                _, after_reset = request(base, "/api/calibration")
                check("reset clears the custom flag", after_reset[role]["custom"] is False)
                # Restore gain/offset. Capture points are gone for good.
                request(
                    base, "/api/calibrate/manual", "POST",
                    {"role": role, "gain": before_cal["gain"], "offset": before_cal["offset"]},
                )
                print(f"         restored {role} gain/offset; capture points were discarded")

            status, res = request(base, "/api/calibrate/capture", "POST", {"role": "not_a_channel", "known_c": 0})
            check("unknown channel is rejected with 400", status == 400, f"status={status} body={res}")
    finally:
        # Restore, even if an assertion above blew up.
        section("Restore")
        _, now = request(base, "/api/state")
        toggles_now = now.get("toggles") or {}
        for name, key in (("display", "display_push"), ("mqtt", "mqtt")):
            want = original.get(key)
            if want is not None and toggles_now.get(key) != want:
                request(base, f"/api/toggle/{name}", "POST", {})
        _, final = request(base, "/api/state")
        check(
            "toggles restored to their original values",
            all(
                original.get(k) is None or (final.get("toggles") or {}).get(k) == original.get(k)
                for k in ("display_push", "mqtt")
            ),
            f"before={original} after={final.get('toggles')}",
        )
        if mqtt_before:
            request(base, "/api/mqtt/config", "POST", {
                "host": mqtt_before.get("host") or "",
                "port": mqtt_before.get("port") or 1883,
                "username": mqtt_before.get("username") or "",
            })
            print("         restored MQTT host/port/username (password not readable, left as set)")

    # ---- result ------------------------------------------------------------
    print(f"\n{_passed} passed, {len(_failed)} failed")
    for name in _failed:
        print(f"  - {name}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
