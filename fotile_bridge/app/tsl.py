"""TSL（物模型）缓存：首次透传真实云时落盘，之后离线返回。"""
from __future__ import annotations

import json
import os
from typing import Optional


class TslStore:
    def __init__(self, data_dir: str):
        self.directory = os.path.join(data_dir, "tsl")
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, product_id: str) -> str:
        return os.path.join(self.directory, f"{product_id.upper()}.json")

    def get(self, product_id: str) -> Optional[dict]:
        path = self._path(product_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def seed_from(self, directory: str) -> None:
        """把随包附带的 TSL 样本复制进缓存（不覆盖已有文件）。"""
        if not os.path.isdir(directory):
            return
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            product_id = name[:-5].upper()
            if self.get(product_id) is not None:
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as f:
                    self.put(product_id, json.load(f))
            except (OSError, ValueError):
                continue

    def put(self, product_id: str, data: dict) -> None:
        path = self._path(product_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
