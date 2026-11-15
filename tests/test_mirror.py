"""云端影子设备测试（不会真的连接云）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fotile_bridge"))

from app.cloud_mirror import CloudMirror  # noqa: E402
from app.config import Config  # noqa: E402


def test_disabled_without_password():
    sent = []
    mirror = CloudMirror(Config(mirror_enabled=True, mirror_device_id="1", mirror_password=""), lambda *a: sent.append(a))
    assert mirror.enabled is False
    mirror.publish_state("1032A", "1", {"PowerSwitchAll": 1})
    assert sent == []


def test_enabled_with_config():
    cfg = Config(mirror_enabled=True, mirror_device_id="1149539722", mirror_password="x", mirror_broker="127.0.0.1")
    mirror = CloudMirror(cfg, lambda *a: None)
    assert mirror.enabled is True
    assert mirror.state_topic == "sync/1032A/1149539722"
