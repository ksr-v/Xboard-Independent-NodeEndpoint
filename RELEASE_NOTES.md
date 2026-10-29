# NodeEndpoint 1.1.1

本版将域名 / IP 新机器命令固定到独立 Node **v1.13-orphan.8**，插件 ZIP 为 `NodeEndpoint-1.1.1.zip`，独立 Node tools 为 **1.0.10**。标准 Node 安装器明确启用 Hysteria2 实际 UDP 监听口的 owned INPUT 自动放行，包含不启用跳跃的单端口节点，并在启动、配置变化和每 30 秒恢复自己的运行时规则。

支持 iptables-nft / legacy 的 IPv4、IPv6；仅放行实际 UDP 监听口，不放行整个广告范围或 TCP。提供商持久规则、默认策略和其他实例规则保留，停止时只清理自己。额外原生 nftables INPUT 链与 firewalld 本次需人工处理；UFW 兼容布局和 persistent 重载后恢复存在窗口。云安全组仍需允许客户端使用的 UDP 端口范围。配置、关闭选择与支持范围见[操作说明](docs/HY2_INPUT_FIREWALL.zh-CN.md)。

配套面板为 Xboard `v2026.10.30.1`、NodeEndpoint `1.1.1`。已有节点使用[固定升级命令](docs/UPGRADE.zh-CN.md)，保留身份、Token、CA 和配置；不能用新机器安装命令代替升级。Root 安装器 `v1.0.10`、Root tools `1.0.6` 和网页助手 `1.0.0` 保持不变，已有 CA 无需重建。默认 Root 面板归档升级仍处理已有有效原生入口的助手。

最终摘要和本轮测试结果见[验证记录](docs/RELEASE_VALIDATION_HY2_INPUT_20261030.md)，不将旧版通过数当作本次重跑。已发布资产不覆盖。

# 历史 NodeEndpoint 1.1.0

同域 `/node-endpoint` 支持复用面板管理员登录；经一次 SSH 初始化后，可在网页预览、确认并执行固定 Root 任务，查看任务分阶段结果和同步公共 CA。Root 助手使用 Unix socket 和系统 UID 校验，网页不提供任意命令或私钥操作。

全新安装 Xboard v2026.10.29.2 会同时安装并启用五个内置插件，初始化原生 HTTPS 入口和助手。已有面板升级保留设置、CA、APP_KEY 和禁用选择；旧入口可使用固定 `--enable-web-manager` 命令单独安装助手。详见[网页管理说明](docs/WEB_MANAGER.zh-CN.md)。

插件 ZIP 为 `NodeEndpoint-1.1.0.zip`；Root 引导为 `root-installer-v1.0.10`、`tools 1.0.6`、`web-manager 1.0.0`。已发布旧资产不覆盖。Node 的安装版本继续固定 v1.13-orphan.7。

## 历史 1.0.10

本版使机器管理中的域名和 IP 新装命令都固定使用独立 Node **v1.13-orphan.7** 的正式 Release 安装器。原生 ZIP 与独立迁移工具 **tools 1.0.9** 同步携带新发行锁和逐资产 SHA-256。

配套后端 **v2026.10.28.1** 与 Node v7 修复在线 IP 统计、跨节点并发抢占设备名额、旧连接迁移至限额时的名额保留，以及断网后离线清理。统计按来源 IP 去重，同一公网 IP 后的多台设备共享一项。配套版本与实测范围见[联动说明](docs/DEVICE_LIMIT_RELEASE_20261028.md)。

先备份、更新后端，再按[升级教程](docs/UPGRADE.zh-CN.md)逐台对已有 Node 执行 `upgrade`。仅上传插件会改变以后的新装命令；它不替已运行的后端或 Node 更新二进制。Node 升级会重启服务并中断现有连接。

全插件后端已内置本插件；其他兼容后端可上传 `NodeEndpoint-1.0.10.zip` 原生升级，保留设置、机器 Token、CA 与数据库。Root 面板入口继续使用 `root-installer-v1.0.9 / tools 1.0.6`。历史 tag 和 Release 资产保留。

本次设备业务验收使用隔离 VM 的 VLESS/TCP 与 REST；真实 WS 网络、浏览器操作、原生 ARM64 业务、公网 NAT/IPv6 和长期压力未在本轮重跑。旧版 1.0.9 的组件与 Root 命令历史结果不冒充本版重跑结果。
