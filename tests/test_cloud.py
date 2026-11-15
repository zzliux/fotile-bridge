"""最小云端点测试（pytest-asyncio）。"""
from __future__ import annotations

import os
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fotile_bridge"))

from app.cloud import CloudServer  # noqa: E402
from app.config import Config  # noqa: E402
from app.tsl import TslStore  # noqa: E402


@pytest.fixture
def server(tmp_path):
    cfg = Config(http_port=0, mqtt_port=1883, advertise_host="192.168.1.50", learn_mode=False, data_dir=str(tmp_path))
    return CloudServer(cfg, TslStore(str(tmp_path)))


@pytest.mark.asyncio
async def test_new_device_login(server):
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.post("/v2/new_device_login", json={"deviceId": "1149539722"})
        data = await resp.json()
    assert resp.status == 200
    assert data["deviceId"] == "1149539722"
    assert data["token"]
    assert data["schemaVersion"].startswith("V002")


@pytest.mark.asyncio
async def test_time_sync(server):
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.post("/v5/time_sync/", json={})
        data = await resp.json()
    assert data["timestampMs"] > 0
    assert len(data["timestampString"]) == 14


@pytest.mark.asyncio
async def test_route_service_points_to_local_broker(server):
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.post("/iot-mqttManager/routeService", json={"deviceId": "1149539722"})
        data = await resp.json()
    assert data[0]["ip"] == "192.168.1.50"
    assert data[0]["port"] == 1883
    names = [t["name"] for t in data[0]["topics"]]
    assert "control/{product_id}/{device_id}" in names
    assert data[0]["vendorId"] == "Fotile"
    assert data[0]["keepalived"] == 30


@pytest.mark.asyncio
async def test_access_new_envelope(server):
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.post("/v3/device/access/new", json={"deviceId": "1149539722"})
        data = await resp.json()
    assert data["code"] == 200
    assert data["success"] is True
    assert data["data"]["mqttIp"] == "192.168.1.50"


@pytest.mark.asyncio
async def test_tsl_cache_hit(server):
    server.tsl.put("1032A", {"AL": [{"n": "PowerSwitchAll"}]})
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.post("/v2/tsl/query/product", json={"productId": "1032A"})
        data = await resp.json()
    assert data["AL"][0]["n"] == "PowerSwitchAll"
