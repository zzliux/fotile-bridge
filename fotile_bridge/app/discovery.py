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
    def __init__(self, publish: Callable[[str, str, bool], None]):
        self.publish = publish
        self._done: Set[str] = set()

    # ---- 对外 ----

    def ensure(self, profile: Profile, device_id: str, state: Dict[str, object]) -> None:
        self._build(
            product_id=profile.product_id,
            device_id=device_id,
            state=state,
            fields=profile.fields,
            commands=profile.commands,
            power_key=profile.power_key,
            model=profile.model,
        )

    def ensure_generic(self, product_id: str, device_id: str, state: Dict[str, object]) -> None:
        self._build(
            product_id=product_id,
            device_id=device_id,
            state=state,
            fields=[],
            commands={},
            power_key=None,
            model="",
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
            json.dumps(payload, ensure_ascii=False),
            True,
        )

    @staticmethod
    def _template(field: Field) -> str:
        if field.options:
            mapping = ", ".join(f"'{k}': '{v}'" for k, v in field.options.items())
            return (
                "{{ {" + mapping + "}.get(value_json." + field.key
                + " | string, value_json." + field.key + ") }}"
            )
        return f"{{{{ value_json.{field.key} }}}}"

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
            json.dumps(payload, ensure_ascii=False),
            True,
        )
