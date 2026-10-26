"""MQTT publishing + Home Assistant discovery, with a runtime on/off toggle.

The topic tree and discovery payloads follow docs/mqtt-homeassistant.md. The
message builders are pure (topic/payload lists) so they're testable without a
broker; :class:`MqttPublisher` drives a paho client (injectable for tests) and
manages connect/disconnect as the ``mqtt.enabled`` toggle flips.
"""

from __future__ import annotations

import json
import socket
import time

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
    "airflow_mismatch": "Airflow Mismatch",
    "abnormal_delta_t": "Abnormal ΔT",
    "delta_t_not_developing": "ΔT Not Developing",
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

def default_client_id(cfg: Config) -> str:
    """Stable client id: ``mqtt.client_id`` or ``ac-monitor-<hostname>``.

    It must survive reboots. With paho's random default id, a Pi that restarts
    without a clean DISCONNECT leaves its old session alive on the broker until
    keepalive expiry; that session's retained LWT ``offline`` then lands *after*
    the new process has announced ``online`` and sticks forever (#73). Reusing
    the id makes the broker take over the old session at CONNECT time instead.
    """
    if cfg.mqtt.client_id:
        return cfg.mqtt.client_id
    return f"ac-monitor-{socket.gethostname()}"


def _default_client(client_id: str):  # pragma: no cover - needs a broker
    import paho.mqtt.client as mqtt

    # requirements.txt is unpinned, so a rebuilt image can land on paho 1.x or
    # 2.x. A bare Client() works on both, but on 2.x it warns and silently picks
    # the v1 callback API — state the choice instead of inheriting a deprecated
    # default. Callbacks only use positional client/userdata/message.
    api = getattr(mqtt, "CallbackAPIVersion", None)
    if api is not None:
        return mqtt.Client(api.VERSION1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


class MqttPublisher:
    """Connect/publish/disconnect driven by the ``mqtt.enabled`` toggle.

    ``client_factory(client_id)`` returns a paho-like client (``will_set``,
    ``username_pw_set``, ``connect``, ``loop_start/stop``, ``publish``,
    ``subscribe``, ``disconnect``); injected in tests.

    Availability is self-healing (#73): retained ``online`` is re-published on
    every (re)connect, every ``announce_interval_s``, whenever Home Assistant
    announces itself on ``<discovery_prefix>/status``, and whenever we observe
    our own status topic flip to ``offline`` while we are connected.
    """

    def __init__(self, client_factory=_default_client, clock=time.time):
        self._factory = client_factory
        self._clock = clock
        self.client = None
        self.connected = False
        self.client_id = None
        self.last_announce_at = None
        self._discovered = False
        self._status_topic = None
        self._ha_status_topic = None

    def sync(self, state) -> None:
        m = state.config.mqtt
        if (
            self.client is not None
            and self.client_id is not None
            and default_client_id(state.config) != self.client_id
        ):
            # client_id changed via the API (#75): reconnect cleanly under the
            # new id. close() publishes retained offline and disconnects; the
            # connect below announces online and resends discovery.
            self.close()
        if m.enabled and self.client is None and m.host:
            self._connect(state.config)
        elif not m.enabled and self.client is not None:
            self.close()
        try:
            self._sync_connected(state)
        finally:
            state.mqtt_status = self.status(state.config)

    def _sync_connected(self, state) -> None:
        if not self.connected:
            return
        interval = state.config.mqtt.announce_interval_s
        if interval > 0 and (
            self.last_announce_at is None or self._clock() - self.last_announce_at >= interval
        ):
            self._announce(self.client, rediscover=False)
        if not self._discovered:
            for t, p, r in discovery_messages(state.config):
                self.client.publish(t, p, retain=r)
            self._discovered = True
        for t, p, r in state_messages(state.snapshot(), state.config):
            self.client.publish(t, p, retain=r)

    def status(self, cfg: Config) -> dict:
        """MQTT connection state for /api/state. Never includes credentials."""
        return {
            "enabled": cfg.mqtt.enabled,
            "connected": self.connected,
            "client_id": self.client_id,
            "last_announce_at": self.last_announce_at,
        }

    def _announce(self, client, rediscover: bool = True) -> None:
        """Publish retained ``online`` and (by default) force discovery resend.

        Runs on every successful (re)connect, not just the first. paho reconnects
        on its own under ``loop_start()`` without telling us — and by then the
        broker has already published our retained LWT ``offline`` on our behalf.
        Without this, a broker restart (every Home Assistant update is one) leaves
        HA showing every entity unavailable while fresh readings keep arriving.

        Re-sending discovery covers the other half — a broker that came back
        without its retained store has forgotten the entity configs.
        """
        if self._status_topic:
            client.publish(self._status_topic, "online", retain=True)
            self.last_announce_at = self._clock()
        if rediscover:
            self._discovered = False

    def _on_connect(self, client, _userdata=None, _flags=None, rc=0, *_args) -> None:
        # Signature varies across paho callback API versions (v1: rc int, v2:
        # ReasonCode). Both compare equal to 0 / are falsy-ish on success.
        try:
            failed = int(getattr(rc, "value", rc)) != 0
        except (TypeError, ValueError):  # pragma: no cover - exotic rc type
            failed = False
        if failed:
            self.connected = False
            return
        self.connected = True
        topics = [t for t in (self._status_topic, self._ha_status_topic) if t]
        for t in topics:  # subscriptions don't survive a non-persistent session
            client.subscribe(t)
        self._announce(client)

    def _on_disconnect(self, _client, *_args) -> None:
        self.connected = False

    def _on_message(self, client, _userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace") if isinstance(msg.payload, bytes) else str(msg.payload)
        payload = payload.strip().lower()
        if msg.topic == self._ha_status_topic and payload == "online":
            # HA (re)started: it wants availability + discovery again.
            self._announce(client)
        elif msg.topic == self._status_topic and payload == "offline" and self.connected:
            # A stale LWT (e.g. from a pre-reboot session) overwrote us. We're
            # alive, so correct the retained value right away.
            self._announce(client, rediscover=False)

    def _connect(self, cfg: Config) -> None:
        m = cfg.mqtt
        self.client_id = default_client_id(cfg)
        c = self._factory(self.client_id)
        status = f"{m.base_topic}/status"
        self._status_topic = status
        self._ha_status_topic = f"{m.discovery_prefix}/status"
        c.will_set(status, "offline", retain=True)
        if m.username:
            c.username_pw_set(m.username, m.password)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.connect(m.host, m.port)
        c.loop_start()
        self.client = c
        # CONNACK arrives asynchronously via on_connect, which sets `connected`,
        # subscribes, and announces. Until then sync() publishes nothing (paho
        # would only queue it).

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
