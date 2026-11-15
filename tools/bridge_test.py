#!/usr/bin/env python3
"""桥接健康检查：验证 本地broker(1885) <-> HA broker(1883) 双向是否通。

用法：
  python3 tools/bridge_test.py [HA_IP] [LOCAL_PORT] [HA_PORT] [USER] [PASS]
默认：192.168.31.150 1885 1883 fotile fotile
"""
import socket
import struct
import sys
import time
import uuid


def enc_len(n: int) -> bytes:
    out = b""
    while True:
        d = n % 128
        n //= 128
        out += bytes([d | (0x80 if n else 0)])
        if not n:
            return out


def connect(host, port, cid, user=None, pw=None):
    flags = 0x02 | (0xC0 if user else 0)
    vh = b"\x00\x04MQTT\x04" + bytes([flags]) + b"\x00\x3c"
    pl = struct.pack("!H", len(cid)) + cid
    if user:
        pl += struct.pack("!H", len(user)) + user + struct.pack("!H", len(pw or b"")) + (pw or b"")
    body = vh + pl
    s = socket.create_connection((host, port), 3)
    s.sendall(b"\x10" + enc_len(len(body)) + body)
    connack = s.recv(4)
    if len(connack) < 4 or connack[0] != 0x20:
        raise SystemExit(f"CONNECT failed on {port}: {connack!r}")
    rc = connack[3]
    if rc != 0:
        raise SystemExit(f"CONNECT refused on {port}, code {rc}")
    return s


def publish(s, topic, payload):
    body = struct.pack("!H", len(topic)) + topic + payload
    s.sendall(b"\x30" + enc_len(len(body)) + body)


def subscribe(s, topic, pid=1):
    body = struct.pack("!H", pid) + struct.pack("!H", len(topic)) + topic + b"\x00"
    s.sendall(b"\x82" + enc_len(len(body)) + body)


def wait_for(s, needle, timeout=5.0):
    s.settimeout(timeout)
    try:
        while True:
            hdr = s.recv(1)
            if not hdr:
                return False
            mult, length = 1, 0
            while True:
                b = s.recv(1)
                if not b:
                    return False
                length += (b[0] & 0x7F) * mult
                mult *= 128
                if not b[0] & 0x80:
                    break
            buf = b""
            while len(buf) < length:
                chunk = s.recv(length - len(buf))
                if not chunk:
                    return False
                buf += chunk
            if hdr[0] & 0xF0 == 0x30 and needle in buf:
                return True
    except socket.timeout:
        return False


def main():
    ha = sys.argv[1] if len(sys.argv) > 1 else "192.168.31.150"
    local_port = int(sys.argv[2]) if len(sys.argv) > 2 else 1885
    ha_port = int(sys.argv[3]) if len(sys.argv) > 3 else 1883
    user = (sys.argv[4] if len(sys.argv) > 4 else "fotile").encode()
    pw = (sys.argv[5] if len(sys.argv) > 5 else "fotile").encode()

    tag = uuid.uuid4().hex[:8].encode()
    out_topic = b"sync/BRIDGE" + tag + b"/1"
    in_topic = b"fotile/BRIDGE" + tag

    # 本地 -> HA
    sub = connect(ha, ha_port, b"bt_sub_out", user, pw)
    subscribe(sub, b"sync/#")
    pub = connect(ha, local_port, b"bt_pub_out")
    publish(pub, out_topic, b'{"ok":1}')
    time.sleep(0.3)
    ok_out = wait_for(sub, out_topic)
    sub.close(); pub.close()

    # HA -> 本地
    sub2 = connect(ha, local_port, b"bt_sub_in")
    subscribe(sub2, b"fotile/#")
    pub2 = connect(ha, ha_port, b"bt_pub_in", user, pw)
    publish(pub2, in_topic, b'{"ok":1}')
    time.sleep(0.3)
    ok_in = wait_for(sub2, in_topic)
    sub2.close(); pub2.close()

    print(f"local->HA: {'OK' if ok_out else 'FAIL'}")
    print(f"HA->local: {'OK' if ok_in else 'FAIL'}")
    if not (ok_out and ok_in):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
