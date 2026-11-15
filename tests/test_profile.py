"""profile 加载测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fotile_bridge"))

from app.profile import ProfileStore  # noqa: E402

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "fotile_bridge", "app", "profiles")


def test_load_dishwasher_profile():
    store = ProfileStore(PROFILE_DIR)
    profile = store.get("1032a")
    assert profile is not None
    assert profile.product_id == "1032A"
    keys = {f.key for f in profile.fields}
    assert "ClRemTime" in keys
    assert "DoorLockSta" in keys


def test_unknown_product_returns_none():
    store = ProfileStore(PROFILE_DIR)
    assert store.get("XXXX") is None
