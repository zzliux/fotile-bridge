# Fotile Local Bridge

把方太（Fotile）智能家电从“厂商云”接管到本地 Home Assistant 的桥接服务。

- 设备仍然以为自己连的是方太云：HTTP 走 `api.fotile.com:80`，MQTT 走 1883
- 实际连的是旁路由 / HA 上的本服务：**不出网、断网可用、不需要 App 登录**
- HA 通过标准 MQTT 集成接入，实体用 MQTT Discovery 自动生成
- 不依赖网易易盾、模拟器、抓包或账号风控

## 架构

```
┌──────────┐   DNS: api.fotile.com → HA      ┌──────────────────────────────┐
│ 方太设备  │ ───────────────────────────────▶ │  fotile_bridge (HA add-on)   │
│          │   HTTP :8088  (最小云)            │  ├─ aiohttp 最小云 / TSL 缓存  │
│          │   MQTT :1883  (routeService 返回) │  ├─ mosquitto (设备接入)      │
└──────────┘                                  │  └─ MQTT bridge → HA broker   │
                                              └──────────────┬───────────────┘
                                                             │ sync/# control/#
                                                      ┌──────▼───────┐
                                                      │ Home Assistant│
                                                      │  MQTT 集成    │
                                                      └──────────────┘
```

设备开机流程：

1. `POST /v2/time_sync`、`/v2/new_device_login` → 本地返回会话（含伪造 token）
2. `POST /v3/device/access/new` → 本地返回，不带 mqttIp
3. `POST /iot-mqttManager/routeService` → 返回**本地** MQTT broker 地址
4. `POST /v2/tsl/query/product` → 返回缓存的物模型；没有缓存时透传真实云端并落盘
5. 设备连本地 MQTT，订阅 `control/{pid}/{did}` 等主题，向 `sync/{pid}/{did}` 上报状态

## 快速开始（HAOS）

1. 把本仓库添加为 HA Add-on 仓库：设置 → 加载项 → 仓库 → 添加 `https://github.com/<you>/fotile-local-bridge`
2. 安装 **Fotile Local Bridge**，配置本机 IP 和 HA MQTT 账号后启动
3. 旁路由上做两件事：
   - DNS：`api.fotile.com` → HA 的 IP
   - DNAT：设备访问 `api.fotile.com:80` → `HA:8088`
4. 给设备断电重连，HA 里会自动出现实体

## 配置

| 选项 | 说明 |
|---|---|
| `http_port` | 最小云 HTTP 端口，默认 8088 |
| `mqtt_port` | 设备接入的 MQTT 端口，默认 1883 |
| `advertise_host` | `routeService` 返回给设备的 IP（HA 的局域网 IP） |
| `upstream_ip` | 真实方太云 IP，用于 `learn` 模式透传（默认 `101.37.40.179`） |
| `learn_mode` | 开启时未知请求/TSL 会透传到真实云并缓存 |
| `ha_mqtt_host/user/password` | 桥接到 HA 现有 broker（留空则不桥接） |

## 控制

状态：设备向 `sync/{productId}/{deviceId}` 发 JSON，HA 直接订阅。

控制：HA 向 `fotile/{productId}/{deviceId}/set` 发命令，桥接服务按 `profiles/<productId>.json`
里的 `commands` 映射成设备认识的 `control/...` 报文；没有映射时可用 `raw` 直接发 JSON：

```yaml
topic: fotile/1032A/1149539722/set
payload: '{"raw": {"PowerSwitchAll": 2}}'
```

> 各型号的控制报文格式不同，抓一次 App 下发即可补进 profile，之后长期有效。

## 目录

```
fotile_bridge/           # HA add-on（Dockerfile / config.yaml / app）
  app/cloud.py           # 最小云 HTTP 端点 + TSL 透传缓存
  app/mqtt_bridge.py     # 设备 broker + HA 桥接 + Discovery
  app/profiles/          # 各型号物模型与命令映射
tests/                   # 本地可跑的单元测试
docs/                    # 抓包记录、协议说明
```

## 已知限制

- 目前 profile 只覆盖洗碗机 `1032A`（字段来自实机抓包）
- 控制指令映射待补（需要一次 App 下发抓包）
- 设备固件升级后可能需要重新落一份 TSL

## License

MIT

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q

# 不装 add-on 也能单独跑最小云（默认 :8088）
FOTILE_HTTP_PORT=8088 FOTILE_DATA_DIR=./data \
  .venv/bin/python -m app.main
```

最小云会自己探测局域网 IP 作为 `advertise_host`；设备接入前确认路由器 DNS/DNAT 已指向这台机器。

## 路线图

- [x] 最小云：login / access / routeService / tsl 缓存
- [x] 设备 MQTT 接入 + HA Discovery（状态字段）
- [ ] 抓一次 App 控制下发，补齐 `control/` 命令映射
- [ ] TSL 自动学习（learn 模式已验证透传路径）
- [ ] 支持更多方太品类（烟机/烤箱/蒸箱），按 productId 加 profile
