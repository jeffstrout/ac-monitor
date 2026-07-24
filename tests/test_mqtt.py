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

    def will_set(self, t, p, retain=False):
        self.will = (t, p, retain)

    def username_pw_set(self, u, p):
        self.userpw = (u, p)

    def connect(self, h, port):
        self.host = (h, port)

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
    pub = mqtt_out.MqttPublisher(client_factory=lambda: fake)
    pub.sync(st)
    assert pub.connected is True
    assert fake.will == ("ac_monitor/status", "offline", True)          # LWT
    assert ("ac_monitor/status", "online", True) in fake.published
    assert any(t.endswith("/config") for t, _, _ in fake.published)     # discovery
    assert ("ac_monitor/temperature/input_air", "72.5", False) in fake.published


def test_publisher_no_connect_when_disabled():
    fake = FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda: fake)
    pub.sync(_state(enabled=False))
    assert pub.connected is False and fake.host is None


def test_publisher_no_connect_without_host():
    # config.validate() rejects enabled+no-host, so force the state to exercise
    # the publisher's defensive guard directly.
    st = _state(enabled=False)
    st.config.mqtt.enabled = True
    st.config.mqtt.host = ""
    fake = FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda: fake)
    pub.sync(st)
    assert pub.connected is False and fake.host is None


def test_publisher_disconnects_on_toggle_off():
    st, fake = _state(enabled=True), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda: fake)
    pub.sync(st)
    assert pub.connected
    st.config.mqtt.enabled = False
    pub.sync(st)
    assert pub.connected is False
    assert ("ac_monitor/status", "offline", True) in fake.published     # graceful offline


def test_discovery_published_once():
    st, fake = _state(), FakeClient()
    pub = mqtt_out.MqttPublisher(client_factory=lambda: fake)
    pub.sync(st)
    n1 = sum(1 for t, _, _ in fake.published if t.endswith("/config"))
    pub.sync(st)
    n2 = sum(1 for t, _, _ in fake.published if t.endswith("/config"))
    assert n1 > 0 and n2 == n1                                          # not re-published
