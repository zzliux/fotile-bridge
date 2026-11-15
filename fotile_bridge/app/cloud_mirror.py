"""云端影子设备：让方太 App 里显示在线。

设备本身连本地 broker（离线也能用），本模块再用**设备自己的凭据**登录方太云：
  - 把本地设备上报的 sync 状态镜像到云端（App 看到真实状态）
  - 订阅云端的 control/service/CustomEvent，把 App 下发的命令转给本地设备
  - 设置 will 消息，add-on 掉线时云端显示离线

这样 App 和本地控制同时可用；云端仍然是“设备在线”，只是这个设备是我们模拟的。
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from .config import Config

LOG = logging.getLogger(__name__)


class CloudMirror:
    def __init__(self, cfg: Config, local_publish: Callable[[str, str, bool], None]):
        self.cfg = cfg
        self.local_publish = local_publish
        self.client = None
        self.device_id = cfg.mirror_device_id
        self.product_id = cfg.mirror_product_id.upper()
        self.state_topic = f"sync/{self.product_id}/{self.device_id}"

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.mirror_enabled and self.device_id and self.cfg.mirror_password)

    def start(self) -> None:
        if not self.enabled:
            return
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"Fotile_DEV_{self.device_id}",
            protocol=mqtt.MQTTv31,
        )
        client.username_pw_set(self.device_id, self.cfg.mirror_password)
        client.will_set(self.state_topic, json.dumps({"offline": 0}), qos=1, retain=False)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.connect(self.cfg.mirror_broker, self.cfg.mirror_broker_port, keepalive=90)
        client.loop_start()
        self.client = client
        LOG.info("cloud mirror connected to %s:%s as %s", self.cfg.mirror_broker, self.cfg.mirror_broker_port, self.device_id)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        topics = [
            f"control/{self.product_id}/{self.device_id}",
            f"service/{self.product_id}/{self.device_id}",
            f"CustomEvent/{self.device_id}",
        ]
        client.subscribe([(t, 1) for t in topics])
        LOG.info("cloud mirror subscribed %s", topics)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        LOG.warning("cloud mirror disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        # 云端 → 本地：App 的控制指令
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            return
        self.local_publish(msg.topic, payload, False)
        LOG.info("cloud -> local %s %s", msg.topic, payload[:120])

    def publish_state(self, product_id: str, device_id: str, state: dict) -> None:
        if not self.enabled or self.client is None:
            return
        if product_id.upper() != self.product_id or str(device_id) != str(self.device_id):
            return
        self.client.publish(self.state_topic, json.dumps(state, ensure_ascii=False), qos=1)
        LOG.debug("mirrored state to cloud: %s", state)
