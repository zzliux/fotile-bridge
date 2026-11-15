# 部署实录：把方太洗碗机接管到本地 HA

这份文档记录**实际跑通的那一套**配置（2026-11-15），包括主路由、旁路由、HA 三部分，以及每一步怎么验证、怎么回滚。

## 0. 先说结论：没有“改 DNS”，也没有“欺骗 IP”

设备从头到尾都以为自己连的是 `api.fotile.com:80`。我们做的是**在旁路由上按源 IP 做透明 TCP 重定向（DNAT）**：

- 设备发出的包，目的地址仍是 `101.37.40.179:80`
- 包经过旁路由时，被 nftables 改写成 `192.168.31.150:8088`（HA 上的最小云）
- 回程做 masquerade，让设备以为是云端正常响应
- MQTT 更简单：`routeService` 直接返回 `192.168.31.150:1885`，设备自己去连

没有 DNS 劫持的好处：**手机上的方太 App 仍然访问真实云**，只有洗碗机这一台被接管。

## 1. 拓扑与流量路径

```
                     ┌──────────── 主路由 192.168.31.1 ────────────┐
 洗碗机 .241 ──Wi-Fi──▶│  DEVICEPBR: 命中 MAC → mark 0x100           │
                     │  ip rule: fwmark 0x100 → table 100          │
                     │  table 100: default via 192.168.31.2        │
                     └───────────────────┬─────────────────────────┘
                                         │
                     ┌───────────────────▼──── 旁路由 192.168.31.2 ─┐
                     │ nft table ip fotile_bridge                    │
                     │   prerouting  saddr .241 tcp/80 → HA:8088     │
                     │   postrouting saddr .241 daddr HA:8088 masq   │
                     └───────────────────┬───────────────────────────┘
                                         │
                     ┌───────────────────▼──── HA 192.168.31.150 ───┐
                     │ add-on: Fotile Local Bridge                   │
                     │   :8088 最小云（login/routeService/tsl）       │
                     │   :1885 mosquitto（设备接入）                  │
                     │   桥接 → :1883 HA Mosquitto（实体进 HA）        │
                     └───────────────────────────────────────────────┘
```

设备开机链路：

1. `POST /v5/time_sync/` → HA:8088（被 DNAT）
2. `POST /v2/new_device_login` → HA:8088，本地返回 token
3. `POST /iot-mqttManager/routeService` → HA:8088，返回 `{"ip":"192.168.31.150","port":1885}`
4. 设备直连 `192.168.31.150:1885`（同网段，走二层，不经过 PBR）
5. 订阅 `control/1032A/1149539722`，向 `sync/1032A/1149539722` 上报状态
6. add-on 合并状态后 retain 到 `fotile/state/1032A/1149539722`，经桥接进 HA，MQTT Discovery 自动建实体

## 2. 主路由：只让洗碗机走旁路由

主路由（OpenWrt 18.06，XiaoQiang）上是一个“设备分流”（PBR）功能，脚本在 `/etc/device-panel/pbr.sh`：

```bash
# 查看
iptables -t mangle -S DEVICEPBR | grep -i 44:0F:B4
ip rule
ip route show table 100
```

实际规则：

```
iptables -t mangle -A DEVICEPBR ! -d 192.168.31.0/24 -i br-lan \
  -m mac --mac-source 44:0F:B4:E1:26:D6 -j MARK --set-xmark 0x100/0xffffffff

ip rule add fwmark 0x100 lookup 100
ip route add default via 192.168.31.2 table 100
```

含义：洗碗机访问**非局域网**的流量打 mark `0x100`，查表 100，默认网关变成旁路由 `.2`；访问 `192.168.31.0/24` 的流量不打标，仍然直接二层通信（所以连 HA:1885 不受影响）。

> 不用这台路由的 PBR 界面也可以手工加：上面三条命令即可；持久化写到 `/etc/firewall.user` 或开机脚本。

## 3. 旁路由：nftables 透明重定向

旁路由是 ImmortalWrt 24.10，**没有 iptables**，用 nft。脚本在仓库 `deploy/side-router-dnat.sh`。

```bash
scp deploy/side-router-dnat.sh root@192.168.31.2:/tmp/
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh apply'
```

生成的表：

```
table ip fotile_bridge {
  chain prerouting {
    type nat hook prerouting priority -101; policy accept;
    ip saddr 192.168.31.241 tcp dport 80 counter dnat to 192.168.31.150:8088
  }
  chain postrouting {
    type nat hook postrouting priority 101; policy accept;
    ip saddr 192.168.31.241 ip daddr 192.168.31.150 tcp dport 8088 counter masquerade
  }
}
```

两个关键点：

1. **priority -101**：必须早于 fw4 的 dstnat（-100）和 mihomo 的 dstnat+1（-99）。这台旁路由跑着 mihomo，它会把 TCP 重定向到 TUN（`:34595`），不抢在前面规则就轮不到我们。
2. **masquerade**：DNAT 后目的变成同网段的 HA，如果不 SNAT，HA 会直接回包给设备，源地址对不上被 RST。masquerade 让回程经过旁路由，由它还原成 `101.37.40.179:80`。

