"""TSL 种子加载测试。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fotile_bridge"))

from app.tsl import TslStore  # noqa: E402

SEED = os.path.join(os.path.dirname(__file__), "..", "fotile_bridge", "app", "tsl_seed")


def test_seed_loaded(tmp_path):
    store = TslStore(str(tmp_path))
    store.seed_from(SEED)
    data = store.get("1032A")
    assert data is not None
    names = [item["n"] for item in data["AL"]]
    assert "PowerSwitchAll" in names
    assert data["version"].startswith("V002")
