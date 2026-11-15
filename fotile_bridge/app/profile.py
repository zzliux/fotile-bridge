"""物模型 profile：每个 productId 一份，描述状态字段与命令映射。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Field:
    key: str
    name: str = ""
    unit: str = ""
    device_class: str = ""
    icon: str = ""
    entity: str = "sensor"  # sensor | binary_sensor
    options: Dict[str, str] = field(default_factory=dict)  # 原始值 -> 可读文案
    enabled: bool = True

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "Field":
        return cls(
            key=key,
            name=data.get("name", key),
            unit=data.get("unit", ""),
            device_class=data.get("device_class", ""),
            icon=data.get("icon", ""),
            entity=data.get("entity", "sensor"),
            options={str(k): str(v) for k, v in (data.get("values") or {}).items()},
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Profile:
    product_id: str
    name: str = ""
    fields: List[Field] = field(default_factory=list)
    commands: Dict[str, dict] = field(default_factory=dict)
    # 设备上报里出现即视为 binary 的键（0/1）
    power_key: str = "PowerSwitchAll"
    model: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        fields = [Field.from_dict(k, v) for k, v in (data.get("fields") or {}).items()]
        return cls(
            product_id=data["productId"],
            name=data.get("name", ""),
            fields=fields,
            commands=data.get("commands") or {},
            power_key=data.get("powerKey", "PowerSwitchAll"),
            model=data.get("model", ""),
        )


class ProfileStore:
    def __init__(self, directory: str):
        self.directory = directory
        self._cache: Dict[str, Profile] = {}

    def get(self, product_id: str) -> Optional[Profile]:
        product_id = (product_id or "").upper()
        if product_id in self._cache:
            return self._cache[product_id]
        path = os.path.join(self.directory, f"{product_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            profile = Profile.from_dict(json.load(f))
        self._cache[product_id] = profile
        return profile
