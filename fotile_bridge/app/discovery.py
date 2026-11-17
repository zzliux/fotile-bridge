"""MQTT Discovery：把设备状态字段映射成 HA 实体。

设计目标是"通用"：
  - 有 profile 的型号：按 profile 的名字/单位/图标发布
  - 没 profile 的型号：按状态 JSON 的键自动生成传感器（键名即实体名），
    保证新型号不改代码也能先看到数据
  - profile 里没写的额外字段：同样自动补成传感器
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List, Set

from .profile import Field, Profile

LOG = logging.getLogger(__name__)

SKIP_KEYS = {"offline", "timestamp", "time"}


class Discovery:
    def __init__(self, publish: Callable[[str, str, bool], None], show_experimental: bool = False):
        self.publish = publish
        self.show_experimental = show_experimental
        self._done: Set[str] = set()
        self._cleaned: Set[str] = set()

    # ---- 对外 ----

    def _finish(self, payload: dict) -> dict:
        """给每个实体挂上设备可用性主题，设备掉线时 HA 会变灰。"""
        avail = getattr(self, "_avail_topic", None)
        if avail:
            payload.setdefault("availability_topic", avail)
            payload.setdefault("payload_available", "online")
            payload.setdefault("payload_not_available", "offline")
        return payload

    def _publish_online(self, device: dict, product_id: str, device_id: str) -> None:
        node = f"fotile_{product_id}_{device_id}_online"
        payload = {
            "name": "在线",
            "unique_id": node,
            "object_id": node,
            "device_class": "connectivity",
            "state_topic": f"fotile/availability/{device_id}",
            "payload_on": "online",
            "payload_off": "offline",
            "device": device,
        }
        self.publish(f"homeassistant/binary_sensor/{node}/config", json.dumps(payload, ensure_ascii=False), True)

    def ensure(self, profile: Profile, device_id: str, state: Dict[str, object]) -> None:
        self._avail_topic = f"fotile/availability/{device_id}"
        self._build(
            product_id=profile.product_id,
            device_id=device_id,
            state=state,
            fields=profile.fields,
            commands=profile.commands,
            power_key=profile.power_key,
            model=profile.model,
            controls=profile.controls,
            programs=profile.programs,
            text_controls=profile.text_controls,
            switches=profile.switches,
            gsc=profile.gsc,
        )

    def ensure_generic(self, product_id: str, device_id: str, state: Dict[str, object]) -> None:
        self._avail_topic = f"fotile/availability/{device_id}"
        self._build(
            product_id=product_id,
            device_id=device_id,
            state=state,
            fields=[],
            commands={},
            power_key=None,
            model="",
            controls=[],
            programs={},
            text_controls=[],
            switches=[],
            gsc={},
        )

    # ---- 内部 ----

    def _build(
        self,
        product_id: str,
        device_id: str,
        state: Dict[str, object],
        fields: List[Field],
        commands: Dict[str, dict],
        power_key: str,
        model: str,
        controls: List[dict],
        programs: Dict[str, int],
        text_controls: List[dict],
        switches: List[dict],
        gsc: Dict[str, dict],
    ) -> None:
        key = f"{product_id}:{device_id}"
        if key in self._done:
            return

        device = {
            "identifiers": [f"fotile_{device_id}"],
            "name": f"方太 {model or product_id} {device_id}",
            "manufacturer": "Fotile",
            "model": model or product_id,
        }
        state_topic = f"fotile/state/{product_id}/{device_id}"
        self._publish_online(device, product_id, device_id)
        known = {f.key for f in fields}
        published = 0

        for field in fields:
            if field.key not in state:
                continue
            self._publish_entity(field, device, state_topic, product_id, device_id)
            published += 1

        # 未在 profile 中声明的字段，按 key 自动生成传感器
        for name, value in state.items():
            if name in known or name in SKIP_KEYS:
                continue
            if not isinstance(value, (int, float, str, bool)):
                continue
            self._publish_entity(
                Field(key=name, name=name, icon="mdi:gauge"),
                device, state_topic, product_id, device_id,
            )
            published += 1

        if "power" in commands:
            self._publish_power_switch(device, state_topic, product_id, device_id, power_key or "PowerSwitchAll")
        for spec in switches:
            self._publish_state_switch(device, state_topic, product_id, device_id, spec)
        self._publish_gsc(device, product_id, device_id, gsc)
        if self.show_experimental and "appointment" in commands:
            self._publish_appointment(device, state_topic, product_id, device_id)
        self._publish_controls(device, state_topic, product_id, device_id, controls)
        if self.show_experimental:
            self._publish_text_controls(device, state_topic, product_id, device_id, text_controls)
        self._cleanup_hidden(product_id, device_id)
        if programs:
            node = f"fotile_{product_id}_{device_id}_program"
            mapping = ", ".join(
                f"'{int(v.get('WorkMode', 0))}': '{name}'" for name, v in programs.items()
            )
            payload = {
                "name": "碗碟程序",
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{product_id}/{device_id}/program",
                "retain": True,
                "options": list(programs.keys()),
                "icon": "mdi:dishwasher",
                "device": device,
            }
            self.publish(f"homeassistant/select/{node}/config", json.dumps(self._finish(payload), ensure_ascii=False), True)

        for name, spec in commands.items():
            if not isinstance(spec, dict) or "button" not in spec:
                continue
            btn = spec["button"] if isinstance(spec["button"], dict) else {}
            node = f"fotile_{product_id}_{device_id}_btn_{name}"
            payload = {
                "name": btn.get("name", name),
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{product_id}/{device_id}/set",
                "payload_press": json.dumps({name: 1}),
                "icon": btn.get("icon", "mdi:gesture-tap-button"),
                "device": device,
            }
            self.publish(f"homeassistant/button/{node}/config", json.dumps(self._finish(payload), ensure_ascii=False), True)

        self._done.add(key)
        LOG.info("discovery published for %s (%d entities)", key, published)

    def _publish_entity(self, field: Field, device: dict, state_topic: str, product_id: str, device_id: str) -> None:
        node = f"fotile_{product_id}_{device_id}_{field.key}"
        payload = {
            "name": field.name,
            "unique_id": node,
            "object_id": node,
            "state_topic": state_topic,
            "value_template": self._template(field),
            "device": device,
        }
        if field.entity == "binary_sensor":
            payload["payload_on"] = "1"
            payload["payload_off"] = "0"
        else:
            if field.unit:
                payload["unit_of_measurement"] = field.unit
            if field.device_class:
                payload["device_class"] = field.device_class
        if field.icon:
            payload["icon"] = field.icon
        if not field.enabled:
            payload["enabled_by_default"] = False
        self.publish(
            f"homeassistant/{field.entity}/{node}/config",
            json.dumps(self._finish(payload), ensure_ascii=False),
            True,
        )

    @staticmethod
    def _template(field: Field) -> str:
        """把原始值转成可读值。

        方太时间字段不是普通分钟数，而是打包编码：
          hhmm      = 小时*256 + 分钟            （时长，如 286 = 1h30m = 90 分钟）
          hhmm_time = 小时*256 + 分钟            （时间点，6144 = 24:00 = 未设置）
          hhmm_day  = (天数*24+小时)*256 + 分钟  （预约，如 12062 = 明天 23:30）
        见 profiles/1032A.json 的 _notes。
        """
        if field.decode == "hhmm_day":
            v = f"value_json.{field.key}"
            return (
                "{% set v = " + v + " %}{% set th = v // 256 %}"
                "{% set d = th // 24 %}{% set h = th % 24 %}{% set m = v % 256 %}"
                "{{ '未设置' if v == 6144 and value_json.WorkState != 6"
                " else ((('今天 ' if d == 0 else ('明天 ' if d == 1 else (d|string) ~ '天后 '))"
                " ~ ('%02d:%02d'|format(h, m))) }}"
            )
        if field.decode == "hhmm_time":
            v = f"value_json.{field.key}"
            return (
                "{{ '未设置' if " + v + " == 6144 else"
                " '%02d:%02d' | format(" + v + " // 256, " + v + " % 256) }}"
            )
        if field.decode == "hhmm":
            v = f"value_json.{field.key}"
            return (
                "{{ ((" + v + " // 256) * 60 + (" + v + " % 256))"
                " if ((" + v + " % 256) < 60 and (" + v + " // 256) < 24)"
                " else " + v + " }}"
            )
        if field.decode == "program" and field.options:
            mapping = ", ".join(f"'{k}': '{v}'" for k, v in field.options.items())
            return (
                "{% set m = {" + mapping + "} %}"
                "{{ m.get(value_json.WorkMode | string, '未知')"
                " if value_json.WorkState in [2, 6] else '无' }}"
            )
        if field.options:
            mapping = ", ".join(f"'{k}': '{v}'" for k, v in field.options.items())
            return (
                "{{ {" + mapping + "}.get(value_json." + field.key
                + " | string, value_json." + field.key + ") }}"
            )
        return f"{{{{ value_json.{field.key} }}}}"

    def _publish_controls(self, device: dict, state_topic: str, product_id: str, device_id: str, controls: list) -> None:
        for control in controls:
            kind = control.get("type", "number")
            field = control.get("field")
            if not field:
                continue
            node = f"fotile_{product_id}_{device_id}_{field}_{kind}"
            base = {
                "name": control.get("name", field),
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{product_id}/{device_id}/set",
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "device": device,
            }
            if control.get("icon"):
                base["icon"] = control["icon"]
            if control.get("param"):
                base.pop("state_topic", None)
                base.pop("value_template", None)
                base["command_topic"] = f"fotile/{product_id}/{device_id}/param"
                base["command_template"] = '{"' + field + '": {{ value }}}'
                base["min"] = control.get("min", 0)
                base["max"] = control.get("max", 100)
                base["step"] = control.get("step", 1)
                base["mode"] = "box"
                if control.get("unit"):
                    base["unit_of_measurement"] = control["unit"]
                self.publish(f"homeassistant/number/{node}/config", json.dumps(self._finish(base), ensure_ascii=False), True)
                continue
            if kind == "switch":
                base["payload_on"] = json.dumps({field: control.get("on", 1)})
                base["payload_off"] = json.dumps({field: control.get("off", 0)})
                base["state_on"] = str(control.get("on", 1))
                base["state_off"] = str(control.get("off", 0))
                topic = f"homeassistant/switch/{node}/config"
            elif kind == "button":
                base.pop("state_topic", None)
                base.pop("value_template", None)
                base["payload_press"] = json.dumps(control.get("payload") or {field: 1})
                topic = f"homeassistant/button/{node}/config"
            elif kind == "select":
                options = control.get("options") or {}
                base["options"] = list(options.keys())
                mapping = ", ".join(f"'{k}': {v}" for k, v in options.items())
                base["command_template"] = "{'x': 1}"  # placeholder, replaced below
                base["command_template"] = '{"' + field + '": {{ {' + mapping + '}[value] }}}'
                topic = f"homeassistant/select/{node}/config"
            else:
                base["command_topic"] = f"fotile/{product_id}/{device_id}/set"
                base["command_template"] = '{"' + field + '": {{ value }}}'
                base["min"] = control.get("min", 0)
                base["max"] = control.get("max", 100)
                base["step"] = control.get("step", 1)
                base["mode"] = "box"
                if control.get("unit"):
                    base["unit_of_measurement"] = control["unit"]
                topic = f"homeassistant/number/{node}/config"
            self.publish(topic, json.dumps(self._finish(base), ensure_ascii=False), True)

    def _cleanup_hidden(self, product_id: str, device_id: str) -> None:
        """把旧版本已经注册、现在默认隐藏的实体的 discovery 清空，让 HA 移除它们。"""
        key = f"{product_id}:{device_id}"
        if key in self._cleaned:
            return
        base = f"fotile_{product_id}_{device_id}"
        for topic in (
            f"homeassistant/number/{base}_appointment/config",
            f"homeassistant/button/{base}_drain/config",
            f"homeassistant/text/{base}_text_WorkMode/config",
            f"homeassistant/text/{base}_text_WorkState/config",
            f"homeassistant/sensor/{base}_StoreTotalTime/config",
        ):
            self.publish(topic, "", True)
        self._cleaned.add(key)

    def _publish_text_controls(self, device: dict, state_topic: str, product_id: str, device_id: str, text_controls: list) -> None:
        for item in text_controls:
            field = item.get("field")
            if not field:
                continue
            node = f"fotile_{product_id}_{device_id}_text_{field}"
            payload = {
                "name": item.get("name", field),
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{product_id}/{device_id}/set",
                "command_template": '{"' + field + '": {{ value | int }}}',
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "enabled_by_default": False,
                "icon": item.get("icon", "mdi:form-textbox"),
                "device": device,
            }
            self.publish(f"homeassistant/text/{node}/config", json.dumps(self._finish(payload), ensure_ascii=False), True)

    def _publish_appointment(self, device: dict, state_topic: str, product_id: str, device_id: str) -> None:
        node = f"fotile_{product_id}_{device_id}_appointment"
        payload = {
            "name": "预约时间",
            "unique_id": node,
            "object_id": node,
            "command_topic": f"fotile/{product_id}/{device_id}/set",
            "command_template": '{"appointment": {{ value }}}',
            "min": 0,
            "max": 1440,
            "step": 1,
            "mode": "box",
            "unit_of_measurement": "min",
            "icon": "mdi:clock-outline",
            "state_topic": state_topic,
            "value_template": "{{ value_json.SetAppointmentTime }}",
            "enabled_by_default": False,
            "device": device,
        }
        self.publish(f"homeassistant/number/{node}/config", json.dumps(self._finish(payload), ensure_ascii=False), True)

    def _publish_gsc(self, device: dict, product_id: str, device_id: str, gsc: dict) -> None:
        """果蔬篮（PopWash）子系统的程序选择与按钮。"""
        programs = (gsc or {}).get("programs") or {}
        if not programs:
            return
        base = f"fotile_{product_id}_{device_id}"
        select_node = f"{base}_gsc_program"
        select_payload = {
            "name": gsc.get("name", "果蔬篮程序"),
            "unique_id": select_node,
            "object_id": select_node,
            "command_topic": f"fotile/{product_id}/{device_id}/set",
            "command_template": '{"gsc_program": "{{ value }}"}',
            "state_topic": f"fotile/{product_id}/{device_id}/gsc_program",
            "value_template": "{{ value }}",
            "options": list(programs.keys()),
            "icon": gsc.get("icon", "mdi:basket"),
            "device": device,
        }
        self.publish(
            f"homeassistant/select/{select_node}/config",
            json.dumps(self._finish(select_payload), ensure_ascii=False),
            True,
        )
        for btn in gsc.get("buttons") or []:
            key = btn.get("key")
            if not key:
                continue
            node = f"{base}_{key}"
            payload = {
                "name": btn.get("name", key),
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{product_id}/{device_id}/set",
                "payload_press": json.dumps({key: 1}),
                "icon": btn.get("icon", "mdi:gesture-tap-button"),
                "device": device,
            }
            self.publish(
                f"homeassistant/button/{node}/config",
                json.dumps(self._finish(payload), ensure_ascii=False),
                True,
            )

    def _publish_state_switch(self, device: dict, state_topic: str, product_id: str, device_id: str, spec: dict) -> None:
        """把 0/1 字段做成开关：状态来自设备上报，命令走 set 主题。"""
        key = spec.get("key")
        if not key:
            return
        node = f"fotile_{product_id}_{device_id}_{str(key).lower()}"
        payload = {
            "name": spec.get("name", key),
            "unique_id": node,
            "object_id": node,
            "command_topic": f"fotile/{product_id}/{device_id}/set",
            "payload_on": json.dumps(spec.get("payload_on", {key: 1})),
            "payload_off": json.dumps(spec.get("payload_off", {key: 0})),
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "state_on": "1",
            "state_off": "0",
            "icon": spec.get("icon", "mdi:toggle-switch"),
            "enabled_by_default": bool(spec.get("enabled", True)),
            "device": device,
        }
        self.publish(
            f"homeassistant/switch/{node}/config",
            json.dumps(self._finish(payload), ensure_ascii=False),
            True,
        )

    def _publish_power_switch(self, device: dict, state_topic: str, product_id: str, device_id: str, power_key: str) -> None:
        node = f"fotile_{product_id}_{device_id}_power"
        payload = {
            "name": "电源",
            "unique_id": node,
            "object_id": node,
            "command_topic": f"fotile/{product_id}/{device_id}/set",
            "payload_on": json.dumps({"power": "on"}),
            "payload_off": json.dumps({"power": "off"}),
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{power_key} }}}}",
            "state_on": "1",
            "state_off": "0",
            "icon": "mdi:power",
            "device": device,
        }
        self.publish(
            f"homeassistant/switch/{node}/config",
            json.dumps(self._finish(payload), ensure_ascii=False),
            True,
        )
