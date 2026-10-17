# NodeEndpoint 1.0.0 实际测试报告

更新日期：2026-10-10（Asia/Shanghai）。历史基线为 Windows **110 项**、专用 Ubuntu VM **139 项执行检查全部通过**，包括当时发行 ZIP 在两平台各 6 项原生导入复测。后续实际两 VM Xboard / NodeWorker / Go 联测独立记录于 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)；本轮 Root 产品修复的 Windows / Linux 回归各为 36 项。两平台及多轮的重复检查不能累计为新增唯一用例。本报告不能作为生产部署验收。

## 环境与来源

开发环境为 Windows，工作区 `D:\xb-node-endpoint`。使用 PHP 8.2.33、Laravel 12.69.3、Sanctum 4.3.3、SQLite、Python 3.13.13、Git OpenSSL、Node.js 24.21.0、Playwright Core 1.58.2 和 Chrome 154.0.8037.98。PHP 配置和依赖均限于本地开发环境。

后续在用户授权的专用本地 VM 实际运行 Ubuntu 24.04.3 x86_64、Python 3.12.3、OpenSSL 3.0.13、Nginx 1.24 与 PHP 8.3.6。依赖安装和测试资源限于实验环境；已有 Docker、FlowScope 采集器和系统 PHP-FPM 保持运行，独立 fixture 不重启这些服务。

六个指定仓库按 [SOURCE_AUDIT.md](docs/SOURCE_AUDIT.md) 中的 exact commit 只读核对；最终检查全部工作树无改动。集成测试读取原始 Xboard 组件，将插件和 Admin 资产复制到隔离目录，使用合成用户、机器 Token 和 SQLite 数据。未访问生产服务器，未修改或推送参考仓库。

## Windows 历史基线套件

| 套件 | 实际结果 | 测试方式与范围 |
| --- | --- | --- |
| `tests/integration/run.php` | 26 PASS | 原始 Xboard PluginManager、AbstractPlugin、InitializePlugins、Admin、ServerV2、Controller 和真实 Laravel/Sanctum；隔离 SQLite，增加协议/内核选择约束 |
| `tests/panel/test_panel.py` | 33 PASS | 11 项真实 OpenSSL/本地 TLS；3 项输入与日期校验；6 项 Nginx 解析/配置单元测试；6 项状态/路径归属单元测试；7 项模拟 Nginx 事务 |
| `tests/node/test_node_endpoint.py` | 36 PASS | 真实本地 TLS/HTTP/WSS、CLI、配置/身份/信任/下载和模拟 systemd；新增实际 REST 协议/内核不兼容拒绝 |
| `tests/ui/run.cjs` | 9 PASS | Headless Chrome 操作真实本地插件 API；无页面 JavaScript 错误；桌面和手机无水平溢出 |
| `tests/integration/package.php` | 6 PASS | 重建 ZIP 经原始 PluginManager.upload 导入，52 文件逐字节核对，安装/启用、页面/API/handshake 与三插件资源保持 |

Windows 2026-10-09 首轮为 108 项，之后 Node / PHP 各新增一项协议校验，最新 110 项全部通过。UI 结果保存在 `tests/ui/work/result.json`；临时目录、合成凭据、数据库、vendor 和 node_modules 均不进入交付 ZIP。

## Linux VM 历史基线套件

| 套件 | 执行结果 | 测试方式与范围 |
| --- | --- | --- |
| panel / node Python 回归 | 33 / 36 PASS | Linux OpenSSL、协议、配置与模拟事务套件，含新装协议拒绝 |
| PHP 原生集成 | 26 PASS | PHP 8.3.6、真实 Laravel / Sanctum、原始 Xboard 组件与隔离 SQLite |
| 发行 ZIP 导入 | 6 PASS | 重建包的原生 upload/install/enable、52 文件内容和资源保持检查 |
| `tests/panel/linux_integration.py` | 6 PASS | 真实隔离 Nginx、TLS / WSS、路径限制、冲突与故障恢复 |
| `tests/node/linux_permissions.py` | 14 PASS | 真实 UID/mode/links、独立进程竞争、flock 释放与复用 |
| `tests/panel/cli_integration.py` | 7 PASS | 真实 Root CLI、原版路由表、独立 PHP-FPM 与服务保持检查 |
| `tests/node/linux_integration.py` | 11 PASS | 最新工具、固定 AMD64 Go binary、真实 systemd/双内核/WSS/换证、标准多实例迁移与 CLI 回滚 |

