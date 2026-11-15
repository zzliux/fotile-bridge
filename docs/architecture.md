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
