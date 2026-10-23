# NodeEndpoint 1.0.7 / root-installer-v1.0.9

宝塔 / aaPanel + Docker 部署现可通过 SSH 以宿主机 Root 一次确认，自动配置 IP 私有证书入口、随机 CA 口令托管、通讯设置、公共 CA 与本机防火墙。全新四插件后端提供 `bash docker-install.sh`，默认宝塔 MySQL / MariaDB 和内置 Redis，可选 SQLite。

已有标准 Docker 面板使用：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --docker
```

详细步骤见 [Docker 简化安装](docs/DOCKER_INSTALL.md)。容器重建后需重新审阅计划，用原 CA 私钥匹配托管口令后换绑；不生成替代 CA 或重置 APP_KEY。旧的手动 Docker 入口、宿主网络和非标准挂载布局需单独迁移。

插件 ZIP 可原生升级并保留设置；无需卸载其它插件。四插件后端 v2026.10.23.1 包含 NodeEndpoint 1.0.7、SmartExpiry 1.2.0、二维码审计副本 1.2.0 和 FlowScope 2.4.0。Node 二进制保持 v1.13-orphan.4。