VM 首轮 126 项后，Node / PHP 各新增一项回归，并完成 Go 11 项；139 项全部通过。该历史套件的 Go 使用合成 Python 面板，PHP 使用真实 Laravel/Sanctum/原版路由；完整数据库与原版 NodeWorker 的后续联测另见 FULL_E2E_TEST_REPORT。

## 已核验行为

| 要求 | 实际证据与结论 |
| --- | --- |
| 安装、重复启用、升级、禁用、卸载、重新安装 | 原生组件集成 PASS；常驻路由启用前后、middleware 去重、禁用后立即停止作用均覆盖 |
| 三个现有插件共存 | 使用 SmartExpiry 1.2.0、QRCodeExtend 1.2.0、FlowScope 2.4.0 的真实生命周期，核验 Admin 字节及不同启用顺序；Root 证据与合成业务表保留 |
| 管理员权限 | 真实 Sanctum：匿名/普通用户拒绝；通用插件配置不能绕过自有 HTTP 确认；请求限速；独立页面不改 Admin bundle |
| 地址与协议 | IPv4、IPv6、DNS/punycode、尾点、端口、网站冲突、非法输入、HTTPS/WSS 与显式 HTTP/WS 均覆盖；非法保存不产生部分更新 |
| 双 10 年 PKI | 真实 OpenSSL 验证日历 10 年、首次截止对齐、续签受 CA 上限约束、CA/leaf 扩展、IP/DNS SAN、加密 CA 私钥、实际日期与 180/90/30 提醒 |
| TLS 拒绝条件 | 错误 CA、过期/未来证书、错误 SAN、弱 CA、私钥混入、重复根及篡改自签名均拒绝；始终检查链和主机名 |
| 同 CA 换钥与双 CA 轮换 | 真实签发和本地链验证；原信任下接受新服务器私钥；两根独立指纹全部校验、接受两条链、分阶段切换和完成 |
| REST 与 WSS 分离 | Python 客户端协议套件核验 REST 身份、HTTP 101、WebSocket Accept、auth.success 和身份匹配；VM 实际 Go 在合成面板上验证持续认证 WSS、Node状态和live pong；后续实际原版 NodeWorker 冷启动认证、Redis 事件送达及四绑定计量已验证；UUID 热更新失败另列，见 FULL_E2E_TEST_REPORT |
| 作用域 handshake | 原始 Controller 响应经过插件 middleware，仅改独立入口；旧域名及全局设置保留；无 heartbeat/REST 模式保持官方 fallback；无凭据 GET handshake 实测 422 |
| 新节点命令 | node/machine × sing-box/Xray × AMD64/ARM64 命令核验，无 Token；Xray禁tuic/anytls，hysteria2 auth alias规范为hysteria；prepare依据实际REST协议/版本和machine全部bindings在WSS认证、stage前拒绝不兼容配置 |
| 多实例迁移与失败恢复 | 单字段字节保留、身份/原URL、格式/环境和损坏备份/并发修改拒绝由基础套件覆盖；VM 真实Go/systemd标准两实例Xray、多bindings选中URL迁移及真实CLI rollback通过，其他URL/token/binary/meta/sentinel保持 |
| WSS 认证副作用 | 源码审计发现 NodeWorker 注册连接并清理设备状态；默认 preflight 不认证 WS，额外确认才认证；迁移备份停服后认证、启动后只作 REST 检查。后续实际联测观察代理活跃设备及停止 Go 后设备归零，见 FULL_E2E_TEST_REPORT |
| Root 配置冲突、幂等与回滚 | 解析器及单元 fixture 覆盖状态边界；VM 真实 Nginx 和 Root CLI / PHP-FPM 补充监听冲突、路径限制、故障恢复、重载和独立资源归属检查，原站资源保持 |
| Root 归属与导出保护 | 新/空状态目录领取标记，外来目录在 chmod 前拒绝、拷贝标记拒绝、配置可写祖先拒绝；卸载后仍保留应用路径的私钥导出禁令。VM 另完成真实 UID/mode/links 和独立进程 flock 14 项检查 |
| 管理页 | 手动 Bearer 管理员认证、Token 仅存内存、输入清空、HTTP 风险确认、URL 预览、两种身份和命令、刷新退出、桌面/手机布局；密码登录/CAPTCHA 未执行 |

管理页截图为合成数据：[桌面](docs/screenshots/settings-desktop.png)、[手机](docs/screenshots/settings-mobile.png)、[登录页](docs/screenshots/login-desktop.png)。

