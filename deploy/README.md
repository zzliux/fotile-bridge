# 部署脚本

完整步骤见 [../docs/deployment.md](../docs/deployment.md)。

- `side-router-dnat.sh`：在旁路由上把洗碗机的 HTTP(80) 透明转发到 HA 的最小云。
  - ImmortalWrt/OpenWrt 24.x 使用 nftables（自动检测）
  - 老版本回退到 iptables
  - 只匹配指定源 IP，不动 DNS，不影响手机 App

```bash
scp deploy/side-router-dnat.sh root@192.168.31.2:/tmp/
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh apply'
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh status'
ssh root@192.168.31.2 'sh /tmp/side-router-dnat.sh rollback'
```

可用环境变量覆盖默认值：

```bash
DEV=192.168.31.241 HA=192.168.31.150 PORT=8088 sh side-router-dnat.sh apply
```
