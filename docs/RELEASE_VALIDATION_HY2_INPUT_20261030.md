# NodeEndpoint 1.1.1 / Node v8 验证记录

本次配套版本为 NodeEndpoint `1.1.1`、独立 Node `v1.13-orphan.8`、独立 Node tools `1.0.10` 与 Xboard `v2026.10.30.1`。Root 安装入口 `root-installer-v1.0.10`、不可变 Root tools `1.0.6`、网页助手 `1.0.0` 保持不变。

本版新增节点实例 owned UDP INPUT 放行，以及启动、配置变更和 `30` 秒周期恢复；单端口与跳跃 Hysteria2 均适用。仅处理实际 UDP 监听口，不保存全局规则，不改提供商规则或默认策略。具体启用、关闭和防火墙兼容边界见[操作说明](HY2_INPUT_FIREWALL.zh-CN.md)。

本地 Windows PHP 8.2 / Laravel 12.69.3 的原生插件组件 `36/36` 与 synthetic 安装命令生成 `4/4` 已通过，检查当前 1.1.1 源码、v8 固定安装源、身份 / CA 保全及命令转义。这些源码检查不执行 Node 安装器、真实主机防火墙或代理内核。另完成插件 ZIP 实际原生上传、安装和启用 `6/6`，逐字核对 `139` 个成员，并保全完整 Admin 资源、其他已启用插件及 Root canary。独立 tools `1.0.10` 包含 `99` 个成员；两包的 Node 锁与 audited 清单逐字相同。

Node 正式源码为 `1790f29a2fe5b7798383724e537cc1356b52d66d`，四个程序的实际编译来源为 `313a2c78a340336ba66038d4d9b0109a78d47cc5`。安装器在后续发行提交中补齐已生成程序摘要，因此两项身份分别记录。`release-lock.json` 固定六项资产摘要，`audited-SHA256SUMS` 与发行清单逐字一致；六项本地资产的大小与 SHA-256 已核对。

发行协调器已完成隔离 Linux Node 源码检查：Go `599` 项 / `15` 个包，race `129` 项 / `2` 个包，真实 network / mount namespace 防火墙流量 `9` 项；Node 源码 Python `94` 项通过、`1` 项可选检查跳过。这些计数各自记录，不合算为一个虚构总数。真实防火墙检查使用独立客户端网络命名空间、veth 和私有 `/run`，覆盖 nft / legacy、双栈、单监听口、范围 NAT、重载修复、兄弟实例清理和外部规则 / 默认策略保留；不是用户生产防火墙的验收。

使用 Go `1.26.8`、固定构建时间和相同源码，在 Linux 与 Windows 主机生成的 **Linux AMD64** Node / xbctl 程序逐字一致。ARM64 只完成交叉构建，未宣称原生 ARM64 运行或流量验收。插件打包侧另完成 Node 迁移工具 Python `36/36`，使用真实 loopback TLS 与模拟 systemd，不与 Node 源码 Python `94` 项混算。

固定结构摘要见 [Node v8 验证记录](verification/node-v8-release-20261030.json)。插件 ZIP 和独立 tools 使用同一发行锁；最终打包、原生上传及逐成员核验由同次发布记录关联。公开 Release 的六项资产已由发行协调器通过无凭据 browser_download_url 完整下载，大小 / SHA-256 与本地锁及 GitHub API digest 一致；tag 的原始安装器与 Release 文件逐字相同。打包侧另独立核对了公开安装器与 SHA256SUMS，清单与包内 audited 文件逐字一致。历史 v7、NAT、Root 助手或设备限制测试仍保留原范围。

运行时与规则恢复的具体断言以本轮组件及 namespace 结果为准，配置失败、停止清理、所有权和关闭选择不以菜单的 active 状态代替。额外原生 nftables INPUT 链和 firewalld 属于本次人工处理范围。云安全组、Docker 端口发布、真实商业客户端、原生 ARM64、长期负载和生产迁移仅在有对应证据时记录为通过。
