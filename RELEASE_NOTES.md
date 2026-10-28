# NodeEndpoint 1.0.10

本版使机器管理中的域名和 IP 新装命令都固定使用独立 Node **v1.13-orphan.7** 的正式 Release 安装器。原生 ZIP 与独立迁移工具 **tools 1.0.9** 同步携带新发行锁和逐资产 SHA-256。

配套后端 **v2026.10.28.1** 与 Node v7 修复在线 IP 统计、跨节点并发抢占设备名额、旧连接迁移至限额时的名额保留，以及断网后离线清理。统计按来源 IP 去重，同一公网 IP 后的多台设备共享一项。配套版本与实测范围见[联动说明](docs/DEVICE_LIMIT_RELEASE_20261028.md)。

先备份、更新后端，再按[升级教程](docs/UPGRADE.zh-CN.md)逐台对已有 Node 执行 `upgrade`。仅上传插件会改变以后的新装命令；它不替已运行的后端或 Node 更新二进制。Node 升级会重启服务并中断现有连接。

全插件后端已内置本插件；其他兼容后端可上传 `NodeEndpoint-1.0.10.zip` 原生升级，保留设置、机器 Token、CA 与数据库。Root 面板入口继续使用 `root-installer-v1.0.9 / tools 1.0.6`。历史 tag 和 Release 资产保留。

本次设备业务验收使用隔离 VM 的 VLESS/TCP 与 REST；真实 WS 网络、浏览器操作、原生 ARM64 业务、公网 NAT/IPv6 和长期压力未在本轮重跑。旧版 1.0.9 的组件与 Root 命令历史结果不冒充本版重跑结果。
