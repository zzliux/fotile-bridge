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


def publish_packet(topic: str, payload: bytes, packet_id: int = 0) -> bytes:
    t = topic.encode()
    if packet_id:
        body = struct.pack("!H", len(t)) + t + struct.pack("!H", packet_id) + payload
        first = 0x32  # PUBLISH QoS1
    else:
        body = struct.pack("!H", len(t)) + t + payload
        first = 0x30  # PUBLISH QoS0
    rem = len(body)
    enc = b""
    while True:
        d = rem % 128
        rem //= 128
        enc += bytes([d | (0x80 if rem else 0)])
        if not rem:
            break
    return bytes([first]) + enc + body


class MqttRelay:
    def __init__(self, cfg: Config, local_publish: Callable[[str, str, bool], None]):
        self.cfg = cfg
        self.local_publish = local_publish
        self.server: Optional[asyncio.AbstractServer] = None
        self.device_writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._active = 0
        self._pid = 60000
        self._pending_pids = set()
        self._cloud_writer = None
        self._cloud_creds = None
        self._cloud_lock = asyncio.Lock()

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
        # nftables DNAT 下 SO_ORIGINAL_DST 不可靠（会指回自己），统一用配置的云端 broker
        upstream_host, upstream_port = self.cfg.relay_upstream, 1883
        if upstream_host in ("127.0.0.1", "0.0.0.0", self.cfg.advertise_host):
            LOG.error("relay upstream misconfigured: %s", upstream_host)
            writer.close()
            return
        LOG.info("relay connection -> %s:%s", upstream_host, upstream_port)
        if self._active >= 4:
            LOG.warning("relay too many connections, dropping")
            writer.close()
            return
        self._active += 1
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(upstream_host, upstream_port), timeout=10
            )
        except (OSError, asyncio.TimeoutError) as exc:
            LOG.error("relay upstream failed: %s", exc)
            writer.close()
            self._active -= 1
            return

        self.device_writer = writer
        splitter_dev = MqttSplitter()
        splitter_up = MqttSplitter()

        async def dev_to_up():
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for pkt in splitter_dev.feed(data):
                    if self._consume_puback(pkt):
                        continue
                    self._tap_device(pkt)
                    up_writer.write(pkt)
                    await up_writer.drain()

        async def up_to_dev():
            while True:
                data = await up_reader.read(65536)
                if not data:
                    break
                for pkt in splitter_up.feed(data):
                    self._tap_cloud(pkt)
                    async with self._lock:
                        writer.write(pkt)
                        await writer.drain()

        try:
            await asyncio.gather(dev_to_up(), up_to_dev())
        finally:
            self._active = max(0, self._active - 1)
            self.device_writer = None
            for w in (writer, up_writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
            LOG.info("relay connection closed")

    def _parse_connect(self, pkt: bytes):
        """从设备的 CONNECT 里取出云端账号密码，供控制发布使用。"""
        if not pkt or pkt[0] >> 4 != 1:
            return None
        i = 1
        while i < len(pkt) and pkt[i] & 0x80:
            i += 1
        i += 1
        body = pkt[i:]
        try:
            pos = 0
            plen = struct.unpack("!H", body[pos:pos + 2])[0]
            pos += 2 + plen
            flags = body[pos + 1]
            pos += 4  # level + flags + keepalive
            clen = struct.unpack("!H", body[pos:pos + 2])[0]
            pos += 2 + clen
            if flags & 0x04:  # will
                wl = struct.unpack("!H", body[pos:pos + 2])[0]
                pos += 2 + wl
                ml = struct.unpack("!H", body[pos:pos + 2])[0]
                pos += 2 + ml
            user = None
            pw = None
            if flags & 0x80:
                ul = struct.unpack("!H", body[pos:pos + 2])[0]
                user = body[pos + 2:pos + 2 + ul]
                pos += 2 + ul
            if flags & 0x40:
                pl = struct.unpack("!H", body[pos:pos + 2])[0]
                pw = body[pos + 2:pos + 2 + pl]
            return user, pw
        except (struct.error, IndexError):
            return None

    def _tap_device(self, pkt: bytes) -> None:
        creds = self._parse_connect(pkt)
        if creds and creds[0] and creds[1]:
            self._cloud_creds = creds
            LOG.info("captured device cloud credentials")
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

    def _consume_puback(self, pkt: bytes) -> bool:
        """吞掉设备对我们注入的 QoS1 报文的 PUBACK，避免流向云端。"""
        if not pkt or pkt[0] >> 4 != 4:
            return False
        i = 1
        while i < len(pkt) and pkt[i] & 0x80:
            i += 1
        i += 1
        if len(pkt) < i + 2:
            return False
        pid = struct.unpack("!H", pkt[i:i + 2])[0]
        if pid in self._pending_pids:
            self._pending_pids.discard(pid)
            LOG.debug("consumed PUBACK for injected packet id=%s", pid)
            return True
        return False

    async def _ensure_cloud(self):
        if self._cloud_creds is None:
            return None
        async with self._cloud_lock:
            if self._cloud_writer is not None:
                return self._cloud_writer
            user, pw = self._cloud_creds
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.relay_upstream, 1883), timeout=10
            )
            cid = b"fotile_ha_bridge"
            vh = b"\x00\x06MQIsdp\x03" + bytes([0x02 | 0xC0]) + b"\x00\x3c"
            body = vh + struct.pack("!H", len(cid)) + cid
            body += struct.pack("!H", len(user)) + user
            body += struct.pack("!H", len(pw)) + pw
            rem = len(body)
            enc = b""
            while True:
                d = rem % 128
                rem //= 128
                enc += bytes([d | (0x80 if rem else 0)])
                if not rem:
                    break
            writer.write(b"\x10" + enc + body)
            await writer.drain()
            connack = await asyncio.wait_for(reader.read(4), timeout=5)
            if len(connack) < 4 or connack[3] != 0:
                writer.close()
                LOG.warning("cloud control connect rejected: %s", connack.hex())
                return None
            self._cloud_writer = writer
            LOG.info("cloud control channel ready")
            return writer

    async def inject(self, topic: str, payload: str) -> bool:
        writer = await self._ensure_cloud()
        if writer is None:
            LOG.warning("inject skipped: no cloud channel")
            return False
        self._pid += 1
        if self._pid > 65000:
            self._pid = 60001
        writer.write(publish_packet(topic, payload.encode(), self._pid))
        await writer.drain()
        LOG.info("published control via cloud %s %s", topic, payload[:120])
        return True

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
