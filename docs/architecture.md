# 抓包记录与协议说明

> 2026-11-15，方太洗碗机（`productId=1032A`，`deviceId=1149539722`，MAC `44:0f:b4:e1:26:d6`）
> 在旁路由（ImmortalWrt 24.10）上抓到的真实流量。全部为明文，无需解密。

## 网络拓扑

```
洗碗机 192.168.31.241 ──网关 192.168.31.2（旁路由）──▶ 方太云
```

## MQTT 接入

- broker：`121.41.80.81:1883`（另见过 `47.97.26.28`，云端有多个入口）
- 协议：MQTT 3.1（`MQIsdp`），keepalive 90

CONNECT 报文：

| 字段 | 值 |
|---|---|
| ClientId | `Fotile_DEV_1149539722` |
| Will Topic | `sync/1032A/1149539722` |
| Will Payload | `{"offline":0}` |
| Username | `1149539722` |
| Password | `Pwd...`（设备侧混淆串，非明文密码） |

SUBSCRIBE：

```
service/1032A/1149539722
control/1032A/1149539722
CustomEvent/1149539722
productSettings/1032A
```

PUBLISH（设备 → 云），主题 `sync/1032A/1149539722`：

```json
{"PowerSwitchAll":1,"WorkState":2,"RunState":1,"WorkMode":2,"AddFunL":1,
 "StoreTotalTime":7,"SetAppointmentTime":0,"SoftensaltLevel":0,"DoorLockSta":1,
 "Reminder":0,"ErrorNum":0,"RemoteAuth":1,"AutoDrain":0,"ClRemTime":9,
 "StRemTime":168,"SetWorkTime":286,"GSCPowerSwitch":1,"GSCWorkSta":1,
 "GSCRunSta":0,"GSCWorkMode":0,"GSCSetWorkT":18,"GSCClRemT":18,
 "GSCDoorLockSta":0,"GSCAutoDrain":0,"GSCErrorNum":0,"ValleyAppoint":65535,
 "ValleyMode":0,"ValleyAddFunL":1,"ValleyStoreTime":7,"AllowRemoteControl":0,
 "AuthType":1,"NewRemoteAuth":3}
```

运行中会按分钟增量推送，例如 `{"ClRemTime":8}`、`{"ClRemTime":7}`。

云 → 设备，主题 `productSettings/1032A`：

```json
{"optionalPropertiesSetting":{"totalLength":0,"properties":[
   {"identifier":"AllowRemoteControl","offset":40,"length":1},
   {"identifier":"AuthType","offset":41,"length":1},
   {"identifier":"NewRemoteAuth","offset":42,"length":1}]},
 "version":3,
 "networkAuthSetting":{"localControlTime":5,"authType":"3"}}
```

`networkAuthSetting` 疑似是局域网控制的授权策略（授权时长/类型），后续可研究直接用本地授权，
不必完全顶替云端。

## HTTP 接口（设备开机）

设备访问 `http://api.fotile.com`（80，明文）：

| 接口 | 用途 |
|---|---|
| `/v2/new_device_login` | 设备登录，返回 token/refreshToken |
| `/v3/device/access/new` | 返回信封 + `data.mqttIp` |
| `/iot-mqttManager/routeService` | 返回 MQTT broker 地址与 topic 列表 |
| `/v2/tsl/query/product` | 拉取物模型（需要 Access-Token） |

`new_device_login` 返回结构（扁平，无 code 信封）：

```json
{"currentTimeStamp":1789825059898,"deviceId":"1149539722","familyId":"...",
 "refreshToken":"...","refreshTokenTimeStamp":1789827738298,"schemaVersion":"V001",
 "token":"...","tokenTimeStamp":1789911459898}
```

`routeService` 返回数组，topic 列表里的键名是 `indentifier`（官方接口拼写如此）。

## 待确认

- `control/...` 的下发报文格式（需要一次 App 控制抓包）
- 各状态字段的取值枚举（如 `WorkState`、`WorkMode`、`PowerSwitchAll`）
- 是否可用 `networkAuthSetting` 直接走官方局域网控制，省掉本地最小云

## 社区调研（2026-11）

结论：**目前没有任何公开项目解析过方太洗碗机的报文**，这份文档里的字段是从实机抓包得到的。

| 来源 | 结论 |
|---|---|
| [kaattz/ha-fotile](https://github.com/kaattz/ha-fotile) | 只做油烟机；TSL 硬编码为烟机型号，无 `1032A`、无 `ClRemTime`，不能直接复用 |
| 瀚思彼岸「方太幸福家接入ha」帖（2024–2025） | 多人求方案，无人公开成功；有人提到 `iot.fotile.com` |
| `grep.app` / `searchcode` / GitHub 代码搜索 | 没有 `ClRemTime`、`1032A` 的公开代码命中 |
| [iot.fotile.com](https://iot.fotile.com/) | 方太开放云平台的管理控制台（Vue SPA），后端是 `api.fotile.com/iot-open-admin`，有 `/product/bind/tsl`、`/tsl/import`、`/queryLogs/mqtt` 等接口，但需要服务商/管理员账号，个人不可用 |

因此本项目自己维护 `profiles/<productId>.json`，字段语义按以下方式确定：

1. 抓包得到完整状态 JSON（字段名即语义，值为枚举/数值）
2. 观察状态迁移（如一轮洗涤的 `WorkState`/`RunState` 变化）确定枚举含义
3. 控制报文格式通过抓一次 App 下发的 `control/...` 得到

## 控制报文（2026-11-15 实测）

主题：`control/1032A/1149539722`（云端 → 设备，QoS1，实测会重发 2–3 次，处理时按幂等对待）

| 动作 | payload |
|---|---|
| 关机 | `{"PowerSwitchAll":0,"GSCPowerSwitch":0}` |
| 开机 | `{"PowerSwitchAll":1,"GSCPowerSwitch":1}` |

设备回执（`sync/1032A/1149539722`）：

```json
// 关机后
{"PowerSwitchAll":0,"WorkState":0,"RunState":0,"DoorLockSta":0,"ClRemTime":286,
 "SetWorkTime":0,"GSCPowerSwitch":0,"GSCWorkSta":0,"GSCSetWorkT":0}
// 开机后
{"PowerSwitchAll":1,"WorkState":1,"SetWorkTime":286,"GSCPowerSwitch":1,
 "GSCWorkSta":1,"GSCSetWorkT":18}
```

### 已确认的枚举

| 字段 | 值 | 含义 |
|---|---|---|
| `PowerSwitchAll` | 0 / 1 | 关 / 开 |
| `WorkState` | 0 | 关机 |
| | 1 | 待机（开机未选程序） |
| | 2 | 运行中 |
| | 4 | 已完成 |
| `ClRemTime` | 数值（分钟） | 剩余时间；关机时回到默认程序时长 286 |
| `GSCSetWorkT` | 18 | 蒸汽清洁默认 18 分钟 |

`WorkState=3` 尚未观察到，推测是暂停/预约等待，后续补充。
