# NodeEndpoint 1.0.0 专用 Linux VM 验证报告

日期：2026-10-10（Asia/Shanghai）。本文记录首轮专用 Ubuntu VM **139 项执行检查全部通过**的历史基线，包括当时发行 ZIP 的 6 项原生导入复测；Windows 对应历史套件为 110 项通过。后续两 VM 实际 Xboard / NodeWorker / Go 联测独立记录于 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。两平台及多轮有重复执行的用例，不能累计为新增唯一用例。当前发行摘要统一见 `dist/SHA256SUMS`。

## 环境与授权范围

用户指定专用本地 VM `192.168.128.129`，以 `codex` 用户免密 SSH / sudo 授权安装测试依赖、创建隔离环境；认证密钥不进入报告。实际环境为 Ubuntu 24.04.3、x86_64、Python 3.12.3、OpenSSL 3.0.13、Nginx 1.24、PHP 8.3.6；PHP 组件使用 Laravel 12.69.3、Sanctum 4.3.3 和隔离 SQLite。测试使用合成用户、身份和凭据，没有访问生产面板。

测试资源放在专用实验目录。已有 Docker、FlowScope 采集器和系统 PHP-FPM 保持运行，不重启这些服务；独立 Nginx / PHP-FPM fixture 使用自己的配置、socket、PID 和进程记录，清理仅针对可确认归属的测试资源。测试允许安装依赖，不表示交付工具会自动安装或升级生产依赖。

参考源码按 SOURCE_AUDIT 中固定 commit 只读打包，排除 `.git` 和 Node 仓库；独立面板只携带测试需要的保留 Admin 资产。测试 vendor 与当前 Composer lock 的版本及来源 reference 完全一致，autoload 搬移验证通过。参考源码、vendor、私有证据、生成密钥和实验数据库不进入发行源码包。

## 已执行套件

| 套件 | 执行结果 | 实际范围 |
| --- | --- | --- |
| `tests/panel/test_panel.py` | 33 PASS | 在 Linux 重跑 OpenSSL / loopback TLS 与解析、归属、模拟事务测试 |
| `tests/node/test_node_endpoint.py` | 36 PASS | 协议、配置、信任、模拟事务与实际 REST 协议 / 内核不兼容拒绝 |
| `tests/integration/run.php` | 26 PASS | 原始 Xboard 组件与真实 Laravel / Sanctum，隔离 SQLite，协议选择约束 |
| `tests/integration/package.php` | 6 PASS | 重建发行 ZIP 经原始 PluginManager.upload 导入，52 文件逐字节核对，安装/启用及资源保持检查 |
| `tests/panel/linux_integration.py` | 6 PASS | 真实隔离 Nginx、TLS / WSS、禁止路径、监听冲突和失败恢复 |
| `tests/node/linux_permissions.py` | 14 PASS | 真实 UID / mode / links、独立进程竞争、flock 释放与复用；不操作服务或网络 |
| `tests/panel/cli_integration.py` | 7 PASS | 真实 Root CLI、Laravel 原版路由表与独立 PHP-FPM，原服务保持检查 |
| `tests/node/linux_integration.py` | 11 PASS | 真实固定 AMD64 Go binary、systemd、双内核、认证 WSS、换证、标准多实例迁移与 CLI 回滚 |

完整构成为 **33 + 36 + 26 + 6 + 6 + 14 + 7 + 11 = 139 项执行检查，全部 PASS**。首轮 126 项基线后，Node / PHP 各增加一项协议组合校验，另加入 Go 11 项。重建发行 ZIP 在 Windows 和 Ubuntu 各通过原生 6 项，52 文件逐字节核对，三插件 Admin 与 Root 证据保持。Windows 最新总计 110 项；两平台结果有重叠，不表示新增唯一用例。

Linux 权限测试实际验证 Root 私有输入、group-writable / 外来 UID 祖先、错误文件所有者和可读权限、hardlink / symlink、锁文件权限及独立进程竞争；持锁进程正常退出或终止后，内核均释放 flock。真实 Root CLI / PHP-FPM 验证原版 route:list、无 Token 422、TLS、安装/幂等/修改/回滚/卸载与原站保持。公开记录见 [权限结果](docs/verification/vm-node-permissions.json)、[Root CLI 结果](docs/verification/vm-panel-cli-result.json) 和 [进程清理](docs/verification/vm-panel-cli-cleanup.json)。

## 原生 AMD64 Go Node：最终 run005

最新生产工具与固定摘要的独立 Node `v1.13-orphan.4` AMD64 二进制完成 **11 项 PASS**，`cleanup_failures=[]`。实际执行 install-new、node / Xray / VLESS、machine / sing-box / SOCKS5、多绑定、TLS REST / WSS 与真实内核代理字节；Node WSS 有实时 pong 和 node.status，machine 以实时 pong 证明连接存活，其源码未设置状态 ping 回调。同 CA 换服务器私钥后不改信任，双 CA 过渡后接受新根签发链。结果与测试摘要见 [公开 Go 结果](docs/verification/vm-node-go-result.json)。

标准布局从开始即采用两实例 Xray：node 多绑定与 machine 多绑定。只迁移选中实例 URL，核对其他实例 URL、Token、binary、metadata 和数据 sentinel，再执行真实 CLI rollback 恢复。测试使用 prepare 的无下载路径，单独核对摘要后 stage 已上传的发行资产；这一流程没有调用 prepare 的网络 downloader。

