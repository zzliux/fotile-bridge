# Fotile Bridge

把方太（Fotile）智能家电接进 Home Assistant 的桥接服务。

设备仍然正常连方太云（**App 在线、可控**），HA 通过一台旁路由把设备的 MQTT 流量引到本服务：
本服务再转发到真实云端，同时旁听状态、把 HA 的控制经云端下发。

> 适合“既想用官方 App，又想接 HA”的场景。设备固件把远程控制绑定在云端会话上，
> 所以不能简单把设备劫持到本地 broker（会丢授权、App 离线），中转是折中且稳定的做法。

## 能力

- **App 无感**：设备照常连 `api.fotile.com` 与云端 MQTT，App 显示在线、可控制
- **HA 可控**：电源、碗碟程序、开始/继续、暂停、取消、排水
- **HA 可看**：剩余时间、工作状态、运行状态、门锁、故障码、提醒、保洁存储
- **不抓凭据**：设备 CONNECT 的账号密码每次连接都会轮换，中转直接透传，不保存
- **零代码适配新型号**：没有 profile 的型号也会按 `sync` 字段自动生成传感器

## 架构与流量走向

```
                        ┌────────────── 主路由 ──────────────┐
  方太设备 ──Wi-Fi────▶ │ PBR: 按设备 MAC 打标，流量走旁路由  │
                        └────────────────┬────────────────────┘
                                         ▼
                        ┌────────────── 旁路由 ──────────────┐
                        │ nftables DNAT: 设备:1883 → HA:1886  │
                        └────────────────┬────────────────────┘
                                         ▼
                        ┌────────────── HA ───────────────────┐
                        │ Fotile Bridge (add-on)              │
                        │   :1886 中转 ──▶ 真实云 broker       │
                        │     ├─ 旁听 sync → 本地 :1885 → HA  │
                        │     └─ HA control → 云端 → 设备      │
                        └─────────────────────────────────────┘
```

三条路径：

| 路径 | 走向 |
|---|---|
| 设备上报 | 设备 → 旁路由 → HA:1886 → 云 broker（中转同时把 `sync` 复制到本地 broker，供 HA 实体使用） |
| App 控制 | App → 方太云 → 云 broker → HA:1886 → 设备（中转原样转发，App 照常工作） |
| HA 控制 | HA MQTT → 本地 broker → 中转 → 云 broker → 设备（复用设备自己的云端会话下发） |

## 前置条件

- HAOS（或 Supervised），已安装并启用 **Mosquitto broker** 加载项与 **MQTT** 集成
- 一台可以改路由/DNAT 的路由器，或者一台旁路由（OpenWrt / ImmortalWrt 均可）
- 设备已经用官方 App 配好网、绑定账号

## 配置项从哪来

add-on 的“配置”页所有字段如下，逐项说明取值来源：

| 配置项 | 取值来源 | 示例 |
|---|---|---|
| `advertise_host` | HA 的局域网 IP。设置 → 系统 → 网络，或路由器 DHCP 列表里 HA 的地址 | `192.168.31.150` |
| `mqtt_port` | HA 上**空闲**端口，给设备用的本地 broker。避开 1883（Mosquitto） | `1885` |
| `http_port` | 历史“本地最小云”端口，中转模式下用不到，保持默认即可 | `8088` |
| `ha_mqtt_host` | HA 的局域网 IP（Mosquitto 加载项监听在 1883） | `192.168.31.150` |
| `ha_mqtt_port` | Mosquitto 端口 | `1883` |
| `ha_mqtt_user` | 在 Mosquitto 加载项 → 配置 → `logins` 里新增的账号 | `fotile` |
| `ha_mqtt_password` | 同上，`logins` 里对应的密码 | `fotile123` |
| `relay_enabled` | 中转模式开关，填 `true` | `true` |
| `relay_port` | 中转监听端口，选 HA 上空闲的 | `1886` |
| `relay_upstream` | 方太云 MQTT broker 的 IP。可从旁路由抓设备 CONNECT 得到，或用已知入口 | `121.41.80.81` |
| `upstream_ip` | `api.fotile.com` 的解析结果（`dig +short api.fotile.com`），仅 learn 模式用 | `101.37.40.179` |
| `learn_mode` | 是否允许把未知 HTTP 请求透传真实云并缓存 TSL | `true` |
| `show_experimental` | 是否显示未验证实体（预约时间、原始值输入等） | `false` |

