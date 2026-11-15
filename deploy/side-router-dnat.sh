#!/bin/sh
# 在旁路由上把洗碗机的 HTTP(80) 透明转发到本地最小云（HA:8088）。
# 只影响指定源 IP，不改 DNS，不影响手机 App 访问真实云。
#
# 兼容：ImmortalWrt/OpenWrt 24.x 默认 nftables（本脚本用 nft）；老版本有 iptables 时回退。
# 用法： ./side-router-dnat.sh apply|rollback|status
set -e

DEV="${DEV:-192.168.31.241}"     # 洗碗机
HA="${HA:-192.168.31.150}"       # Home Assistant
PORT="${PORT:-8088}"             # add-on http_port
TABLE="fotile_bridge"

apply_nft() {
  nft delete table ip "$TABLE" 2>/dev/null || true
  nft -f - <<EOF
table ip $TABLE {
  chain prerouting {
    type nat hook prerouting priority -101; policy accept;
    ip saddr $DEV tcp dport 80 dnat to $HA:$PORT
  }
  chain postrouting {
    type nat hook postrouting priority 101; policy accept;
    ip saddr $DEV ip daddr $HA tcp dport $PORT masquerade
  }
}
EOF
  echo "applied (nft): $DEV:80 -> $HA:$PORT"
}

apply_iptables() {
  iptables -t nat -C PREROUTING -s "$DEV" -p tcp --dport 80 \
    -j DNAT --to-destination "$HA:$PORT" 2>/dev/null || \
  iptables -t nat -A PREROUTING -s "$DEV" -p tcp --dport 80 \
    -j DNAT --to-destination "$HA:$PORT"
  iptables -t nat -C POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
    -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
    -j MASQUERADE
  echo "applied (iptables): $DEV:80 -> $HA:$PORT"
}

case "${1:-status}" in
  apply)
    if command -v nft >/dev/null 2>&1; then apply_nft; else apply_iptables; fi
    ;;
  rollback)
    nft delete table ip "$TABLE" 2>/dev/null || true
    iptables -t nat -D PREROUTING -s "$DEV" -p tcp --dport 80 \
      -j DNAT --to-destination "$HA:$PORT" 2>/dev/null || true
    iptables -t nat -D POSTROUTING -s "$DEV" -d "$HA" -p tcp --dport "$PORT" \
      -j MASQUERADE 2>/dev/null || true
    echo "rolled back"
    ;;
  status)
    nft list table ip "$TABLE" 2>/dev/null || echo "no nft table"
    iptables -t nat -S 2>/dev/null | grep "$DEV" || true
    ;;
  *) echo "usage: $0 apply|rollback|status"; exit 1 ;;
esac
