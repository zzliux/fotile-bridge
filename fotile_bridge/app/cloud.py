"""最小云：冒充 api.fotile.com 的设备侧 HTTP 接口。

设备开机只需要几个接口：
  POST /v2/new_device_login         -> 会话（token/refreshToken）
  POST /v3/device/access/new        -> 信封 + mqttIp（可带）
  POST /iot-mqttManager/routeService-> MQTT broker 地址与 topic 列表
  POST /v2/tsl/query/product        -> 物模型，未知时透传真实云并缓存
其余请求在 learn 模式下原样转发到真实云。
"""
from __future__ import annotations

import json
import logging
import secrets
import socket
import time
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, web

from .config import Config
from .tsl import TslStore

LOG = logging.getLogger(__name__)

ROUTE_TOPICS = [
    {"name": "service/{product_id}/{device_id}", "level": 1, "indentifier": "service", "isSubPub": 1},
    {"name": "control/{product_id}/{device_id}", "level": 1, "indentifier": "control", "isSubPub": 1},
    {"name": "sync/{product_id}/{device_id}", "level": 1, "indentifier": "sync", "isSubPub": 2},
    {"name": "CustomEvent/{device_id}", "level": 1, "indentifier": "CustomEvent", "isSubPub": 1},
    {"name": "reply/{product_id}/{device_id}", "level": 1, "indentifier": "reply", "isSubPub": 2},
]

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _ms() -> int:
    return int(time.time() * 1000)


def _rid() -> str:
    return secrets.token_hex(16)


def _detect_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class CloudServer:
    def __init__(self, cfg: Config, tsl: TslStore):
        self.cfg = cfg
        self.tsl = tsl
        self.host = cfg.advertise_host or _detect_lan_ip()
        self.session: Optional[ClientSession] = None
        self.app = web.Application()
        r = self.app.router
        r.add_post("/v5/time_sync/", self.time_sync)
        r.add_post("/v2/time_sync", self.time_sync)
        r.add_post("/v2/new_device_login", self.new_device_login)
        r.add_post("/v3/device/access/new", self.access_new)
        r.add_route("*", "/iot-mqttManager/routeService", self.route_service)
        r.add_post("/v2/tsl/query/product", self.tsl_query)
        r.add_post("/v3/tsl/query/product", self.tsl_query)
        r.add_route("*", "/{tail:.*}", self.proxy)

    # ---- 工具 ----

    async def body_json(self, request: web.Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def route_service_payload(self, device_id: str) -> list:
        return [{
            "ip": self.host,
            "port": self.cfg.mqtt_port,
            "vendorId": "Fotile",
            "clientId": f"Fotile_DEV_{device_id}",
            "keepalived": 30,
            "topics": ROUTE_TOPICS,
        }]

    # ---- 设备接口 ----

    async def time_sync(self, request: web.Request) -> web.Response:
        now = _ms()
        return web.json_response({
            "timestampString": time.strftime("%Y%m%d%H%M%S", time.localtime()),
            "timestampSeconds": now // 1000,
            "timestampMs": now,
        })

    async def new_device_login(self, request: web.Request) -> web.Response:
        data = await self.body_json(request)
        device_id = str(data.get("deviceId") or data.get("device_id") or "")
        now = _ms()
        token = secrets.token_hex(24)
        LOG.info("new_device_login device=%s -> %s:%s", device_id, self.host, self.cfg.mqtt_port)
        # 字段与真实云端对齐：token 24h、refreshToken 约 45min、familyId 固定
        return web.json_response({
            "currentTimeStamp": now,
            "deviceId": device_id,
            "familyId": int(device_id) if device_id.isdigit() else 0,
            "refreshToken": token,
            "refreshTokenTimeStamp": now + 2678400,
            "schemaVersion": "V002-2026.1.21",
            "token": token,
            "tokenTimeStamp": now + 86400000,
        })

    async def access_new(self, request: web.Request) -> web.Response:
        data = await self.body_json(request)
        LOG.info("device/access/new %s", {k: data.get(k) for k in ("deviceId", "productId") if k in data})
        return web.json_response({
            "msg": "success",
            "code": 200,
            "success": True,
            "requestId": _rid(),
            "data": {
                "schemaVersion": "V001",
                "mqttIp": self.host,
                "unbinded": 3,
                "timestampMs": _ms(),
            },
        })

    async def route_service(self, request: web.Request) -> web.Response:
        data = await self.body_json(request)
        device_id = str(data.get("deviceId") or data.get("device_id") or "")
        LOG.info("routeService device=%s -> %s:%s", device_id, self.host, self.cfg.mqtt_port)
        return web.json_response(self.route_service_payload(device_id))

    async def tsl_query(self, request: web.Request) -> web.Response:
        data = await self.body_json(request)
        product_id = str(data.get("productId") or data.get("product_id") or "").upper()
        if product_id:
            cached = self.tsl.get(product_id)
            if cached is not None:
                LOG.info("tsl hit cache product=%s", product_id)
                return web.json_response(cached)
            if self.cfg.learn_mode:
                upstream = await self.forward_raw(request, await request.read())
                if upstream is not None:
                    status, headers, body = upstream
                    try:
                        parsed = json.loads(body)
                    except ValueError:
                        parsed = None
                    if status == 200 and isinstance(parsed, (dict, list)):
                        self.tsl.put(product_id, parsed if isinstance(parsed, dict) else {"data": parsed})
                        LOG.info("tsl learned product=%s", product_id)
                    return web.Response(status=status, body=body, headers=headers)
        LOG.warning("tsl miss product=%s learn=%s", product_id, self.cfg.learn_mode)
        return web.json_response({"AL": [], "SL": []})

    # ---- 透传 ----

    async def proxy(self, request: web.Request) -> web.Response:
        if not self.cfg.learn_mode:
            return web.json_response({"error": {"msg": "not found", "code": 404}}, status=404)
        raw = await request.read()
        if LOG.isEnabledFor(logging.DEBUG) and raw:
            LOG.debug("proxy body %s %s %s", request.method, request.path_qs, raw[:400])
        upstream = await self.forward_raw(request, raw)
        if upstream is None:
            return web.json_response({"error": {"msg": "upstream unreachable", "code": 502}}, status=502)
        status, headers, body = upstream
        LOG.debug("proxy %s %s -> %s", request.method, request.path_qs, status)
        if LOG.isEnabledFor(logging.DEBUG) and body and len(body) < 2000:
            LOG.debug("proxy resp %s %s", request.path_qs, body[:600])
        return web.Response(status=status, body=body, headers=headers)

    async def forward_raw(self, request: web.Request, body: bytes):
        if self.session is None:
            self.session = ClientSession(timeout=ClientTimeout(total=10))
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Host"] = "api.fotile.com"
        url = f"http://{self.cfg.upstream_ip}{request.path_qs}"
        try:
            async with self.session.request(request.method, url, headers=headers, data=body) as resp:
                payload = await resp.read()
                out_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding", "connection")
                }
                return resp.status, out_headers, payload
        except Exception as exc:  # noqa: BLE001
            LOG.warning("upstream error %s: %s", url, exc)
            return None

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.cfg.http_port)
        await site.start()
        LOG.info("minimal cloud listening on :%s (advertise %s)", self.cfg.http_port, self.host)
        self._runner = runner

    async def stop(self) -> None:
        await self.close()
        runner = getattr(self, "_runner", None)
        if runner is not None:
            await runner.cleanup()
