"""MQTT 透明中转：设备 ↔ 方太云。

设备以为自己在连云端 broker，实际连的是这里；这里再连真实 broker。
- 逐包转发（MQTT 3.1/3.1.1），旁听 PUBLISH
- 设备上报的 sync 注入本地 broker，供 HA 使用
- HA 的控制报文注入到设备方向的连接，设备当成云端下发执行

这样设备始终是"真在线"（App 正常），密码/会话由设备自己维护，不需要抓凭据。
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
from typing import Callable, Optional

from .config import Config

LOG = logging.getLogger(__name__)

SO_ORIGINAL_DST = 80


def original_dst(sock: socket.socket) -> Optional[tuple]:
    try:
        raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    except OSError:
        return None
    if len(raw) < 8:
        return None
    port = struct.unpack("!H", raw[2:4])[0]
    ip = ".".join(str(b) for b in raw[4:8])
    if ip == "0.0.0.0" or port == 0:
        return None
    return ip, port


class MqttSplitter:
    """把字节流切成完整的 MQTT 包。"""

    def __init__(self):
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while len(self.buf) >= 2:
            mult, length, i = 1, 0, 1
            while i < len(self.buf):
                b = self.buf[i]
                length += (b & 0x7F) * mult
                mult *= 128
                i += 1
                if not b & 0x80:
                    break
            else:
                break
            if i + length > len(self.buf):
                break
            out.append(self.buf[: i + length])
            self.buf = self.buf[i + length :]
        return out


def publish_packet(topic: str, payload: bytes) -> bytes:
    t = topic.encode()
    body = struct.pack("!H", len(t)) + t + payload
    rem = len(body)
    enc = b""
    while True:
        d = rem % 128
        rem //= 128
        enc += bytes([d | (0x80 if rem else 0)])
        if not rem:
            break
    return b"\x30" + enc + body


class MqttRelay:
    def __init__(self, cfg: Config, local_publish: Callable[[str, str, bool], None]):
        self.cfg = cfg
        self.local_publish = local_publish
        self.server: Optional[asyncio.AbstractServer] = None
        self.device_writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "0.0.0.0", self.cfg.relay_port)
        self._loop = asyncio.get_running_loop()
        self._start_local_control()
        LOG.info("mqtt relay listening on :%s", self.cfg.relay_port)

    def _start_local_control(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fotile_relay_ctl")
        client.on_connect = lambda c, u, f, rc, p=None: c.subscribe("control/#", qos=0)
        client.on_message = self._on_local_control
        client.connect("127.0.0.1", self.cfg.mqtt_port, keepalive=30)
        client.loop_start()
        self.local_client = client
        LOG.info("relay watching local control/#")

    def _on_local_control(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        fut = asyncio.run_coroutine_threadsafe(self.inject(msg.topic, payload), self._loop)
        fut.add_done_callback(lambda f: f.exception() and LOG.warning("inject failed: %s", f.exception()))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        dst = original_dst(sock) if sock is not None else None
        upstream_host, upstream_port = dst or (self.cfg.relay_upstream, 1883)
        LOG.info("relay connection -> %s:%s", upstream_host, upstream_port)
        try:
            up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
        except OSError as exc:
            LOG.error("relay upstream failed: %s", exc)
            writer.close()
            return

        self.device_writer = writer
        splitter_dev = MqttSplitter()
        splitter_up = MqttSplitter()

        async def dev_to_up():
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                up_writer.write(data)
                await up_writer.drain()
                for pkt in splitter_dev.feed(data):
                    self._tap_device(pkt)

        async def up_to_dev():
            while True:
                data = await up_reader.read(65536)
                if not data:
                    break
                for pkt in splitter_up.feed(data):
                    self._tap_cloud(pkt)
                    writer.write(pkt)
                    await writer.drain()

        try:
            await asyncio.gather(dev_to_up(), up_to_dev())
        finally:
            self.device_writer = None
            for w in (writer, up_writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
            LOG.info("relay connection closed")

    def _tap_device(self, pkt: bytes) -> None:
        topic, payload = self._parse_publish(pkt)
        if topic and topic.startswith("sync/") and payload:
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                return
            self.local_publish(topic, text, False)

    def _tap_cloud(self, pkt: bytes) -> None:
        topic, payload = self._parse_publish(pkt)
        if topic and topic.startswith("control/") and payload:
            LOG.info("cloud control %s %s", topic, payload[:120])

    @staticmethod
    def _parse_publish(pkt: bytes):
        if not pkt or pkt[0] >> 4 != 3:
            return None, None
        i = 1
        length, mult = 0, 1
        while i < len(pkt):
            b = pkt[i]
            length += (b & 0x7F) * mult
            mult *= 128
            i += 1
            if not b & 0x80:
                break
        body = pkt[i:]
        if len(body) < 2:
            return None, None
        tlen = struct.unpack("!H", body[:2])[0]
        topic = body[2 : 2 + tlen].decode("utf-8", "replace")
        rest = body[2 + tlen :]
        qos = (pkt[0] >> 1) & 3
        if qos:
            rest = rest[2:]
        return topic, rest

    async def inject(self, topic: str, payload: str) -> bool:
        writer = self.device_writer
        if writer is None:
            LOG.warning("inject skipped: no device connection")
            return False
        async with self._lock:
            writer.write(publish_packet(topic, payload.encode()))
            await writer.drain()
        LOG.info("injected -> device %s %s", topic, payload[:120])
        return True

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