已知可用的云端 broker 入口：`121.41.80.81`、`120.26.51.72`、`47.97.27.44`（云端会轮换，连不上就换一个）。

### Mosquitto 账号怎么建

设置 → 加载项 → **Mosquitto broker** → 配置，在 `logins` 列表里加一条（保留原有）：

```yaml
logins:
  - username: "fotile"
    password: "fotile123"
```

保存并重启 Mosquitto，然后把这个账号密码填进 add-on 的 `ha_mqtt_user` / `ha_mqtt_password`。

## 部署步骤

### 1. 安装 add-on

设置 → 加载项 → 加载项商店 → 右上角 ⋮ → 仓库 → 添加：

```
https://github.com/zzliux/fotile-bridge
```

安装 **Fotile Bridge**，先别启动。

### 2. 填写配置

```yaml
http_port: 8088
mqtt_port: 1885
advertise_host: "192.168.31.150"
upstream_ip: "101.37.40.179"
learn_mode: true
ha_mqtt_host: "192.168.31.150"
ha_mqtt_port: 1883
ha_mqtt_user: "fotile"
ha_mqtt_password: "fotile123"
log_level: info
mirror_enabled: false
relay_enabled: true
relay_port: 1886
relay_upstream: "121.41.80.81"
show_experimental: false
```

保存 → 启动。日志应出现：

```
app.relay: mqtt relay listening on :1886
app.relay: relay watching local control/#
bridge connected to local broker :1885
```

### 3. 主路由：把设备流量指向旁路由

不同路由器做法不同，目标是：**该设备访问非局域网地址时，网关变成旁路由**。

OpenWrt 示例（按源 MAC 打标 + 策略路由）：

```bash
iptables -t mangle -N DEVICEPBR
iptables -t mangle -A DEVICEPBR ! -d 192.168.31.0/24 -i br-lan \
  -m mac --mac-source 44:0F:B4:E1:26:D6 -j MARK --set-xmark 0x100
ip rule add fwmark 0x100 lookup 100
ip route add default via 192.168.31.2 table 100
```

小米路由等带“设备分流/旁路由”界面的，直接在界面里把设备指到旁路由即可。

### 4. 旁路由：DNAT 1883 到中转

```bash
scp deploy/side-router-relay.sh root@192.168.31.2:/tmp/
ssh root@192.168.31.2 'sh /tmp/side-router-relay.sh apply'
```

规则（ImmortalWrt 24.x 用 nftables）：

```
table ip fotile_relay {
  chain prerouting  { ip saddr 192.168.31.241 tcp dport 1883 dnat to 192.168.31.150:1886 }
  chain postrouting { ip saddr 192.168.31.241 ip daddr 192.168.31.150 tcp dport 1886 masquerade }
}
```

> 优先级必须是负数（脚本用 -101），否则会被 mihomo / fw4 的 dstnat 抢先。

### 5. 设备重连 + 远程授权

- 给设备断电重连，或从路由器踢一下 Wi-Fi，让 MQTT 走新的 DNAT 路径
- **在设备面板上按一次“启动”**，完成远程控制授权（状态里会出现 `RemoteAuth=1`）
- App 里确认设备在线，HA 里会自动出现实体

### 6. 验证

```bash
# 旁路由：DNAT 是否命中（每个连接计 1 次）
ssh root@192.168.31.2 'nft list table ip fotile_relay | grep counter'

# add-on 日志关键字
#   relay connection -> 121.41.80.81:1883
#   captured device cloud credentials
#   cloud control channel ready
#   published control via cloud ...

# HA：设置 → 设备与服务 → MQTT → 设备，出现「方太 ... <deviceId>」
```

## 实体

