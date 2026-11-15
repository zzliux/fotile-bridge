#!/usr/bin/env python3
"""极简 pcap -> MQTT 解析器（方太洗碗机流量用）"""
import struct, sys, collections

def read_pcap(path):
    data = open(path, 'rb').read()
    magic = data[:4]
    if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1'):
        endian = '<'
    elif magic in (b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
        endian = '>'
    else:
        raise SystemExit(f'unsupported pcap magic {magic!r}')
    off = 24
    while off + 16 <= len(data):
        ts, tus, incl, orig = struct.unpack(endian + 'IIII', data[off:off+16])
        off += 16
        pkt = data[off:off+incl]
        off += incl
        yield ts + tus / 1e6, pkt

def parse_frame(pkt):
    if len(pkt) < 14:
        return None
    ethertype = struct.unpack('!H', pkt[12:14])[0]
    l3 = 14
    if ethertype == 0x8100:
        ethertype = struct.unpack('!H', pkt[16:18])[0]
        l3 = 18
    if ethertype != 0x0800:
        return None
    ip = pkt[l3:]
    if len(ip) < 20 or ip[9] != 6:
        return None
    ihl = (ip[0] & 0x0f) * 4
    src = '.'.join(map(str, ip[12:16]))
    dst = '.'.join(map(str, ip[16:20]))
    tcp = ip[ihl:]
    if len(tcp) < 20:
        return None
    sport, dport = struct.unpack('!HH', tcp[:4])
    doff = (tcp[12] >> 4) * 4
    return src, sport, dst, dport, tcp[doff:]

def mqtt_parse(buf):
    out = []
    off = 0
    while off + 2 <= len(buf):
        b0 = buf[off]
        mult, val, i = 1, 0, 1
        while off + i < len(buf):
            byte = buf[off + i]
            val += (byte & 0x7f) * mult
            mult *= 128
            i += 1
            if not byte & 0x80:
                break
        if off + i + val > len(buf):
            break
        body = buf[off + i: off + i + val]
        out.append((b0, body))
        off += i + val
    return out

def show(tag, b0, body):
    t = b0 >> 4
    if t == 1:
        print(f'[{tag}] CONNECT {body[:150]!r}')
    elif t == 3:
        qos = (b0 >> 1) & 3
        if len(body) < 2:
            return
        tl = struct.unpack('!H', body[:2])[0]
        topic = body[2:2+tl].decode('utf-8', 'replace')
        rest = body[2+tl:]
        if qos:
            rest = rest[2:]
        try:
            txt = rest.decode('utf-8')
        except UnicodeDecodeError:
            txt = rest.decode('utf-8', 'replace')
        print(f'[{tag}] PUBLISH qos={qos} topic="{topic}" len={len(rest)}')
        print(f'      {txt[:500]}')
        if rest[:1] not in (b'{', b'['):
            print(f'      hex={rest[:120].hex()}')
    elif t == 8:
        print(f'[{tag}] SUBSCRIBE {body[:250]!r}')
    elif t == 9:
        print(f'[{tag}] SUBACK {body[:50]!r}')
    elif t == 12:
        print(f'[{tag}] PINGREQ')
    elif t == 13:
        print(f'[{tag}] PINGRESP')
    elif t == 2:
        print(f'[{tag}] CONNACK {body[:20]!r}')
    elif t == 4:
        print(f'[{tag}] PUBACK {body[:20]!r}')

def main(path):
    flows = collections.defaultdict(lambda: collections.defaultdict(int))
    bufs = collections.defaultdict(lambda: b'')
    for ts, pkt in read_pcap(path):
        r = parse_frame(pkt)
        if not r:
            continue
        src, sport, dst, dport, payload = r
        if not payload:
            continue
        if 1883 in (sport, dport):
            server = dst if dport == 1883 else src
            direction = 'dev->srv' if dport == 1883 else 'srv->dev'
            flows[server][direction] += 1
            bufs[(server, direction)] += payload
    for (server, direction), buf in sorted(bufs.items()):
        print(f'\n=== {server} {direction} ({len(buf)} bytes, {flows[server][direction]} pkts) ===')
        for b0, body in mqtt_parse(buf):
            show(direction, b0, body)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'fotile.pcap')
