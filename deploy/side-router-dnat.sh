#!/bin/sh
# 在旁路由上把洗碗机的 HTTP(80) 透明转发到本地最小云（HA:8088）。
# 只影响指定源 IP，不改 DNS，不影响手机 App 访问真实云。
#
# 用法： ./side-router-dnat.sh apply|rollback|status
set -e

DEV="${DEV:-192.168.31.241}"     # 洗碗机
HA="${HA:-192.168.31.150}"       # Home Assistant
PORT="${PORT:-8088}"             # add-on http_port

apply() {
  iptables -t nat -C PREROUTING -s "$DEV" -p tcp --dport 80 \
    -j DNAT --to-destination "$HA:$PORT" 2>/dev/null || \
  iptables -t nat -A PREROUTING -s "$DEV" -p tcp --dport 80 \
    -j DNAT --to-destination "$HA:$PORT"

  # 回程必须经过旁路由，否则 HA 直接回给设备会因源地址不符被 RST
  iptables -t nat -C POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
    -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
    -j MASQUERADE
  echo "applied: $DEV:80 -> $HA:$PORT"
}

rollback() {
  iptables -t nat -D PREROUTING -s "$DEV" -p tcp --dport 80 \
    -j DNAT --to-destination "$HA:$PORT" 2>/dev/null || true
  iptables -t nat -D POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
    -j MASQUERADE 2>/dev/null || true
  echo "rolled back"
}

status() {
  iptables -t nat -S PREROUTING | grep "$DEV" || echo "no PREROUTING rule"
  iptables -t nat -S POSTROUTING | grep "$DEV" || echo "no POSTROUTING rule"
}

case "${1:-status}" in
  apply) apply ;;
  rollback) rollback ;;
  status) status ;;
  *) echo "usage: $0 apply|rollback|status"; exit 1 ;;
esac