发行 ZIP 在隔离组件中实际上传并安装通过；这不是生产 Admin 浏览器上传验收。三个包均完成 ZIP CRC、唯一安全路径、逐文件 SHA-256、插件版本和私钥/运行目录排除检查，重复构建摘要相同。每包的 `MANIFEST.sha256` 不包含自身摘要，外层完整包摘要由 `dist/SHA256SUMS` 和 `release.json` 提供。

额外实际检查：插件 12 个 PHP 文件和 purge CLI 全部语法通过；项目 Python 文件解析通过；前端 JavaScript、浏览器 runner 和 Bash wrapper 语法通过。独立 Node 两个 release binary 实际下载，SHA-256 与 ELF AMD64/ARM64 标识匹配；AMD64 后续已在 VM 执行，ARM64 仍仅完成下载和标识校验。资产摘要见 `tools/node/release-lock.json`。

## 后续完整实机联测与最终发行包

实际宝塔面板 VM 与节点 VM 共同执行原版 Xboard/Laravel/MariaDB/Redis/队列/NodeWorker 与固定 Go 的冷启动认证、选择迁移/回滚、CA 换钥与双根过渡、Redis 事件分发、设备清理、新装、四绑定流量计量和最终恢复。Xray 全量及单用户增量 UUID 热更新失败，Observer 的 UUID 触发链仍有未定位缺口，不能称完整联测全部通过。Root 产品发现的双 Nginx master 与 quoted brace 解析问题已修复，Windows/Linux 回归各 36 项。证据、失败历史及具体边界见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。

上述 110/139 和 52 文件均指历史基线；当前包以 `dist/release.json`、`SHA256SUMS` 和 `dist/verification/final-package-results.json` 为准。最终重建包在两平台各通过 6 项原生组件测试并逐成员比较，未把它伪称为完整面板同字节再次安装。重复构建逐字节相同，私钥、凭据和运行目录排除验证通过。

## NOT TESTED 与交付限制

- Octane、Docker、其他 OpenResty/宝塔布局及生产防火墙/云安全组；本轮实际宝塔 Nginx/PHP-FPM 与专属 UFW/nftables 规则已验收，已有 Docker/FlowScope 服务保持检查不能作为 Docker 应用部署验收。
- ARM64 执行、真实订阅客户端协议部署、生产流量与计量连续性；真实 Linux 已验证的行为以 VM_TEST_REPORT 范围为限。
- 跨互联网/IPv6 网络及旧域名实际 DNS 故障后的持续代理流量。本地 IP TLS 成功只证明测试连接无域名依赖。
- Xboard-Independent 记录的 `403604e` PHP overlay 未提供源码；官方基线与保留 Admin 已审计，不能据此声称实际 overlay 运行通过。
- 生产密码登录/CAPTCHA、公网 IP 检测外部服务、未知未来 Admin/TrustProxies 构建的兼容性。

V1 保留所有已确认需求；对自定义 Node unit、Docker Node、复杂 YAML、无法证明稳定数据目录的多实例等暂不提供自动迁移，遇到这些布局安全拒绝。旧 CA 最终清理由管理员按节点绑定逐项确认；没有自动移除旧信任、CRL/OCSP 吊销检查或 FlowScope 采集器自动迁移。原生卸载一致保留自身恢复设置，另提供明确确认的本地 purge；独立 HTTPS 入口须通过 Root 工具单独卸载。

## 复现

依赖安装与 PHP 运行设置见源码包内 `tests/integration/README.md`。源码包不携带 `.audit`，需按 SOURCE_AUDIT 的 commit 准备只读参考检出；Composer/npm 依赖用各自 lock 文件安装。在项目根目录运行：

```powershell
$env:OPENSSL_BINARY = 'C:\Program Files\Git\mingw64\bin\openssl.exe'
$env:PATH = 'C:\Program Files\Git\mingw64\bin;' + $env:PATH
python tests/panel/test_panel.py
python tests/node/test_node_endpoint.py
php -c .runtime/php.ini tests/integration/run.php
# 先按原生测试说明启动仅 loopback 的 PHP fixture
node tests/ui/run.cjs
python tools/build/build.py
python tools/build/verify.py
php -c .runtime/php.ini tests/integration/package.php
```

其他机器应使用实际本地 Python/PHP/OpenSSL 路径；`.runtime/php.ini` 是开发机配置，不在发行包内。Linux VM 结果见 [VM_TEST_REPORT.md](VM_TEST_REPORT.md)，目标部署验收要求见 [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)。当前交付已完成多项隔离环境验收，尚未完成生产上线。
