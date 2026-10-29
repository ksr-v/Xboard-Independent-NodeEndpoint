# NodeEndpoint 1.1.2 / Node v9 验证记录

目标配套为 NodeEndpoint `1.1.2`、独立 Node `v1.13-orphan.9`、Node tools `1.0.11` 与 Xboard `v2026.10.30.2`。Root installer `v1.0.10`、不可变 Root tools `1.0.6` 和网页助手 `1.0.0` 保持不变。

Node 本地正式源码为 `39787deee9feccec4cdc368856969befd372facd`，四个程序的实际编译来源为 `5d3911cbe318688cadf1ccd4848ab85cec95c34e`。六项本地资产的大小与 SHA-256 已核验，active v9 锁已填入，audited 清单与资产中的 SHA256SUMS 逐字一致；公开六资产的无凭据下载大小、SHA-256 与 GitHub digest 均核验一致，tag 原始安装器与 Release 文件逐字一致。

已确认的 ACME 问题是混合大小写 DNS 名称导致 CA 返回的 Order 与 CSR literal 不一致。v9 将 ACME 入口、配置指纹和缓存读取使用的名称统一，保留既有 TLS 验证，不删除现有证书缓存，不改变 self / file / content 模式。IP、通配符和尾点的既有验证语义保留，不宣称增加全部 IDN 或尾点证书兼容能力。

现场已确认 nftables `0.9.0`、内核 `4.19.0-21`：JSON 查询退出码为 0、返回 `nftables: null`，独立纯文本 `list tables` 也成功且为空。v9 只在该独立空表检查成立时兼容 null，其他异常、未知结构和非空结果仍拒绝。转发计划改用 `ip` / `ip6` 家族 NAT、数值优先级与未挂钩的完整所有权标记规则，避免依赖旧环境不支持的 inet NAT / table comment；仅对校验通过的旧 v8 inet 资源作受控迁移，不清空外部规则。

最终本地 v9 锁下已重跑：Windows PHP 8.2 / Laravel 12.69.3 原生组件 `36/36`、synthetic 安装命令生成 `4/4`，独立 Node 迁移工具 Python `36/36`。最终 ZIP 真实原生上传、安装、启用及 HTTP 验收 `6/6`，插件包 `141` 成员、独立 tools 包 `101` 成员，固定 v9 关联校验通过。两个 ZIP 顺序构建两次逐字一致；五个历史 ZIP 保持原摘要。最终包摘要与成员验收见发行附件 JSON，避免文档内摘要形成构建循环。这些本地组件检查不执行 Node 安装器、ACME CA 或真实防火墙。

[v8 验证记录](RELEASE_VALIDATION_HY2_INPUT_20261030.md)及其公开下载结果保留原归属，不作为 v9 重跑结果。库级 CSR / Order 回归、缓存保全、nftables 结构与拒绝案例、Node 运行回归、工具与插件包验收和公开资产核验分别记录；无网络测试不代表真实 CA 签发成功。

真实内核 4.19 尚未启动验收。隔离测试机未安装 QEMU，且没有 `/dev/kvm`，本轮没有安装主机包或启动嵌套虚拟机。旧 nft 0.9.0 CLI 在现代内核上的检查及官方能力文档分别记录，均不能替代实际 4.19 内核运行结果。

发行协调器已完成 Go `689` 项 / `16` 包、race `254` 项 / `5` 包，Node 源码 Python `7` 套共 `94` 项通过、`1` 项可选外部镜像检查跳过。现代 nft `1.0.9` 的真实隔离检查包含 INPUT `11` 项（四个兼容过滤后端）、NAT 生命周期 `1` 项和真实 v8 迁移 `4` 项；旧 nft `0.9.0` 在现代内核隔离环境的 INPUT `11` 项（两个 legacy 后端）及 NAT 生命周期 `1` 项通过。这些合计 `28` 次 profile 验收执行，与 Go 单元及 race 计数分开。固定 Go `1.26.8` 下，Linux AMD64 Node / xbctl 与 Windows 构建逐字一致；ARM64 仅完成交叉构建。公开六资产核验全部通过。固定结构状态见 [v9 验证记录](verification/node-v9-release-20261030.json)。
