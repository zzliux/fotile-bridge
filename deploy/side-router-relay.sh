#!/bin/sh
# 把洗碗机的 MQTT(1883) 转到 HA 上的透明中转（fotile_bridge relay）。
# 设备仍然连真实云端，密码/会话由设备自己维护；中转只旁听 + 注入控制。
# 用法： ./side-router-relay.sh apply|rollback|status
set -e

DEV="${DEV:-192.168.31.241}"
HA="${HA:-192.168.31.150}"
PORT="${PORT:-1886}"      # add-on relay_port
TABLE="fotile_relay"

apply_nft() {
  nft delete table ip "$TABLE" 2>/dev/null || true
  nft add table ip "$TABLE"
  nft add chain ip "$TABLE" prerouting '{ type nat hook prerouting priority -101 ; policy accept ; }'
  nft add rule ip "$TABLE" prerouting ip saddr "$DEV" tcp dport 1883 counter dnat to "$HA:$PORT"
  nft add chain ip "$TABLE" postrouting '{ type nat hook postrouting priority 101 ; policy accept ; }'
  nft add rule ip "$TABLE" postrouting ip saddr "$DEV" ip daddr "$HA" tcp dport "$PORT" counter masquerade
  echo "applied: $DEV:1883 -> $HA:$PORT"
}

rollback() {
  nft delete table ip "$TABLE" 2>/dev/null || true
  echo "rolled back"
}

status() {
  nft list table ip "$TABLE" 2>/dev/null || echo "no table"
}

case "${1:-status}" in
  apply) apply_nft ;;
  rollback) rollback ;;
  status) status ;;
  *) echo "usage: $0 apply|rollback|status"; exit 1 ;;
esac