| 类别 | 实体 |
|---|---|
| 控制 | 电源、碗碟程序（13 种）、启动所选程序、开始/继续、暂停、取消、排水（开关） |
| 果蔬篮（PopWash） | 果蔬篮程序（8 种）、启动所选、暂停、继续、取消、果蔬排水（POP） |
| 果蔬篮监控 | 果蔬篮状态、剩余时间、当前程序、故障码 |
| 监控 | 剩余时间、工作状态（中文）、运行状态、门锁、故障码、提醒、保洁存储(天) |
| 默认隐藏 | 预约时间、工作模式/工作状态原始值（`show_experimental: true` 才显示） |

### 控制报文（洗碗机 1032A 实测）

| 动作 | 报文 |
|---|---|
| 开机 / 关机 | `{"PowerSwitchAll":1/0,"GSCPowerSwitch":1/0}` |
| 开始/继续 | `{"WorkState":1}` |
| 暂停 | `{"WorkState":2}` |
| 取消 | `{"WorkState":0}` |
| 启动程序 | `{"WorkMode":N,"WorkState":1,"AddFunL":X,"StoreTotalTime":D,"PowerSwitchAll":1}` |
| 排水 | `{"AutoDrain":1}`；设备排水期间上报 `AutoDrain=1`，结束后自动变回 `0`（实测约 37 秒） |

程序映射：日常洗 0、超快洗 1、超净洗 2、晶亮洗 3、自清洁 4、节能洗 5、智能洗 6、
预冲洗 128、母婴洗 129、静音洗 130、蒸汽消杀 132、独立烘干 133、餐前暖碟 136。
谷电洗（135）走预约流程，暂未实现。

### 排水开关

排水做成了和电源一样的开关（`switch.*_autodrain`）：

- **开** → 发 `{"AutoDrain":1}`
- **关** → 发 `{"AutoDrain":0}`（设备不保证响应，可能忽略）
- **状态** → 直接读设备上报的 `AutoDrain`，所以排水结束后开关会**自动关闭**，不需要额外定时器

实测（2026-11-17）：

```
11:03:01  AutoDrain=1   ← 开始排水
11:03:38  AutoDrain=0   ← 排完，开关自动关
```

排水期间 `WorkState` 仍是 0（已关机），所以只能靠 `AutoDrain` 判断，不能用工作状态。

### 果蔬篮 / PopWash

插入果蔬篮后，洗碗机可以在同一个腔体里做果蔬/生鲜清洗和虾蟹暂养。设备用 **GSC\*** 这组字段承载，
和碗碟程序完全独立（`WorkState` 不参与）。

**程序枚举（实测 2026-11-17，App 依次操作 + 抓 control 报文）**

| 程序 | `GSCWorkMode` | 默认时长 |
| --- | --- | --- |
| 果蔬清洗-轻 | 2 | 11 分钟 |
| 果蔬清洗-中 | 0 | 18 分钟 |
| 果蔬清洗-强 | 1 | 28 分钟 |
| 果蔬清洗-深层 | 12 | 80 分钟（原始 276 = 1h20m） |
| 生鲜清洗-轻 | 5 | 11 分钟 |
| 生鲜清洗-中 | 3 | 18 分钟 |
| 生鲜清洗-强 | 4 | 28 分钟 |
| 虾蟹暂养 | 10 | 无（`GSCSetWorkT=0`） |

**报文**

| 动作 | 报文 |
| --- | --- |
| 启动所选 | `{"GSCWorkMode":N,"GSCWorkSta":1,"PowerSwitchAll":1}` |
| 暂停 | `{"GSCWorkSta":2}` |
| 继续 | `{"GSCWorkSta":1}` |
| 取消 | `{"GSCWorkSta":0}` |
| POP 排水 | `{"GSCAutoDrain":1}`，结束设备回 `0` |

**状态字段**

| 字段 | 含义 |
| --- | --- |
| `GSCWorkSta` | 0 已关机 / 1 待机 / 2 运行中 / 3 暂停 |
| `GSCRunSta` | 0 停止 / 1 运行 / 2 暂停（默认隐藏） |
| `GSCClRemT` | 剩余时间（`hhmm` 编码） |
| `GSCAutoDrain` | POP 排水状态，实测持续约 137 秒（普通排水约 36 秒） |

