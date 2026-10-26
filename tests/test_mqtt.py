"""Tests for ac_monitor.mqtt_out — discovery/state builders + publisher lifecycle."""

import json

from ac_monitor import config as cfgmod
from ac_monitor import mqtt_out
from ac_monitor.derive import FAULT_NAMES, Derived
from ac_monitor.hat import Readings
from ac_monitor.state import AppState


def _state(enabled=True, host="192.168.1.10"):
    cfg = cfgmod.from_dict(
        {"units": {"temperature": "F"}, "mqtt": {"enabled": enabled, "host": host}}
    )
    st = AppState(config=cfg)
    r = Readings(unit="F")
    r.temps = {"output_air": 55.3, "input_air": 72.5, "suction_line": 40.1, "liquid_line": 90.2}
    r.temps_c = {k: 0.0 for k in r.temps}
    r.health = {**{k: True for k in r.temps}, "fan": True}
    r.fan_running = True
    r.delta_t = 17.2
    st.readings = r
    st.derived = Derived(delta_t=17.2, faults={n: False for n in FAULT_NAMES})
    return st


def test_discovery_messages():
    msgs = mqtt_out.discovery_messages(_state().config)
    topics = [t for t, _, _ in msgs]
    assert any(t.endswith("/sensor/ac_monitor/input_air/config") for t in topics)
    assert any(t.endswith("/sensor/ac_monitor/delta_t/config") for t in topics)
    assert any(t.endswith("/binary_sensor/ac_monitor/airflow/config") for t in topics)
    assert any(t.endswith("/binary_sensor/ac_monitor/no_airflow/config") for t in topics)
    assert all(retain for _, _, retain in msgs)                       # discovery is retained
    payload = json.loads(next(p for t, p, _ in msgs if "input_air" in t))
    assert payload["device_class"] == "temperature"
    assert payload["unit_of_measurement"] == "°F"
    assert payload["availability_topic"] == "ac_monitor/status"


def test_state_messages():
    st = _state()
    d = {t: p for t, p, _ in mqtt_out.state_messages(st.snapshot(), st.config)}
    assert d["ac_monitor/temperature/input_air"] == "72.5"
    assert d["ac_monitor/airflow"] == "ON"
    assert d["ac_monitor/derived/delta_t"] == "17.2"
    assert d["ac_monitor/derived/fault/no_airflow"] == "OFF"


class FakeClient:
    def __init__(self):
        self.published = []
        self.will = None
        self.userpw = None
        self.host = None
        self.loop = False
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.subscribed = []
        self.client_id = None

    def simulate_reconnect(self):
        """Fire the on_connect callback the way paho does after it reconnects.

        paho re-establishes the session on its own under ``loop_start()``; the
        publisher only learns about it through this callback.
        """
        assert self.on_connect is not None, "publisher never registered on_connect"
        self.on_connect(self, None, {}, 0)

    def will_set(self, t, p, retain=False):
        self.will = (t, p, retain)

    def username_pw_set(self, u, p):
        self.userpw = (u, p)

    def connect(self, h, port):
        self.host = (h, port)
        # Real paho delivers CONNACK on the network thread; fire it inline.
        if self.on_connect is not None:
            self.on_connect(self, None, {}, 0)

    def subscribe(self, t):
        self.subscribed.append(t)

    def simulate_disconnect(self):
        self.on_disconnect(self, None, 1)

    def deliver(self, topic, payload):
        """Simulate the broker delivering a message we subscribed to."""
        msg = type("Msg", (), {"topic": topic, "payload": payload.encode()})()
        self.on_message(self, None, msg)

    def loop_start(self):
        self.loop = True

    def loop_stop(self):
        self.loop = False

    def disconnect(self):
        self.host = None

    def publish(self, t, p, retain=False):
        self.published.append((t, p, retain))


def test_publisher_connects_and_publishes():
    st, fake = _state(enabled=True), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    assert pub.connected is True
    assert fake.will == ("ac_monitor/status", "offline", True)          # LWT
    assert ("ac_monitor/status", "online", True) in fake.published
    assert any(t.endswith("/config") for t, _, _ in fake.published)     # discovery
    assert ("ac_monitor/temperature/input_air", "72.5", False) in fake.published


def test_publisher_no_connect_when_disabled():
    fake = FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(_state(enabled=False))
    assert pub.connected is False and fake.host is None


def test_publisher_no_connect_without_host():
    # config.validate() rejects enabled+no-host, so force the state to exercise
    # the publisher's defensive guard directly.
    st = _state(enabled=False)
    st.config.mqtt.enabled = True
    st.config.mqtt.host = ""
    fake = FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    assert pub.connected is False and fake.host is None


def test_publisher_disconnects_on_toggle_off():
    st, fake = _state(enabled=True), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    assert pub.connected
    st.config.mqtt.enabled = False
    pub.sync(st)
    assert pub.connected is False
    assert ("ac_monitor/status", "offline", True) in fake.published     # graceful offline


