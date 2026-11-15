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
    'per_listener_settings false',
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
        'topic sync/# out 0',
        'topic reply/# out 0',
        'topic homeassistant/# out 0',
        'topic fotile/# in 0',
        'try_private false',
    ]
    user = (opt.get('ha_mqtt_user') or '').strip()
    if user:
        lines.append(f'username {user}')
        pw = opt.get('ha_mqtt_password') or ''
        lines.append(f'password {pw}')
with open('/data/mosquitto.conf', 'w') as f:
    f.write('\n'.join(lines) + '\n')
PY

mosquitto -c "$CONF" &
MQTT_PID=$!

python3 -m app.main || true
kill "$MQTT_PID" 2>/dev/null || true
