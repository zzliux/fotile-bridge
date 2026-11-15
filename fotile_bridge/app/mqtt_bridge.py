"""设备 MQTT 接入 + HA 命令桥。

设备连本地 mosquitto（1883），本模块：
  - 订阅 sync/#，把状态喂给 Discovery
  - 订阅 fotile/{pid}/{did}/set，把 HA 命令翻译成 control/... 报文
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .config import Config
from .discovery import Discovery
from .profile import ProfileStore

LOG = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, cfg: Config, profiles: ProfileStore):
        self.cfg = cfg
        self.profiles = profiles
        self.discovery = Discovery(self.publish)
        self.client = None

    # ---- paho ----

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fotile_bridge")
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect("127.0.0.1", self.cfg.mqtt_port, keepalive=30)
        client.loop_start()
        self.client = client
        LOG.info("bridge connected to local broker :%s", self.cfg.mqtt_port)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe("sync/#")
        client.subscribe("fotile/+/+/set")
        LOG.info("subscribed sync/# and fotile/+/+/set")

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self.client is not None:
            self.client.publish(topic, payload, qos=0, retain=retain)

    # ---- 消息 ----

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            LOG.debug("non-json message on %s", topic)
            return

        if topic.startswith("sync/"):
            self._handle_sync(topic, payload)
        elif topic.startswith("fotile/") and topic.endswith("/set"):
            self._handle_command(topic, payload)

    def _handle_sync(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        if len(parts) != 3:
            return
        product_id, device_id = parts[1].upper(), parts[2]
        profile = self.profiles.get(product_id)
        if profile is None:
            # 没有 profile 的型号也不丢数据：按字段名自动生成实体
            LOG.info("no profile for product %s, using generic discovery", product_id)
            self.discovery.ensure_generic(product_id, device_id, payload)
            return
        self.discovery.ensure(profile, device_id, payload)

    def _handle_command(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        if len(parts) != 4:
            return
        product_id, device_id = parts[1].upper(), parts[2]
        profile = self.profiles.get(product_id)
        control_topic = f"control/{product_id}/{device_id}"

        if "raw" in payload and isinstance(payload["raw"], dict):
            self.publish(control_topic, json.dumps(payload["raw"]), False)
            LOG.info("raw command -> %s %s", control_topic, payload["raw"])
            return

        if profile is None or not profile.commands:
            # 没有映射时按原样下发，方便调试
            self.publish(control_topic, json.dumps(payload), False)
            LOG.info("passthrough command -> %s %s", control_topic, payload)
            return

        for name, value in payload.items():
            spec = profile.commands.get(name)
            if spec is None:
                continue
            options = spec.get("options") or {}
            body = options.get(str(value), spec.get("payload"))
            if body is None:
                continue
            self.publish(control_topic, json.dumps(body), False)
            LOG.info("mapped command %s=%s -> %s", name, value, body)
