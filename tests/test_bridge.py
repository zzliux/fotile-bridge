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


def test_number_and_drain_switch_discovery():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"SetAppointmentTime": 30, "AutoDrain": 0})
    topics = [t for t, _, _ in bridge.client.sent]
    assert any(t.startswith("homeassistant/number/") and t.endswith("_appointment/config") for t in topics)
    assert any(t.startswith("homeassistant/switch/") and t.endswith("_autodrain/config") for t in topics)


def test_drain_switch_reflects_autodrain_state():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"AutoDrain": 1})
    cfg = json.loads(next(
        p for t, p, _ in bridge.client.sent
        if t.endswith("_autodrain/config") and p
    ))
    assert cfg["name"] == "排水"
    assert cfg["state_on"] == "1" and cfg["state_off"] == "0"
    assert cfg["value_template"] == "{{ value_json.AutoDrain }}"
    assert json.loads(cfg["payload_on"]) == {"drain": 1}
    assert json.loads(cfg["payload_off"]) == {"AutoDrain": 0}


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


def test_program_select_raw_string():
    from types import SimpleNamespace
    bridge = make_bridge()
    bridge._on_message(None, None, SimpleNamespace(
        topic="fotile/1032A/1149539722/program",
        payload="超快洗".encode(),
    ))
    assert bridge._programs["1149539722"] == "超快洗"


def test_hhmm_decode_template():
    from app.discovery import Discovery
    from app.profile import Field
    d = Discovery(lambda *a: None)
    t = d._template(Field(key="ClRemTime", decode="hhmm"))
    assert "value_json.ClRemTime // 256" in t
    assert "// 256) * 60" in t


def test_hhmm_time_template():
    from app.discovery import Discovery
    from app.profile import Field
    d = Discovery(lambda *a: None)
    t = d._template(Field(key="SetAppointmentTime", decode="hhmm_time"))
    assert "未设置" in t and "// 256" in t


def test_hhmm_day_template():
    from app.discovery import Discovery
    from app.profile import Field
    d = Discovery(lambda *a: None)
    t = d._template(Field(key="SetAppointmentTime", decode="hhmm_day"))
    assert "// 24" in t and "明天" in t and "未设置" in t


def test_storage_param_used_at_start():
    from types import SimpleNamespace
    bridge = make_bridge()
    bridge._on_message(None, None, SimpleNamespace(
        topic="fotile/1032A/1149539722/param",
        payload=b'{"StoreTotalTime": 42}',
    ))
    bridge._programs["1149539722"] = "超净洗"
    bridge._handle_command("fotile/1032A/1149539722/set", {"start_program": 1})
    payload = json.loads(bridge.client.sent[-1][1])
    assert payload["StoreTotalTime"] == 42
    assert payload["WorkMode"] == 2


def test_program_select_is_optimistic_and_sensor_maps_program():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkMode": 2})
    sent = dict((t, p) for t, p, _ in bridge.client.sent)
    sel = json.loads(sent["homeassistant/select/fotile_1032A_1149539722_program/config"])
    assert "state_topic" not in sel
    cur = json.loads(sent["homeassistant/sensor/fotile_1032A_1149539722_WorkMode/config"])
    assert "超净洗" in cur["value_template"]


def test_current_program_shows_only_when_running():
    from app.discovery import Discovery
    from app.profile import Field
    d = Discovery(lambda *a: None)
    t = d._template(Field(key="WorkMode", decode="program", options={"2": "超净洗"}))
    assert "WorkState in [2, 6]" in t
    assert "超净洗" in t


def test_drain_without_power_off(monkeypatch):
    bridge = make_bridge()
    monkeypatch.setattr("app.mqtt_bridge.time.sleep", lambda *_: None)
    bridge._handle_command("fotile/1032A/1149539722/set", {"drain": 1})
    sent = [json.loads(p) for t, p, _ in bridge.client.sent if t == "control/1032A/1149539722"]
    assert sent == [{"AutoDrain": 1}]


def test_entities_have_availability_topic():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"PowerSwitchAll": 0, "WorkState": 0})
    cfgs = [json.loads(p) for t, p, _ in bridge.client.sent if t.endswith("/config") and p]
    with_avail = [c for c in cfgs if c.get("availability_topic")]
    assert with_avail
    assert all(c["availability_topic"] == "fotile/availability/1149539722" for c in with_avail)


