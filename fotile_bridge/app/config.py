"""配置加载：优先 /data/options.json（HA add-on），其次环境变量，便于本地测试。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class Config:
    http_port: int = 8088
    mqtt_port: int = 1883
    advertise_host: str = ""
    upstream_ip: str = "101.37.40.179"
    learn_mode: bool = True
    ha_mqtt_host: str = ""
    ha_mqtt_port: int = 1883
    ha_mqtt_user: str = ""
    ha_mqtt_password: str = ""
    log_level: str = "info"
    data_dir: str = "/data"
    # 云端影子设备（让方太 App 显示在线）
    mirror_enabled: bool = False
    mirror_broker: str = "121.41.80.81"
    mirror_broker_port: int = 1883
    mirror_device_id: str = ""
    mirror_product_id: str = "1032A"
    mirror_password: str = ""


def load_config(path: str = "/data/options.json") -> Config:
    raw = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

    cfg = Config(
        http_port=int(raw.get("http_port", os.environ.get("FOTILE_HTTP_PORT", 8088))),
        mqtt_port=int(raw.get("mqtt_port", os.environ.get("FOTILE_MQTT_PORT", 1883))),
        advertise_host=raw.get("advertise_host") or os.environ.get("FOTILE_ADVERTISE_HOST", ""),
        upstream_ip=raw.get("upstream_ip") or os.environ.get("FOTILE_UPSTREAM_IP", "101.37.40.179"),
        learn_mode=bool(raw.get("learn_mode", os.environ.get("FOTILE_LEARN_MODE", "1") not in ("0", "false"))),
        ha_mqtt_host=raw.get("ha_mqtt_host") or os.environ.get("FOTILE_HA_MQTT_HOST", ""),
        ha_mqtt_port=int(raw.get("ha_mqtt_port", os.environ.get("FOTILE_HA_MQTT_PORT", 1883))),
        ha_mqtt_user=raw.get("ha_mqtt_user") or os.environ.get("FOTILE_HA_MQTT_USER", ""),
        ha_mqtt_password=raw.get("ha_mqtt_password") or os.environ.get("FOTILE_HA_MQTT_PASSWORD", ""),
        log_level=raw.get("log_level", os.environ.get("FOTILE_LOG_LEVEL", "info")),
        data_dir=os.environ.get("FOTILE_DATA_DIR", "/data"),
        mirror_enabled=bool(raw.get("mirror_enabled", False)),
        mirror_broker=raw.get("mirror_broker") or os.environ.get("FOTILE_MIRROR_BROKER", "121.41.80.81"),
        mirror_broker_port=int(raw.get("mirror_broker_port", 1883)),
        mirror_device_id=str(raw.get("mirror_device_id") or ""),
        mirror_product_id=str(raw.get("mirror_product_id") or "1032A"),
        mirror_password=str(raw.get("mirror_password") or ""),
    )
    return cfg