验证：

```bash
nft list table ip fotile_bridge | grep counter
# 设备发起请求后，两个 counter 应该都在涨
```

## 4. HA：Mosquitto 账号 + add-on

### 4.1 Mosquitto 增加桥接账号

设置 → 加载项 → Mosquitto broker → 配置，在 `logins` 里加（保留原有）：

```yaml
  - username: "fotile"
    password: "fotile123"
```

重启 Mosquitto。验证：

```bash
# 能收到 CONNACK 0 就说明账号可用
python3 tools/bridge_test.py
```

### 4.2 安装 add-on

1. 设置 → 加载项 → 加载项商店 → 仓库 → 添加 `https://github.com/zzliux/fotile-local-bridge`
2. 安装 **Fotile Local Bridge**
3. 配置：

```yaml
http_port: 8088
mqtt_port: 1885          # 避开 1883（Mosquitto）和 1884（其它插件）
advertise_host: "192.168.31.150"
upstream_ip: "101.37.40.179"
learn_mode: true
ha_mqtt_host: "192.168.31.150"
ha_mqtt_port: 1883
ha_mqtt_user: "fotile"
ha_mqtt_password: "fotile123"
log_level: info
```

4. 启动，日志应出现：

```
minimal cloud listening on :8088 (advertise 192.168.31.150)
bridge connected to local broker :1885
subscribed sync/#, fotile/state/#, fotile/+/+/set
```

> 注意：Mosquitto 桥接认证必须用 `remote_username` / `remote_password`，写 `username` / `password` 会一直 `not authorised`——这是个踩过的坑。

## 5. 设备重启与验证

给洗碗机断电 10 秒再上电，然后按顺序验证：

```bash
# 1) DNAT 是否命中
ssh root@192.168.31.2 'nft list table ip fotile_bridge | grep counter'
#    packets 应该 > 0

# 2) 本地 broker 是否收到设备状态
python3 - <<'PY'
# 订阅 192.168.31.150:1885 的 sync/#（匿名），能看到 {"PowerSwitchAll":...}
PY

# 3) 桥接是否双向通
python3 tools/bridge_test.py
#    local->HA: OK / HA->local: OK

# 4) HA broker 上的 discovery
#    订阅 homeassistant/#，应能看到 33 个 fotile_1032A_1149539722_* 配置
```

HA 里：设置 → 设备与服务 → MQTT → 设备，出现 **方太 JBSD2T-02-M6Max 1149539722**，33 个实体。

## 6. 踩坑记录

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志 `Connection Refused: not authorised` | 桥接用了 `username/password`，不是 `remote_username/remote_password` | 升级到 0.1.2+ |
| 主路由 tcpdump 只看到 ARP | 高通 NSS 硬件加速，流量不过 CPU | `qca-nss-ecm stop`（抓完恢复） |
| 旁路由抓不到转发流量 | `flow_offloading` 开着 | `uci set firewall.@defaults[0].flow_offloading=0` |
| DNAT 不生效 | mihomo 的 dstnat+1 抢先重定向到 TUN | 规则放 priority -101 |
| 实体显示“未知” | sync 不 retain，设备只在状态变化时发 | 0.1.3 起合并状态并 retain 到 `fotile/state/...` |
| 手机 App 登录提示“账号存在风险” | 网易易盾检测模拟器环境 | 放弃模拟器，改用真机/本地化方案 |
| `iptables: not found`（旁路由） | ImmortalWrt 24.x 只有 nft | 用 `deploy/side-router-dnat.sh` 的 nft 分支 |

## 7. 回滚

```bash
# 旁路由：删除 DNAT 表
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh rollback'

# 主路由：从 PBR 列表移除洗碗机（device-panel 界面操作，或删掉对应 mangle 规则）
iptables -t mangle -D DEVICEPBR -m mac --mac-source 44:0F:B4:E1:26:D6 \
  ! -d 192.168.31.0/24 -i br-lan -j MARK --set-xmark 0x100/0xffffffff
```

回滚后设备会在下次重连时回到真实云端，HA 实体变为不可用（可保留 discovery 配置）。

## 8. 调试期临时改动（记得恢复）

- 旁路由 `flow_offloading` / `flow_offloading_hw` 已关（抓包需要），调试完恢复：

```bash
uci set firewall.@defaults[0].flow_offloading='1'
uci set firewall.@defaults[0].flow_offloading_hw='1'
uci commit firewall && /etc/init.d/firewall restart
```

- 主路由上临时部署过 tcpdump（`/tmp/tcpdump2`），已删除
- 旁路由上的 `/tmp/tcpdump`、`/tmp/libpcap.so.1*`、`/tmp/fotile.pcap` 是临时文件，重启即消失
