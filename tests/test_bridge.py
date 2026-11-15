"""命令映射与 Discovery 测试。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fotile_bridge"))

from app.config import Config  # noqa: E402
from app.mqtt_bridge import MqttBridge  # noqa: E402
from app.profile import ProfileStore  # noqa: E402

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "fotile_bridge", "app", "profiles")


class FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, retain))


def make_bridge():
    bridge = MqttBridge(Config(), ProfileStore(PROFILE_DIR))
    bridge.client = FakeClient()
    return bridge


def test_power_off_mapping():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"power": "off"})
    topic, payload, _ = bridge.client.sent[-1]
    assert topic == "control/1032A/1149539722"
    assert json.loads(payload) == {"PowerSwitchAll": 0, "GSCPowerSwitch": 0}


def test_power_on_mapping():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"power": "on"})
    topic, payload, _ = bridge.client.sent[-1]
    assert json.loads(payload) == {"PowerSwitchAll": 1, "GSCPowerSwitch": 1}


def test_raw_passthrough():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"raw": {"WorkState": 0}})
    topic, payload, _ = bridge.client.sent[-1]
    assert topic == "control/1032A/1149539722"
    assert json.loads(payload) == {"WorkState": 0}


def test_discovery_publishes_power_switch():
    bridge = make_bridge()
    state = {"PowerSwitchAll": 1, "ClRemTime": 10}
    bridge._handle_sync("sync/1032A/1149539722", state)
    topics = [t for t, _, _ in bridge.client.sent]
    assert "homeassistant/switch/fotile_1032A_1149539722_power/config" in topics
    assert "homeassistant/sensor/fotile_1032A_1149539722_ClRemTime/config" in topics


def test_generic_discovery_for_unknown_product():
    bridge = make_bridge()
    bridge._handle_sync("sync/9999X/ABC123", {"SomeField": 42, "Other": "ok"})
    topics = [t for t, _, _ in bridge.client.sent]
    assert "homeassistant/sensor/fotile_9999X_ABC123_SomeField/config" in topics
    assert "homeassistant/sensor/fotile_9999X_ABC123_Other/config" in topics


def test_extra_fields_become_sensors():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"ClRemTime": 5, "NewField": 1})
    topics = [t for t, _, _ in bridge.client.sent]
    assert "homeassistant/sensor/fotile_1032A_1149539722_NewField/config" in topics


def test_value_mapping_template():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkState": 2})
    configs = {t: p for t, p, _ in bridge.client.sent if t.endswith("_WorkState/config")}
    payload = json.loads(next(iter(configs.values())))
    assert "运行中" in payload["value_template"]


def test_disabled_field_has_enabled_by_default_false():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"PowerSwitchAll": 1})
    configs = {t: p for t, p, _ in bridge.client.sent if t.endswith("_PowerSwitchAll/config")}
    payload = json.loads(next(iter(configs.values())))
    assert payload["enabled_by_default"] is False


def test_number_and_button_discovery():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"SetAppointmentTime": 30})
    topics = [t for t, _, _ in bridge.client.sent]
    assert any(t.startswith("homeassistant/number/") and t.endswith("_appointment/config") for t in topics)
    assert any(t.startswith("homeassistant/button/") and t.endswith("_drain/config") for t in topics)


def test_appointment_command():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"appointment": 45})
    topic, payload, _ = bridge.client.sent[-1]
    assert topic == "control/1032A/1149539722"
    assert json.loads(payload) == {"SetAppointmentTime": 45}


def test_hidden_entities_are_not_published_and_old_ones_cleared():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkMode": 0, "WorkState": 0})
    sent = bridge.client.sent
    assert not any("_text_" in t and p for t, p, _ in sent)
    cleared = [t for t, p, retain in sent if p == "" and retain and t.endswith("/config")]
    assert any(t.endswith("_text_WorkMode/config") for t in cleared)
    assert any(t.endswith("_appointment/config") for t in cleared)


def test_start_command():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"start": 1})
    topic, payload, _ = bridge.client.sent[-1]
    assert json.loads(payload) == {"WorkState": 1}


def test_button_discovery_for_commands():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkState": 0})
    topics = [t for t, _, _ in bridge.client.sent]
    assert any("_btn_start/config" in t for t in topics)
    assert any("_btn_pause/config" in t for t in topics)
    assert any("_btn_start_program/config" in t for t in topics)


def test_program_select_and_start():
    bridge = make_bridge()
    bridge._programs["1149539722"] = "日常洗"
    bridge._handle_command("fotile/1032A/1149539722/set", {"start_program": 1})
    topic, payload, _ = bridge.client.sent[-1]
    assert topic == "control/1032A/1149539722"
    assert json.loads(payload)["WorkMode"] == 0
    assert json.loads(payload)["WorkState"] == 1