def test_discovery_published_once():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    n1 = sum(1 for t, _, _ in fake.published if t.endswith("/config"))
    pub.sync(st)
    n2 = sum(1 for t, _, _ in fake.published if t.endswith("/config"))
    assert n1 > 0 and n2 == n1                                          # not re-published


def test_reconnect_republishes_online():
    """A broker restart must not leave the appliance stuck 'offline' in HA.

    The broker publishes our retained LWT 'offline' when the connection drops;
    paho then reconnects silently. Without re-announcing, readings keep flowing
    to a device Home Assistant believes is unavailable.
    """
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    fake.published.clear()

    fake.simulate_reconnect()

    assert ("ac_monitor/status", "online", True) in fake.published


def test_reconnect_resends_discovery():
    """A broker that came back without its retained store has forgotten us."""
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    fake.published.clear()

    fake.simulate_reconnect()
    pub.sync(st)

    assert any(t.endswith("/config") for t, _, _ in fake.published)


# --- #73: stale LWT / HA birth / stable client id / connection state ---------

def test_stale_lwt_after_announce_triggers_reannounce():
    """A pre-reboot session's retained LWT 'offline' landing after our 'online'
    must be corrected immediately, not left stuck (#73)."""
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    assert "ac_monitor/status" in fake.subscribed
    fake.published.clear()

    fake.deliver("ac_monitor/status", "offline")   # stale will from the old session

    assert ("ac_monitor/status", "online", True) in fake.published


def test_own_online_echo_does_not_loop():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    fake.published.clear()
    fake.deliver("ac_monitor/status", "online")
    assert fake.published == []


def test_ha_birth_reannounces_and_resends_discovery():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    assert "homeassistant/status" in fake.subscribed
    fake.published.clear()

    fake.deliver("homeassistant/status", "online")
    assert ("ac_monitor/status", "online", True) in fake.published
    pub.sync(st)
    assert any(t.endswith("/config") for t, _, _ in fake.published)


def test_client_id_stable_and_configurable(monkeypatch):
    monkeypatch.setattr(mqtt_out.socket, "gethostname", lambda: "acpi")
    seen = []

    def factory(cid):
        seen.append(cid)
        return FakeClient()

    for _ in range(2):  # two "boots"
        mqtt_out.MqttPublisher(client_factory=factory).sync(_state())
    assert seen == ["ac-monitor-acpi", "ac-monitor-acpi"]

    st = _state()
    st.config.mqtt.client_id = "custom-id"
    mqtt_out.MqttPublisher(client_factory=factory).sync(st)
    assert seen[-1] == "custom-id"


def test_periodic_reannounce():
    now = [1000.0]
    st, fake = _state(), FakeClient()
    st.config.mqtt.announce_interval_s = 300
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake, clock=lambda: now[0])
    pub.sync(st)
    fake.published.clear()

    now[0] += 100
    pub.sync(st)
    assert ("ac_monitor/status", "online", True) not in fake.published
    now[0] += 250
    pub.sync(st)
    assert ("ac_monitor/status", "online", True) in fake.published


def test_connected_tracks_callbacks_and_is_exposed():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    pub.sync(st)
    snap = st.snapshot()["mqtt"]
    assert snap["connected"] is True and snap["client_id"].startswith("ac-monitor-")
    assert "password" not in snap

    fake.simulate_disconnect()
    fake.published.clear()
    pub.sync(st)
    assert st.snapshot()["mqtt"]["connected"] is False
    assert fake.published == []          # nothing queued while down

    fake.simulate_reconnect()
    assert pub.connected is True


def test_connack_refused_not_connected():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda _cid: fake)
    fake.connect = lambda h, p: fake.on_connect(fake, None, {}, 5)   # not authorised
    pub.sync(st)
    assert pub.connected is False
    assert st.snapshot()["mqtt"]["connected"] is False


def test_client_id_change_reconnects_cleanly():
    """#75: changing mqtt.client_id while connected → offline, disconnect,
    reconnect under the new id, online + discovery."""
    clients, ids = [], []

    def factory(cid):
        ids.append(cid)
        clients.append(FakeClient())
        return clients[-1]

    st = _state()
    pub = mqtt_out.MqttPublisher(client_factory=factory)
    pub.sync(st)
    old = clients[0]
    st.config.mqtt.client_id = "ac-monitor-pi"
    pub.sync(st)

    assert ids[-1] == "ac-monitor-pi" and len(clients) == 2
    assert ("ac_monitor/status", "offline", True) in old.published
    assert old.host is None and old.loop is False            # disconnected
    new = clients[1]
    assert ("ac_monitor/status", "online", True) in new.published
    assert any(t.endswith("/config") for t, _, _ in new.published)
    assert st.snapshot()["mqtt"]["client_id"] == "ac-monitor-pi"
    assert pub.connected is True


def test_unchanged_client_id_does_not_reconnect():
    clients = []
    st = _state()
    pub = mqtt_out.MqttPublisher(client_factory=lambda cid: clients.append(FakeClient()) or clients[-1])
    pub.sync(st)
    pub.sync(st)
    assert len(clients) == 1