def test_availability_topic_is_bridged(tmp_path):
    """run.sh 生成的 mosquitto 配置必须把 availability 桥接给 HA。"""
    run = open(os.path.join(os.path.dirname(__file__), "..", "fotile_bridge", "run.sh")).read()
    assert "topic fotile/availability/# out 0" in run


def test_availability_cache_roundtrip(tmp_path):
    from app.relay import MqttRelay

    cfg = Config(data_dir=str(tmp_path))
    relay = MqttRelay(cfg, lambda *a: None)
    relay._touch_avail("1149539722", True)
    again = MqttRelay(cfg, lambda *a: None)
    assert again._avail_cache["1149539722"] > 0


def test_availability_restore_publishes_online(tmp_path):
    import time
    from app.relay import MqttRelay

    cfg = Config(data_dir=str(tmp_path), availability_fresh=900)
    relay = MqttRelay(cfg, lambda *a: None)
    sent = []
    relay.local_publish = lambda topic, payload, retain: sent.append((topic, payload, retain))
    relay._avail_cache = {"1149539722": time.time() - 60, "999": 0.0}
    relay._restore_availability_now()
    assert ("fotile/availability/1149539722", "online", True) in sent
    assert ("fotile/availability/999", "offline", True) in sent


def test_local_control_ignores_availability():
    from app.relay import MqttRelay

    relay = MqttRelay(Config(), lambda *a: None)
    injected = []
    relay.inject = lambda topic, payload: injected.append(topic)

    class Msg:
        topic = "fotile/availability/1149539722"
        payload = b"online"
        retain = True

    relay._on_local_control(None, None, Msg())
    assert injected == []


def test_gsc_select_and_buttons_published():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"GSCWorkSta": 1, "GSCAutoDrain": 0})
    topics = [t for t, _, _ in bridge.client.sent]
    select_topic = "homeassistant/select/fotile_1032A_1149539722_gsc_program/config"
    assert select_topic in topics
    cfg = json.loads(next(p for t, p, _ in bridge.client.sent if t == select_topic))
    assert cfg["options"] == [
        "果蔬清洗-轻", "果蔬清洗-中", "果蔬清洗-强", "果蔬清洗-深层",
        "生鲜清洗-轻", "生鲜清洗-中", "生鲜清洗-强", "虾蟹暂养",
    ]
    for key in ("gsc_start", "gsc_pause", "gsc_resume", "gsc_stop"):
        assert f"homeassistant/button/fotile_1032A_1149539722_{key}/config" in topics
    # POP 排水开关
    assert "homeassistant/switch/fotile_1032A_1149539722_gscautodrain/config" in topics


def test_gsc_start_uses_selected_program():
    bridge = make_bridge()
    bridge._handle_command("fotile/1032A/1149539722/set", {"gsc_program": "生鲜清洗-强"})
    bridge._handle_command("fotile/1032A/1149539722/set", {"gsc_start": 1})
    body = json.loads([p for t, p, _ in bridge.client.sent if t == "control/1032A/1149539722"][-1])
    assert body == {"GSCWorkMode": 4, "GSCWorkSta": 1, "PowerSwitchAll": 1}


def test_gsc_pause_resume_stop():
    bridge = make_bridge()
    sent = lambda: [json.loads(p) for t, p, _ in bridge.client.sent if t == "control/1032A/1149539722"][-1]
    bridge._handle_command("fotile/1032A/1149539722/set", {"gsc_pause": 1})
    assert sent() == {"GSCWorkSta": 2}
    bridge._handle_command("fotile/1032A/1149539722/set", {"gsc_resume": 1})
    assert sent() == {"GSCWorkSta": 1}
    bridge._handle_command("fotile/1032A/1149539722/set", {"gsc_stop": 1})
    assert sent() == {"GSCWorkSta": 0}


def test_gsc_selection_syncs_from_device_mode():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"GSCWorkMode": 10})
    assert bridge._gsc_programs["1149539722"] == "虾蟹暂养"
    topics = [t for t, _, _ in bridge.client.sent]
    assert "fotile/1032A/1149539722/gsc_program" in topics


