"""MQTT 透明中转：设备 ↔ 方太云。

设备以为自己在连云端 broker，实际连的是这里；这里再连真实 broker。
- 逐包转发（MQTT 3.1/3.1.1），旁听 PUBLISH
- 设备上报的 sync 注入本地 broker，供 HA 使用
- HA 的控制报文注入到设备方向的连接，设备当成云端下发执行

这样设备始终是"真在线"（App 正常），密码/会话由设备自己维护，不需要抓凭据。
"""
from __future__ import annotations

import asyncio
import os
import time
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
        self._cloud_used = None
        self._cloud_lock = asyncio.Lock()
        self._avail_cache_path = os.path.join(cfg.data_dir, "availability.json")
        self._avail_cache: dict[str, float] = self._load_avail_cache()
        self._cache_written_at = 0.0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "0.0.0.0", self.cfg.relay_port)
        self._loop = asyncio.get_running_loop()
        self._start_local_control()
        asyncio.create_task(self._restore_availability())
        LOG.info("mqtt relay listening on :%s", self.cfg.relay_port)

    def _start_local_control(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fotile_relay_ctl")
        def _on_connect(c, u, f, rc, p=None):
            c.subscribe("control/#", qos=0)
            # 启动时把残留的 online 清掉，避免 add-on 重启后误报在线
            c.subscribe("fotile/availability/#", qos=0)

        client.on_connect = _on_connect
        client.on_message = self._on_local_control
        client.connect("127.0.0.1", self.cfg.mqtt_port, keepalive=30)
        client.loop_start()
        self.local_client = client
        LOG.info("relay watching local control/#")

    def _on_local_control(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        # 可用性由 relay 自己维护（_restore_availability / mark_online），
        # 这里只忽略，避免把 availability 当成控制报文转发回云端
        if msg.topic.startswith("fotile/availability/"):
            return
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
        state = {"did": None, "last": time.monotonic()}

        def mark_online(did: str) -> None:
            state["did"] = did
            state["last"] = time.monotonic()
            self._publish_avail(did, "online")

        def mark_offline() -> None:
            did = state.get("did")
            if did:
                self._publish_avail(did, "offline")
                state["did"] = None

        async def dev_to_up():
            while True:
                try:
                    data = await reader.read(65536)
                except (ConnectionResetError, OSError):
                    break
                if not data:
                    break
                state["last"] = time.monotonic()
                for pkt in splitter_dev.feed(data):
                    if self._consume_puback(pkt):
                        continue
                    creds = self._parse_connect(pkt)
                    if creds and creds[0]:
                        try:
                            mark_online(creds[0].decode())
                        except UnicodeDecodeError:
                            pass
                    self._tap_device(pkt)
                    up_writer.write(pkt)
                    await up_writer.drain()

        async def up_to_dev():
            while True:
                try:
                    data = await up_reader.read(65536)
                except (ConnectionResetError, OSError):
                    break
                if not data:
                    break
                state["last"] = time.monotonic()
                for pkt in splitter_up.feed(data):
                    self._tap_cloud(pkt)
                    async with self._lock:
                        writer.write(pkt)
                        await writer.drain()

        async def watchdog():
            # 设备 Wi-Fi 掉线时 TCP 可能没有 FIN，靠静默超时判定离线
            while True:
                await asyncio.sleep(15)
                if time.monotonic() - state["last"] > 180:
                    LOG.warning("relay device silent for 180s, closing connection")
                    try:
                        writer.close()
                        up_writer.close()
                    except Exception:  # noqa: BLE001
                        pass
                    return

        wd = asyncio.create_task(watchdog())
        try:
            await asyncio.gather(dev_to_up(), up_to_dev())
        except (ConnectionResetError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.warning("relay connection error: %s", exc)
        finally:
            wd.cancel()
            mark_offline()
            self._active = max(0, self._active - 1)
            self.device_writer = None
            for w in (writer, up_writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
            LOG.info("relay connection closed")

    def _publish_avail(self, did: str, value: str) -> None:
        self._touch_avail(did, value == "online")
        try:
            self.local_publish(f"fotile/availability/{did}", value, True)
            LOG.info("device %s -> %s", did, value)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("availability publish failed: %s", exc)

    def _load_avail_cache(self) -> dict[str, float]:
        """读取上次记录的设备活跃时间（add-on 重启后用于恢复在线状态）。"""
        try:
            with open(self._avail_cache_path, encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError):
            return {}

    def _touch_avail(self, did: str, online: bool) -> None:
        if not did:
            return
        is_new = did not in self._avail_cache
        self._avail_cache[did] = time.time() if online else 0.0
        # 在线心跳很频繁，落盘做节流；离线或首次出现立即落盘
        now = time.monotonic()
        if online and not is_new and now - self._cache_written_at < 30:
            return
        self._cache_written_at = now
        try:
            with open(self._avail_cache_path, "w", encoding="utf-8") as f:
                json.dump(self._avail_cache, f)
        except OSError as exc:  # noqa: BLE001
            LOG.warning("availability cache write failed: %s", exc)

    async def _restore_availability(self) -> None:
        """add-on 重启后先按上次活跃时间恢复在线状态，避免设备重连前的空窗。"""
        await asyncio.sleep(1.5)
        self._restore_availability_now()

    def _restore_availability_now(self) -> None:
        fresh = float(self.cfg.availability_fresh)
        now = time.time()
        for did, ts in list(self._avail_cache.items()):
            age = now - ts if ts else None
            if age is not None and age <= fresh:
                LOG.info("restore availability %s -> online (active %ds ago)", did, int(age))
                self._publish_avail(did, "online")
            else:
                LOG.info(
                    "restore availability %s -> offline (last active: %s)",
                    did,
                    f"{int(age)}s ago" if age is not None else "never",
                )
                self._publish_avail(did, "offline")

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
            # 设备在持续上报状态，说明它在线
            self._touch_avail(topic.rsplit("/", 1)[-1], True)
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

    def _drop_cloud(self):
        if self._cloud_writer is not None:
            try:
                self._cloud_writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._cloud_writer = None
        self._cloud_used = None

    async def _ensure_cloud(self):
        if self._cloud_creds is None:
            return None
        async with self._cloud_lock:
            if self._cloud_writer is not None and not self._cloud_writer.is_closing():
                if self._cloud_used == self._cloud_creds:
                    return self._cloud_writer
                LOG.info("device credentials rotated, reconnecting cloud control channel")
                self._drop_cloud()
            user, pw = self._cloud_creds
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.relay_upstream, 1883), timeout=10
            )
            cid = b"fotile_ha_bridge"
            # keepalive=0：空闲时不会被云端按 90s 超时踢掉
            vh = b"\x00\x06MQIsdp\x03" + bytes([0x02 | 0xC0]) + b"\x00\x00"
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
            self._cloud_used = self._cloud_creds
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
        try:
            writer.write(publish_packet(topic, payload.encode(), self._pid))
            await writer.drain()
        except (ConnectionResetError, OSError, RuntimeError) as exc:
            LOG.warning("cloud control write failed, reconnecting: %s", exc)
            async with self._cloud_lock:
                self._drop_cloud()
            writer = await self._ensure_cloud()
            if writer is None:
                return False
            writer.write(publish_packet(topic, payload.encode(), self._pid))
            await writer.drain()
        LOG.info("published control via cloud %s %s", topic, payload[:120])
        return True

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
