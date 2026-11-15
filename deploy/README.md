# 部署：把洗碗机的 HTTP 流量导到本地最小云

只改旁路由，不动主路由 DNS，因此**手机上的方太 App 仍然走真实云**。

前提：

- 洗碗机网关已经指向旁路由（`192.168.31.2`）
- HA 上 Fotile Local Bridge 已启动，`http_port=8088`、`mqtt_port=1885`
- `advertise_host` 设为 HA 的 IP（`192.168.31.150`）

执行：

```bash
scp deploy/side-router-dnat.sh root@192.168.31.2:/tmp/
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh apply'
```

然后给洗碗机断电重连，它会：

1. `POST /v5/time_sync/` → 本地最小云
2. `POST /v2/new_device_login` → 本地返回 token
3. `POST /iot-mqttManager/routeService` → 返回 `192.168.31.150:1885`
4. 连本地 MQTT，开始发 `sync/1032A/<deviceId>`

HA 里会自动出现实体（MQTT Discovery）。

回滚：

```bash
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh rollback'
```
