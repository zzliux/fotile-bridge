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
    )
    return cfg