> 注意：**果蔬篮插入没有对应的状态位**。设备不上报“篮在位”，只在 App/设备端禁止启动碗碟程序
> （实测 App 直接禁用按钮），所以 HA 侧无法据此禁用碗碟实体，需要使用者自己注意。

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| App 显示离线 | 设备没走中转 / 中转连不上上游 | 看 `relay connection` 与 `relay upstream failed` 日志，换 `relay_upstream` |
| App 在线但控制无效 | 设备未完成远程授权 | 在面板按一次“启动”，确认 `RemoteAuth=1` |
| HA 有实体但状态未知 | 设备还没上报，或 `sync` 没到本地 | 看日志 `injected`/本地 broker 是否收到 `sync`，触发一次设备状态变化 |
| `No file descriptors available` | 旧版本中转自环 | 升级到 0.4.2+ |
| 中转日志 `Connection Refused` | 本地桥接账号错 | 检查 `ha_mqtt_user/password`（桥接认证用 `remote_username`） |
| DNAT 不生效 | mihomo / fw4 抢先 | nft 规则优先级用 -101 |
| 实体突然全部 `unavailable` | 0.11.0 的 availability 没被桥接到 HA；且重启后要等设备重连 | 升级到 **0.11.1+**（补上 `fotile/availability/#` 桥接 + 重启按上次活跃时间恢复在线） |

## 在线状态（availability）

HA 里每个实体都带 `availability_topic: fotile/availability/<设备ID>`，值 `online` / `offline`。

- **online 的来源**：洗碗机通过中转建立 MQTT 连接（解析到 CONNECT 里的 device_id），或设备持续上报 `sync/`
- **offline 的来源**：连接关闭，或 180 秒静默（设备 Wi-Fi 掉线时 TCP 可能没有 FIN）
- **add-on 重启**：会读 `/data/availability.json` 里记录的最近活跃时间，
  在 `availability_fresh`（默认 900 秒）之内就直接恢复 `online`，超过则发 `offline`，
  避免每次重升级都出现长时间“实体不可用”

> 注意：这是**被动**可见性。设备侧没有“你在线吗”这种主动查询协议；
> 想主动探测只能走 App 侧 HTTP API（需要账号 token，本项目未实现）。

### 排查设备私有字段（debug 日志）

0.11.4 起，`log_level: debug` 时会把以下内容打进日志：

- `device publish <topic> <payload>`：设备发往云端的**所有** MQTT 报文（不只是 `sync/`）
- `proxy body <method> <path> <body>`：设备发往云端的 HTTP 请求体
- `proxy resp <path> <body>`：云端响应（小于 2KB 时）

用途：设备有些能力（例如果蔬篮是否插入）不在 `sync/` 状态里，需要从这些私有报文/接口里找。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q

# 本地跑（不装 add-on）
FOTILE_HTTP_PORT=8088 FOTILE_DATA_DIR=./data .venv/bin/python -m app.main
```

目录：

```
fotile_bridge/            # HA add-on
  app/relay.py            # MQTT 中转（旁听 + 云端控制通道）
  app/mqtt_bridge.py      # 本地 broker 接入 + HA Discovery + 命令映射
  app/discovery.py        # MQTT Discovery
  app/profiles/           # 各型号字段、程序、控制映射
  app/tsl_seed/           # 物模型样本（learn 模式用）
deploy/                   # 旁路由脚本
docs/                     # 协议与踩坑记录
tools/                    # pcap 分析、桥接自检
```

## 已知限制

- 控制依赖云端：断外网时 App 与 HA 控制都不可用（这是“App 可用”的代价）
- 只在洗碗机 `JBSD2T-02-M6Max`（productId `1032A`）上实测
- 云端 broker 会轮换入口，必要时手动换 `relay_upstream`
- 谷电洗（预约启动）未实现

## 新增型号

1. 先用通用 Discovery 跑起来：没有 profile 的型号会按字段名自动生成传感器
2. 复制 `app/profiles/1032A.json`，改 `productId`/`model`，补字段中文名、单位、程序映射
3. 控制报文差异写进 `commands`/`programs`，不需要改代码

## License

MIT