Python 面板、echo 与 Xray 业务监听使用 loopback。原始 sing-box 内核忽略 listen_ip，仍监听 `::`；仅 fixture 自己的随机 systemd unit 追加 `IPAddressDeny=any` / `IPAddressAllow=localhost`，并实际验证 IPv4 非 loopback 连接被拒绝。这是实验环境隔离措施，不是生产默认策略，也未修改原始 Go / 内核二进制。Docker / FlowScope inventory 和标准布局清理结果保持检查通过。

本历史套件的 Go 测试面板是合成 Python TLS / WebSocket fixture。真实 PHP Laravel / Sanctum / 原版路由验证是独立套件；这 139 项不含完整 Xboard 数据库、原版 NodeWorker 和 Go Node 的联合运行。后续实际联测见 FULL_E2E_TEST_REPORT。Go 自带的无凭据公网 IP 观察请求也不等于公网业务通信验收。

VM 暴露的新装协议选择问题已修复，Windows Node 36 / PHP 26 / UI 9 和 VM Node 36 / PHP 26 回归通过。Xray 拒绝 tuic / anytls；`hysteria2` 是官方身份认证的合法 alias，命令生成规范为 `hysteria`，两内核仍可使用。实际 REST 配置另作验证：Xray 的 hysteria 需 version 2；该版本 sing-box builder 对 wire protocol=`hysteria2` 没有 inbound，prepare 拒绝该 wire 值，合法 Hy2 配置为 protocol=`hysteria` / version 2。prepare 检查每个 machine binding，在认证 WSS 与创建 staging 目录之前拒绝不兼容配置。迁移没有更换内核。

| 执行轮次 | 结果与处理 |
| --- | --- |
| run001 | Xray 不支持 fixture 的 SOCKS 配置，暴露实际协议 / 内核组合缺口；调整 Xray fixture 为 VLESS，并修复新装校验 |
| run002 | 默认 SSRF 规则阻止代理流量访问 loopback echo 目的地址；fixture 通过 custom_route_rules 明确 `127.0.0.1/32` → direct |
| run003 | 标准布局已有备份，按归属检查安全拒绝；保留证据并核对测试资源归属 |
| run004 | 10 项通过，但 sing-box 的网络边界未收紧，不能作为最终隔离验收 |
| run005 | 最新工具、11 项全部通过；追加仅 fixture 自有 unit 的网络隔离和非 loopback 拒绝检查，清理与服务保持通过 |

## 复现与证据

在确认归属的专用 Linux 测试机准备必要依赖、固定参考检出和锁定 vendor；发行源码包不包含这些开发依赖。普通组件测试可从项目根运行：

```bash
python3 tests/panel/test_panel.py
python3 tests/node/test_node_endpoint.py
php tests/integration/run.php
php tests/integration/package.php
```

真实进程测试必须在专用实验环境运行。`tests/panel/linux_integration.py` 拒绝已运行的 Nginx；`tests/panel/cli_integration.py` 需要经过核对的非 Root 应用用户和已安装 PHP-FPM。Linux 权限与 Go 测试需要新建 Root 私有目录，标准迁移 fixture 仅能在全部目标标准路径不存在时创建自己的布局，不得覆盖已有 Node。

```bash
sudo python3 tests/panel/linux_integration.py
sudo python3 tests/panel/cli_integration.py --app-user <已核对的非Root应用用户>
sudo python3 tests/node/linux_permissions.py --base <安全Root目录下的新子目录>
sudo python3 tests/node/linux_integration.py \
  --base <安全Root目录下的新子目录> \
  --binary <已独立核对摘要的AMD64发行资产> \
  --include-standard-migration
```

JSON 检查结果、私有 manifest、服务与进程保持记录保存于实验目录。失败与清理不完整时保留证据供人工恢复；不将包含测试凭据的日志或密钥放入交付文档。最终可公开的结果以本报告和 TEST_REPORT 汇总为准。

[最终环境核对](docs/verification/vm-final-environment.json) 确认 Docker、FlowScope 和系统 PHP-FPM 的 PID / start 未变，fixture Nginx/OpenResty/Node进程、自有随机unit与标准binary/unit/drop-in已清理，原 `/etc/xboard-node/backups` 目录已原样恢复。测试依赖和专用实验 workspace 保留供复现；Root 私有诊断仍受保护。

## 后续完整联测

完整两 VM 实际 Xboard / NodeWorker / Go 测试及最终清理已完成，已测冷启动认证及 Admin HTTP 属性更新通过、上游全量及单用户增量 UUID 限制另列，见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。原版数据库/Redis、两个身份与内核、四绑定业务流量和计量、Admin HTTP 属性更新、设备断连、迁移/回滚、CA 轮换和新装均实际执行；失败历史保留，未计为本历史 139 项新增唯一用例。

## 尚未验证的范围

- Octane / Docker / 其他宝塔与 OpenResty 布局及生产防火墙、云安全组；后续联测已覆盖所列实际宝塔 Nginx/PHP-FPM，保留 Docker/FlowScope 不等于应用部署已测。
- 原生 ARM64 执行、公网 / IPv6 业务网络、真实 DNS 故障下的长期运行、真实订阅客户端与生产流量不中断。
- Independent `403604e` PHP overlay、生产密码登录 / CAPTCHA、未知未来 Admin / TrustProxies 构建。
- 自动移除旧 CA、CRL / OCSP 吊销、FlowScope 采集器自动迁移；这些仍是 V1 支持边界。

本 VM 验证结果用于收缩未测范围。生产上线仍须依据 PRODUCTION_DEPLOYMENT 在目标部署的隔离副本上验收，并按实例分批切换。
