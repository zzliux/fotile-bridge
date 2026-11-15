#!/bin/sh
set -e

CONF=/data/mosquitto.conf
OPT=/data/options.json

# 用 Python 读取 add-on 选项并生成 mosquitto 配置（设备匿名接入，仅监听局域网）
python3 - <<'PY'
import json, os

opt = {}
try:
    with open('/data/options.json') as f:
        opt = json.load(f)
except FileNotFoundError:
    pass

port = int(opt.get('mqtt_port', 1883))
bridge_host = (opt.get('ha_mqtt_host') or '').strip()
lines = [
    f'listener {port} 0.0.0.0',
    'allow_anonymous true',
    'persistence false',
    'log_dest stdout',
    'log_type error',
    'log_type warning',
]
if bridge_host:
    lines += [
        '',
        'connection ha',
        f'address {bridge_host}:{int(opt.get("ha_mqtt_port", 1883))}',
        'remote_clientid fotile_bridge',
        'cleansession true',
        'start_type automatic',
        'notifications false',
        'try_private false',
        'topic sync/# out 0',
        'topic reply/# out 0',
        'topic homeassistant/# out 0',
        'topic fotile/state/# out 0',
        'topic fotile/+/+/set in 0',
        'topic fotile/+/+/program in 0',
        'topic fotile/+/+/param in 0',
        'topic fotile/+/+/raw in 0',
    ]
    user = (opt.get('ha_mqtt_user') or '').strip()
    if user:
        # 注意：桥接认证必须用 remote_username/remote_password，username/password 是本地监听器的
        lines.append(f'remote_username {user}')
        pw = opt.get('ha_mqtt_password') or ''
        lines.append(f'remote_password {pw}')
with open('/data/mosquitto.conf', 'w') as f:
    f.write('\n'.join(lines) + '\n')
PY

mosquitto -c "$CONF" &
MQTT_PID=$!

python3 -m app.main || true
kill "$MQTT_PID" 2>/dev/null || true
