"""设备 MQTT 接入 + HA 命令桥。

设备连本地 mosquitto（1883），本模块：
  - 订阅 sync/#，把状态喂给 Discovery
  - 订阅 fotile/{pid}/{did}/set，把 HA 命令翻译成 control/... 报文
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .config import Config
from .discovery import Discovery
from .profile import ProfileStore

LOG = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, cfg: Config, profiles: ProfileStore):
        self.cfg = cfg
        self.profiles = profiles
        self.discovery = Discovery(self.publish, show_experimental=cfg.show_experimental)
        self.client = None
        self._state = {}  # (product, device) -> 合并后的状态
        self._programs = {}  # device_id -> 选中的程序名
        self._params = {}  # device_id -> {字段: 值}，启动时带上
        self.mirror = None

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
        client.subscribe("fotile/state/#")
        client.subscribe("fotile/+/+/set")
        client.subscribe("fotile/+/+/raw")
        client.subscribe("fotile/+/+/program")
        client.subscribe("fotile/+/+/param")
        LOG.info("subscribed sync/#, fotile/state/#, fotile/+/+/set")

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self.client is not None:
            self.client.publish(topic, payload, qos=0, retain=retain)

    # ---- 消息 ----

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        raw = msg.payload.decode("utf-8", "replace")
        if topic.endswith("/program"):
            # select 可能发原始字符串，也可能发 JSON
            name = raw
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    name = str(parsed.get("program") or "")
                elif isinstance(parsed, str):
                    name = parsed
            except ValueError:
                pass
            name = name.strip().strip('"')
            parts = topic.split("/")
            if len(parts) == 4:
                self._programs[parts[2]] = name
                LOG.info("selected program %s for %s", name, parts[2])
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            LOG.debug("non-json message on %s", topic)
            return

        if topic.startswith("sync/"):
            self._handle_sync(topic, payload)
        elif topic.startswith("fotile/state/"):
            self._handle_retained_state(topic, payload)
        elif topic.startswith("fotile/") and topic.endswith("/param"):
            parts = topic.split("/")
            if len(parts) == 4 and isinstance(payload, dict):
                self._params.setdefault(parts[2], {}).update(payload)
                LOG.info("param set for %s: %s", parts[2], payload)
        elif topic.startswith("fotile/") and topic.endswith("/program"):
            parts = topic.split("/")
            if len(parts) == 4:
                self._programs[parts[2]] = str(payload.get("program") or "")
                LOG.info("selected program %s for %s", self._programs[parts[2]], parts[2])
        elif topic.startswith("fotile/") and topic.endswith("/raw"):
            self._handle_raw(topic, payload)
        elif topic.startswith("fotile/") and topic.endswith("/set"):
            self._handle_command(topic, payload)

    def _handle_sync(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        if len(parts) != 3:
            return
        product_id, device_id = parts[1].upper(), parts[2]
        key = (product_id, device_id)
        merged = dict(self._state.get(key) or {})
        merged.update(payload)
        self._state[key] = merged

        # 保留合并后的状态，HA 重启后也能直接拿到数值
        self.publish(f"fotile/state/{product_id}/{device_id}", json.dumps(merged, ensure_ascii=False), True)

        # 镜像到方太云（App 显示在线）
        if self.mirror is not None:
            self.mirror.publish_state(product_id, device_id, merged)

        profile = self.profiles.get(product_id)
        if profile is None:
            LOG.info("no profile for product %s, using generic discovery", product_id)
            self.discovery.ensure_generic(product_id, device_id, merged)
            return
        self.discovery.ensure(profile, device_id, merged)

    def _handle_retained_state(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        if len(parts) != 4:
            return
        product_id, device_id = parts[2].upper(), parts[3]
        self._state[(product_id, device_id)] = dict(payload)
        profile = self.profiles.get(product_id)
        if profile is None:
            self.discovery.ensure_generic(product_id, device_id, payload)
        else:
            self.discovery.ensure(profile, device_id, payload)

    def _handle_raw(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        if len(parts) != 4:
            return
        control_topic = f"control/{parts[1].upper()}/{parts[2]}"
        self.publish(control_topic, json.dumps(payload, ensure_ascii=False), False)
        LOG.info("raw command -> %s %s", control_topic, payload)

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

        if profile is not None and "start_program" in payload:
            name = self._programs.get(device_id, "")
            spec = (profile.programs or {}).get(name)
            if not spec:
                # add-on 重启后 HA 不会重发未变化的 select 值，回退到第一个程序（日常洗）
                fallback = next(iter(profile.programs or {}), "")
                if fallback:
                    LOG.warning("no program selected for %s, fallback to %s", device_id, fallback)
                    name, spec = fallback, profile.programs[fallback]
            if not spec:
                LOG.warning("unknown program %s for %s", name, device_id)
                return
            params = self._params.get(device_id, {})
            store = params.get("StoreTotalTime")
            if store is None:
                store = self._state.get((profile.product_id, device_id), {}).get("StoreTotalTime", 10)
            body = {
                "WorkMode": int(spec.get("WorkMode", 0)),
                "WorkState": 1,
                "AddFunL": int(spec.get("AddFunL", 1)),
                "StoreTotalTime": int(store) if spec.get("store") else 0,
                "PowerSwitchAll": 1,
            }
            self.publish(control_topic, json.dumps(body), False)
            LOG.info("start selected program %s (WorkMode=%s)", name, body["WorkMode"])
            return

        # 语义化命令：动态值直接写进 TSL 字段
        if "appointment" in payload:
            try:
                minutes = int(payload["appointment"])
            except (TypeError, ValueError):
                return
            self.publish(control_topic, json.dumps({"SetAppointmentTime": minutes}), False)
            LOG.info("appointment=%s -> %s", minutes, control_topic)
            return
        if profile is not None:
            known = {f.key for f in profile.fields}
            if payload and all(k in known for k in payload):
                self.publish(control_topic, json.dumps(payload), False)
                # 同步更新本地缓存，紧接着启动程序时能用上新值
                key = (profile.product_id, device_id)
                merged = dict(self._state.get(key) or {})
                merged.update(payload)
                self._state[key] = merged
                LOG.info("field command -> %s %s", control_topic, payload)
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
            if isinstance(spec, dict) and spec.get("sequence"):
                seq = spec["sequence"]
                delay = float(spec.get("delay_ms", 0)) / 1000.0
                for idx, item in enumerate(seq):
                    self.publish(control_topic, json.dumps(item), False)
                    LOG.info("sequence %s step %s -> %s", name, idx + 1, item)
                    if delay and idx < len(seq) - 1:
                        time.sleep(delay)
                continue
            options = spec.get("options") or {}
            body = options.get(str(value), spec.get("payload"))
            if body is None:
                continue
            self.publish(control_topic, json.dumps(body), False)
            LOG.info("mapped command %s=%s -> %s", name, value, body)
