#!/usr/bin/env python3
"""从 pcap 提取 HTTP 请求/响应（明文 80 端口），用于方太设备开机流程分析。"""
import struct
import sys
from collections import defaultdict


def read_pcap(path):
    data = open(path, 'rb').read()
    if data[:4] in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1'):
        endian = '<'
    elif data[:4] in (b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
        endian = '>'
    else:
        raise SystemExit('unsupported pcap')
    off = 24
    while off + 16 <= len(data):
        ts, tus, incl, orig = struct.unpack(endian + 'IIII', data[off:off + 16])
        off += 16
        pkt = data[off:off + incl]
        off += incl
        yield ts + tus / 1e6, pkt


def parse(pkt):
    if len(pkt) < 14 or struct.unpack('!H', pkt[12:14])[0] != 0x0800:
        return None
    ip = pkt[14:]
    if len(ip) < 20 or ip[9] != 6:
        return None
    ihl = (ip[0] & 0x0f) * 4
    src = '.'.join(map(str, ip[12:16]))
    dst = '.'.join(map(str, ip[16:20]))
    tcp = ip[ihl:]
    if len(tcp) < 20:
        return None
    sp, dp = struct.unpack('!HH', tcp[:4])
    seq = struct.unpack('!I', tcp[4:8])[0]
    doff = (tcp[12] >> 4) * 4
    return src, sp, dst, dp, seq, tcp[doff:]


def tcp_streams(path):
    flows = defaultdict(list)
    for ts, pkt in read_pcap(path):
        r = parse(pkt)
        if not r:
            continue
        src, sp, dst, dp, seq, payload = r
        if 80 not in (sp, dp):
            continue
        key = tuple(sorted([(src, sp), (dst, dp)]))
        flows[key].append((seq, src, sp, payload))
    streams = {}
    for key, segs in flows.items():
        by_dir = defaultdict(list)
        for seq, src, sp, payload in segs:
            if payload:
                by_dir[(src, sp)].append((seq, payload))
        for peer, items in by_dir.items():
            items.sort(key=lambda x: x[0])
            streams[peer] = b''.join(p for _, p in items)
    return streams


def split_http(buf):
    pos = 0
    while pos < len(buf):
        end = buf.find(b'\r\n\r\n', pos)
        if end < 0:
            break
        head = buf[pos:end].decode('utf-8', 'replace')
        lines = head.split('\r\n')
        start = lines[0]
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        clen = int(headers.get('content-length', 0) or 0)
        body = buf[end + 4:end + 4 + clen]
        yield start, headers, body
        if 'chunked' in headers.get('transfer-encoding', ''):
            break
        pos = end + 4 + clen


def main(path):
    streams = tcp_streams(path)
    for (src, sp), buf in streams.items():
        if not buf.startswith((b'GET ', b'POST ', b'HTTP/')):
            continue
        print(f'\n######## {src}:{sp} ({len(buf)} bytes) ########')
        for start, headers, body in split_http(buf):
            print(f'>>> {start}')
            for k in ('access-token', 'token', 'deviceid', 'productid', 'content-type', 'version'):
                if k in headers:
                    print(f'    {k}: {headers[k]}')
            if body:
                text = body.decode('utf-8', 'replace')
                print(text[:3000])
                print()


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'fotile_boot.pcap')