def test_gsc_fields_are_visible():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"GSCWorkSta": 2, "GSCClRemT": 18, "GSCWorkMode": 0, "GSCErrorNum": 0})
    topics = [t for t, _, _ in bridge.client.sent]
    for key in ("GSCWorkSta", "GSCClRemT", "GSCWorkMode", "GSCErrorNum"):
        assert f"homeassistant/sensor/fotile_1032A_1149539722_{key}/config" in topics


def test_basket_field_exposed_and_gates_controls():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"GSCDoorLockSta": 1, "GSCAutoDrain": 0, "SetAppointmentTime": 30})
    cfgs = {t: json.loads(p) for t, p, _ in bridge.client.sent if t.endswith("/config") and p}

    basket = cfgs["homeassistant/binary_sensor/fotile_1032A_1149539722_GSCDoorLockSta/config"]
    assert basket["name"] == "果蔬篮"
    assert basket["payload_on"] == "1" and basket["payload_off"] == "0"

    # POP 排水开关：只有篮在位（1）才可用
    pop = cfgs["homeassistant/switch/fotile_1032A_1149539722_gscautodrain/config"]
    assert pop["availability"][0]["payload_available"] == "yes"
    assert "GSCDoorLockSta" in pop["availability"][0]["value_template"]
    assert "in [1]" in pop["availability"][0]["value_template"]

    # 碗碟程序选择与“启动所选程序”：运行中或篮在位时不可用
    select = cfgs["homeassistant/select/fotile_1032A_1149539722_program/config"]
    assert "WorkState" in select["availability"][0]["value_template"]
    assert "in [1, 3]" in select["availability"][0]["value_template"]
    assert "GSCDoorLockSta" in select["availability"][0]["value_template"]
    start = cfgs["homeassistant/button/fotile_1032A_1149539722_btn_start_program/config"]
    assert "in [1, 3]" in start["availability"][0]["value_template"]


def test_runtime_gating_rules():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkState": 0, "PowerSwitchAll": 0, "GSCWorkSta": 0})
    cfgs = {t: json.loads(p) for t, p, _ in bridge.client.sent if t.endswith("/config") and p}

    def tmpl(topic):
        return cfgs[topic]["availability"][0]["value_template"]

    # 排水：关机（PowerSwitchAll=0）才可用
    assert "PowerSwitchAll" in tmpl("homeassistant/switch/fotile_1032A_1149539722_autodrain/config")
    assert "in [0]" in tmpl("homeassistant/switch/fotile_1032A_1149539722_autodrain/config")
    # 继续：仅暂停中
    assert "in [3]" in tmpl("homeassistant/button/fotile_1032A_1149539722_btn_start/config")
    # 暂停：仅运行中
    assert "in [2]" in tmpl("homeassistant/button/fotile_1032A_1149539722_btn_pause/config")
    # 取消：运行或暂停
    assert "in [2, 3]" in tmpl("homeassistant/button/fotile_1032A_1149539722_btn_stop/config")
    # 果蔬篮：暂停仅运行中，取消运行/暂停
    assert "in [2]" in tmpl("homeassistant/button/fotile_1032A_1149539722_gsc_pause/config")
    assert "in [2, 3]" in tmpl("homeassistant/button/fotile_1032A_1149539722_gsc_stop/config")


def test_store_total_time_gated_and_availability_not_conflicting():
    bridge = make_bridge()
    bridge._handle_sync("sync/1032A/1149539722", {"WorkState": 1, "StoreTotalTime": 1})
    cfgs = {t: json.loads(p) for t, p, _ in bridge.client.sent if t.endswith("/config") and p}
    cfg = cfgs["homeassistant/number/fotile_1032A_1149539722_StoreTotalTime_number/config"]
    assert "in [1, 3]" in cfg["availability"][0]["value_template"]
    # 拔掉果蔬篮后才允许设置保洁天数
    assert "GSCDoorLockSta" in cfg["availability"][0]["value_template"]
    assert "in [0]" in cfg["availability"][0]["value_template"]
    # 有 availability 列表时不能再出现 availability_topic（HA 会拒绝）
    assert "availability_topic" not in cfg
    assert cfg["availability"][-1]["topic"] == "fotile/availability/1149539722"
