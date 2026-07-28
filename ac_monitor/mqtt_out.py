"""MQTT publishing + Home Assistant discovery, with a runtime on/off toggle.

The topic tree and discovery payloads follow docs/mqtt-homeassistant.md. The
message builders are pure (topic/payload lists) so they're testable without a
broker; :class:`MqttPublisher` drives a paho client (injectable for tests) and
manages connect/disconnect as the ``mqtt.enabled`` toggle flips.
"""

from __future__ import annotations

import json

from .config import Config
from .derive import FAULT_NAMES

ROLE_LABELS = {
    "suction_line": "Suction Line Temp",
    "liquid_line": "Liquid Line Temp",
    "input_air": "Input Air Temp",
    "output_air": "Output Air Temp",
}
FAULT_LABELS = {
    "sensor_fault": "Sensor Fault",
    "no_airflow": "No-Airflow Fault",
    "abnormal_delta_t": "Abnormal ΔT",
    "wrong_direction": "Wrong Direction",
    "ha_unavailable": "Home Assistant Unavailable",
}


def _device() -> dict:
    return {
        "identifiers": ["ac_monitor_pi"],
        "name": "AC Monitor",
        "manufacturer": "DIY",
        "model": "RPi 5 + Sequent Home Automation HAT",
    }


# --- pure message builders ---------------------------------------------------

def discovery_messages(cfg: Config) -> list[tuple[str, str, bool]]:
    """(topic, json_payload, retain) discovery configs — published once, retained."""
    m = cfg.mqtt
    base, pre = m.base_topic, m.discovery_prefix
    avail = f"{base}/status"
    unit = "°F" if cfg.temperature_unit.upper() == "F" else "°C"
    out: list[tuple[str, str, bool]] = []

    def sensor(key, name, topic, **extra):
        p = {
            "name": name,
            "unique_id": f"ac_monitor_{key}",
            "state_topic": topic,
            "availability_topic": avail,
            "device": _device(),
            **extra,
        }
        out.append((f"{pre}/sensor/ac_monitor/{key}/config", json.dumps(p), True))

    def binary(key, name, topic, **extra):
        p = {
            "name": name,
            "unique_id": f"ac_monitor_{key}",
            "state_topic": topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": avail,
            "device": _device(),
            **extra,
        }
        out.append((f"{pre}/binary_sensor/ac_monitor/{key}/config", json.dumps(p), True))

    for role in cfg.thermistors.channels:
        sensor(role, ROLE_LABELS.get(role, role), f"{base}/temperature/{role}",
               unit_of_measurement=unit, device_class="temperature")
    sensor("delta_t", "Air ΔT", f"{base}/derived/delta_t",
           unit_of_measurement=unit, device_class="temperature")
    binary("airflow", "Airflow", f"{base}/airflow", device_class="running")
    for fault in FAULT_NAMES:
        binary(fault, FAULT_LABELS.get(fault, fault), f"{base}/derived/fault/{fault}",
               device_class="problem")
    return out


def state_messages(snapshot: dict, cfg: Config) -> list[tuple[str, str, bool]]:
    """(topic, payload, retain) live state messages for one poll."""
    base = cfg.mqtt.base_topic
    out: list[tuple[str, str, bool]] = []
    for role, v in snapshot.get("temps", {}).items():
        if v is not None:
            out.append((f"{base}/temperature/{role}", str(v), False))
    dt = snapshot.get("delta_t")
    if dt is not None:
        out.append((f"{base}/derived/delta_t", str(dt), False))
    fan = snapshot.get("fan_running")
    if fan is not None:
        out.append((f"{base}/airflow", "ON" if fan else "OFF", False))
    for name, val in snapshot.get("faults", {}).items():
        out.append((f"{base}/derived/fault/{name}", "ON" if val else "OFF", False))
    return out


# --- publisher ---------------------------------------------------------------

def _default_client():  # pragma: no cover - needs a broker
    import paho.mqtt.client as mqtt

    return mqtt.Client()


class MqttPublisher:
    """Connect/publish/disconnect driven by the ``mqtt.enabled`` toggle.

    ``client_factory`` returns a paho-like client (``will_set``,
    ``username_pw_set``, ``connect``, ``loop_start/stop``, ``publish``,
    ``disconnect``); injected in tests.
    """

    def __init__(self, client_factory=_default_client):
        self._factory = client_factory
        self.client = None
        self.connected = False
        self._discovered = False
        self._status_topic = None

    def sync(self, state) -> None:
        m = state.config.mqtt
        if m.enabled and self.client is None and m.host:
            self._connect(state.config)
        elif not m.enabled and self.client is not None:
            self.close()
        if not self.connected:
            return
        if not self._discovered:
            for t, p, r in discovery_messages(state.config):
                self.client.publish(t, p, retain=r)
            self._discovered = True
        for t, p, r in state_messages(state.snapshot(), state.config):
            self.client.publish(t, p, retain=r)

    def _connect(self, cfg: Config) -> None:
        m = cfg.mqtt
        c = self._factory()
        status = f"{m.base_topic}/status"
        c.will_set(status, "offline", retain=True)
        if m.username:
            c.username_pw_set(m.username, m.password)
        c.connect(m.host, m.port)
        c.loop_start()
        c.publish(status, "online", retain=True)
        self.client = c
        self.connected = True
        self._discovered = False
        self._status_topic = status

    def close(self) -> None:
        """Publish offline and tear down the client (toggle off / shutdown)."""
        if self.client is None:
            return
        try:
            if self._status_topic:
                self.client.publish(self._status_topic, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # pragma: no cover - best-effort teardown
            pass
        finally:
            self.client = None
            self.connected = False
            self._discovered = False
