"""MQTT Discovery：根据 profile 把设备状态字段映射成 HA 实体。"""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, Set

from .profile import Profile

LOG = logging.getLogger(__name__)


class Discovery:
    def __init__(self, publish: Callable[[str, str, bool], None]):
        self.publish = publish
        self._done: Set[str] = set()

    def ensure(self, profile: Profile, device_id: str, state: Dict[str, object]) -> None:
        key = f"{profile.product_id}:{device_id}"
        if key in self._done:
            return
        device = {
            "identifiers": [f"fotile_{device_id}"],
            "name": f"方太 {profile.name or profile.product_id} {device_id}",
            "manufacturer": "Fotile",
            "model": profile.product_id,
        }
        state_topic = f"sync/{profile.product_id}/{device_id}"
        for field in profile.fields:
            if field.key not in state:
                continue
            node = f"fotile_{profile.product_id}_{device_id}_{field.key}"
            payload = {
                "name": field.name,
                "unique_id": node,
                "object_id": node,
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{field.key} }}}}",
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
            topic = f"homeassistant/{field.entity}/{node}/config"
            self.publish(topic, json.dumps(payload, ensure_ascii=False), True)

        if "power" in profile.commands:
            node = f"fotile_{profile.product_id}_{device_id}_power"
            payload = {
                "name": "电源",
                "unique_id": node,
                "object_id": node,
                "command_topic": f"fotile/{profile.product_id}/{device_id}/set",
                "payload_on": json.dumps({"power": "on"}),
                "payload_off": json.dumps({"power": "off"}),
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{profile.power_key} }}}}",
                "state_on": "1",
                "state_off": "0",
                "icon": "mdi:power",
                "device": device,
            }
            self.publish(f"homeassistant/switch/{node}/config", json.dumps(payload, ensure_ascii=False), True)

        self._done.add(key)
        LOG.info("discovery published for %s (%d fields)", key, len(profile.fields))
