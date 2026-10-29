# 变更记录

## 1.1.2 — 2026-10-30，Node v9 配套修复

- 新机器安装命令固定 Node `v1.13-orphan.9`，独立 tools 为 `1.0.11`；已核验六项本地资产并填入精确来源与摘要锁，保留旧版资产。
- ACME 修复针对混合大小写域名的 Order / CSR literal 不一致，在 ACME 入口与配置指纹使用相同规范化；不更改 TLS 校验或 self / file / content 模式，不删除现有证书缓存。
- 已确认旧 nft 0.9.0 的成功 null 输出且独立空表检查成立；v9 按此限定兼容，并改用 ip / ip6 NAT 与规则所有权标记。其他未知输出仍拒绝，旧 v8 inet 表仅在核验后迁移。真实 4.19 内核尚未启动验收。
- 保留 v8 的 owned UDP INPUT、关闭选择与兼容边界。Root installer `1.0.10` / Root tools `1.0.6` / helper `1.0.0` 不变。见[验证记录](docs/RELEASE_VALIDATION_NODE_V9_20261030.md)。

## 1.1.1 — 2026-10-30，Hysteria2 监听口自动放行

- 新机器安装命令固定独立 Node `v1.13-orphan.8`；独立 tools `1.0.10` 同步使用该版精确资产锁。旧包与历史摘要保留。
- 标准 Node 安装器显式启用 owned UDP INPUT 管理，覆盖单端口和跳跃 Hysteria2 的实际监听口；启动、配置变化和每 30 秒核对并恢复自己的规则。
- 支持 iptables-nft / legacy 的 IPv4、IPv6；本地关闭优先，自定义无启用环境变量时默认关闭。额外原生 nftables INPUT 与 firewalld 需人工处理。
- 不修改提供商持久文件、不保存全规则或改变默认策略；重载后恢复有窗口，云安全组仍需人工配置。详见[操作说明](docs/HY2_INPUT_FIREWALL.zh-CN.md)。
- 保留 1.1.0 的登录复用与网页 Root 管理；Root installer `1.0.10` / Root tools `1.0.6` / helper `1.0.0` 不变。Node 本轮隔离验证及插件本地组件结果见[发行记录](docs/RELEASE_VALIDATION_HY2_INPUT_20261030.md)，最终资产与公开下载另行核验。

## 1.1.0 — 2026-10-29，管理员登录复用与网页 Root 任务

- 同域页面复用原生面板管理员登录，处理过期、退出和账号切换，保留手动登录。
- 新增受限 Unix socket 本机助手：固定入口配置、路由刷新、服务器证书续签、已批准端口规则和公共 CA 同步。
- 预览和完整文本确认后异步执行；数据库保存管理员任务所有权，缓存清空后可恢复。
- Root 初始化固定为 `root-installer-v1.0.10` / 原有 `tools 1.0.6` / 新 `web-manager 1.0.0`。原生面板全新初始化一次安装入口和助手，已有入口可单独安装助手。
- 保留既有 CA、设置和插件启用选择；不开放任意命令、私钥导出、CA 轮换或 Docker 特权接口。见[操作说明](docs/WEB_MANAGER.zh-CN.md)。

## 1.0.10 — 2026-10-28，设备限制修复配套更新

- 原生机器管理的域名／IP 新装命令固定到独立 Node `v1.13-orphan.7` 的正式 Release 安装器；`--version` 同步固定，避免新机器继续安装 v6。
- 原生插件 ZIP 与独立 tools `1.0.9` 同步更新 Node 发行锁、四个程序和安装器摘要，配套后端 `v2026.10.28.1` 的在线 IP、跨节点原子设备限额和断网清理修复。
- 更新保留配置升级教程；已有 Node 需单独使用 `upgrade`。Root 入口仍为 `root-installer-v1.0.9 / tools 1.0.6`，历史包与历史验收记录保留。
- 本次联动验证与业务测试适用范围见[发布说明](docs/DEVICE_LIMIT_RELEASE_20261028.md)，旧版插件组件结果不作为 1.0.10 的重跑结果。

## 1.0.9 — 2026-10-25，node菜单与Hysteria2自动转发

- 原生机器管理的域名／IP命令固定到独立Node v1.13-orphan.6，独立迁移工具tools1.0.8同步固定资产摘要。
- 配套后端v2026.10.25.2和Node支持根据面板配置自动管理Hysteria2 UDP跳跃范围转发、更新和退出清理；不代替主机及云安全组UDP放行。
- Node新增完整`node`菜单：修改面板地址／Token、查看最近日志、状态、重启和经校验的最新正式Release在线更新；先认证再修改，保留身份和CA，失败尝试恢复。
- 新增首次配套升级及后续菜单更新说明。Root入口仍为root-installer-v1.0.9/tools1.0.6，其他插件版本保持，二维码继续为1.2.0审计副本。
- 47项自动转发与13项菜单候选联测、正式v6资产验收及边界分别保留独立证据，详见[发行验证](docs/RELEASE_VALIDATION_NODE_MENU_20261025.md)。

## 1.0.8 — 2026-10-25，限速与路由配套更新

- 原生机器管理的域名 / IP 两种命令切换到独立 Node v1.13-orphan.5 固定安装器与二进制版本，保留原机器身份和私有 CA 参数。
- 配套独立后端 v2026.10.25.1 与 Node v1.13-orphan.5 修复已有连接限速更新、sing-box 低速与 Linux 转发门控、Xray 上传限速、路由 / 出站完整切换及失败回滚；后端提交后通知、隐藏启用节点、路由顺序和负数限速校验一并修复。
- 独立 Node 迁移工具包 tools 1.0.7 更新 Node 发行锁与真实资产摘要。Root 面板入口仍为 root-installer-v1.0.9 / tools 1.0.6，不为未改变的入口配置重复升级；已有 CA、口令和设置保留。
- 新增[已有面板与节点更新教程](docs/UPGRADE.zh-CN.md)。只更新插件 ZIP 不会修复已运行的后端或 Node；已有节点使用 upgrade，不能执行替换全部对接的新装命令。
- 证据为 80 项有效 TCP 联测、最终源码 Linux 并发检查及 13 个后端场景；测试边界见[修复报告](docs/RATE_ROUTE_FIX_REPORT.md)。

## 1.0.7 / root-installer-v1.0.9 — Docker 简化安装

- 标准 Compose 镜像和局部数据挂载支持宿主机 Root 自动配置：识别反代、以普通 UID 执行固定助手、随机 CA 口令加密托管、通讯设置与公共 CA 自动同步。
- 展示完整计划后一次确认。容器重建需 Root 重新审阅，并用原 CA 私钥验证托管口令后换绑；失败保留原 CA 和配置。
- 独立全插件后端新增 `bash docker-install.sh`，默认宝塔 MySQL / MariaDB + 内置 Redis，填写数据库连接、管理员邮箱和通讯地址后一次确认；可选 SQLite。已有同版本安装重跑不初始化数据库。
- 网页复制命令固定到 Root v1.0.9 / tools 1.0.6，二维码插件仍为 1.2.0 审计副本。

## 1.0.6 / root-installer-v1.0.8 — 私有入口 IPv4 上报修复

- 为独立入口补齐 FlowScope 的精确 `POST /api/v2/flowscope/node-address`；不放宽整个插件 API、管理页面或周期流量接口。原 Node 核心通讯与可选 FlowScope 路由分开审计，未安装插件的面板保持兼容。
- 网页复制 Root 命令固定到 root-installer-v1.0.8 / tools 1.0.5。新增 `--refresh-entry`，从已有安装记录读取参数，审阅并确认后刷新原生 HTTPS FPM / Octane 入口；不写插件设置或防火墙。仅上传插件、同步 CA 或补防火墙不会更新已加载的 Nginx 路径。
- 同地址修改保留 CA、服务器证书、托管口令与节点身份。Node 对旧 404 最多等待 10 分钟后自动重试，不需要重装节点、改 Token 或强制重启。
- README 与工具说明同步独立全插件后端 v2026.10.19.2。当前验证状态见 [私有入口 IPv4 修复报告](docs/FLOWSCOPE_PRIVATE_ENDPOINT_TEST_REPORT.md)。

## 1.0.4 / root-installer-v1.0.5 — CA 口令自动生成与面板加密托管

- 新 CA 自动生成随机口令，按 APP_KEY 加密保存到独立表；签发前保存待完成凭据，Root 只保存不透明 ID，中断后核对证书再绑定。
- HTTPS 管理员显式查看 / 复制，单独限流与 no-store；默认设置、节点命令及公共同步不携带凭据。浏览器超时、隐藏页面或鉴权失效清空。
- 旧 CA 继续使用原口令和原信任；禁用、卸载及普通 purge 保留托管恢复数据。
- 安装确认改为中文并允许交互重试，完整参数确认保持严格；UFW 精确空规则表使用普通添加，已有或未知规则仍按原有优先级检查。
- 安装教程补齐插件和内置后端的从零安装、原版面板升级、Composer / PHP / 确认中止恢复与云安全组步骤。

## Root 防火墙一体化 — root-installer-v1.0.4 / tools 1.0.3

- 标准 PHP-FPM 一键安装在独立入口通过检查后，按已提交的安装记录配置本机 TCP 端口，再尝试自动导入公共 CA。默认允许所有来源访问该通讯端口；自管规则可使用 `--skip-firewall`。
- firewalld 核对默认和活动 zone 的运行时及永久规则；UFW 保存入站允许规则，未启用时明确报告并保持未启用。双管理器冲突、未知规则体系或无法确认的规则优先级要求人工处理，不清空规则、不改变默认策略、不自动启用防火墙。
- 已有入口使用 `--configure-firewall` 单独补规则，校验 Root 状态、独占锁、配置摘要、归属和实际配置端口，不进入入口修改或证书重签流程。`--sync-ca` 与 `--detect-only` 保持单一职责。
- 防火墙失败仍尝试 CA 导入，报告各步骤并以非零退出，保留已成功安装的入口；云安全组和服务商防火墙由管理员另行配置。
- README 重写从零安装、原版面板保留数据接入、插件升级、Root 一键入口和节点新装 / 迁移说明。插件继续使用已发布的 1.0.3 ZIP，Xboard 核心和 Node 二进制未修改。
- Windows / Linux 各 98 项面板工具测试及真实隔离 UFW 双协议、幂等、重载持久性、自定义端口 / 篡改拒绝通过，宿主防火墙规则原样。firewalld 为命令替身验证；完整新装及业务未重跑，详见 [ROOT_FIREWALL_TEST_REPORT.md](ROOT_FIREWALL_TEST_REPORT.md)。

## 1.0.3 — 域名选项改为独立部署安装命令

- 默认域名选项改名为“域名对接（独立部署）”，使用原面板 app_url 和 ksr-v 独立固定安装源；默认使用系统 / 公有 CA 信任，不携带 NodeEndpoint 私有 CA。IP 选项继续使用配置的 IP / 端口并嵌入公共 CA 与指纹。
- 两种选项均固定 nodeendpoint-native-v1.0.0 安装源及 v1.13-orphan.4 Node / xbctl；本次不更新节点安装器或 Go 二进制。默认 command / install_command 改为独立域名命令，与 install_options.domain.command 一致，原机器身份保留。
- IP 入口或 CA 未就绪不影响独立域名命令、新建机器；禁用 NodeEndpoint 插件本身后恢复宿主原生行为。界面仍通过插件动态资源加载，不修改 Xboard 核心或 Admin bundle。
- 从 1.0.0 / 1.0.1 / 1.0.2 上传 NodeEndpoint-1.0.3.zip 升级、确认启用并刷新；保留设置与 CA，无需卸载其他插件、升级 Xboard 或重装 Root 入口。
- 本轮验证范围见 [NATIVE_DOMAIN_TEST_REPORT.md](NATIVE_DOMAIN_TEST_REPORT.md)；1.0.2 及更早报告保留，历史 UUID / Observer 限制继续保留。

## 1.0.2 — 原生机器安装命令提供域名 / IP 双选

- 机器详情的安装区与新建后的 Token 弹窗同时提供“原版：域名对接”（默认）和“新版：IP + 私有证书”。原版完整保留宿主 app_url、cedar2025 来源及机器身份；新版使用独立 ksr-v 固定安装源、插件配置 IP 与自动公共 CA 信任。
- 保留原生 command / install_command，追加 owner / schema 标记的 install_options。缺少有效 CA、独立入口禁用或使用 HTTP 时，仅禁用 IP 选项；原版命令、新建机器及原生验证错误 / 更新语义保留。
- 动态 View composer 加载 extensionless 外链资源，不修改 Xboard 核心或编译后的 Admin bundle；与 FlowScope 的模板包装共存。浏览器被动观察原生机器响应，不读取认证 storage / Fiber、不改变请求和响应或 clipboard 实现，不持久化安装命令；最多缓存 20 条，未使用 10 分钟过期，退出 / 认证失败 / 禁用时清理。
- 从 1.0.0 / 1.0.1 上传 NodeEndpoint-1.0.2.zip 升级并刷新；无需卸载插件、升级 Xboard 主程序或重做 Root CA。更改通讯设置 / CA 后刷新原生面板，清除旧命令缓存。
- 本次未变更节点安装器，仍固定 nodeendpoint-native-v1.0.0 与 v1.13-orphan.4 二进制。Windows / Linux 的 34 项原生组件及 6 项视图资源 / 共存检查通过；浏览器验证与精确范围见 NATIVE_INSTALL_CHOICES_TEST_REPORT.md，历史 UUID / Observer 失败仍保留。

## 1.0.1 — 原生机器命令自动携带独立 IP / CA

- 在插件 API middleware 中按原生 MachineController action 处理新建机器和复制安装命令；采用独立维护的固定安装源、已保存的 IP/端口、公共 CA 和指纹。缺少信任时在创建机器之前停止。保留原生管理员权限、机器 Token 和命令参数语义。
- 不更改 Xboard 核心或 Admin bundle；原生升级保留 1.0.0 的设置及 CA，Root 入口与 PKI 不重装。关闭独立入口后恢复宿主原生行为。
- 独立 Node 安装器增加 CA 校验、实际 HTTPS 身份预检、固定二进制摘要、单服务 SSL_CERT_FILE 信任、升级保留及失败回滚/卸载处理；Go 二进制维持 v1.13-orphan.4。
- 30 项 Laravel 原生组件通过；Bash 20 项中 19 通过、1 因隔离目录无发行镜像跳过；Linux Root CA 7 项通过。专用两 VM 实测原生机器生成命令、IP HTTPS、新装、NodeWorker WSS auth.success、升级保留 CA、故障恢复和卸载清理。新建机器无业务节点绑定，本轮未复测代理/流量/UUID；历史失败继续保留。详见 NATIVE_MACHINE_TEST_REPORT.md。

## 同机公共 CA 自动导入 — root-installer-v1.0.3 / tools 1.0.2

- 新安装完成后自动导入公共 CA；已有入口可通过 `--sync-ca` 单独执行，无需重新上传插件、输入管理员 Token 或重新生成证书。
- Root 校验自己管理的安装记录/CA/指纹后，仅将公共证书通过 stdin 传给降权后的应用 PHP，复用插件证书校验并原子写入应用用户自己的 0600 文件。Web 不执行 Shell，私钥/口令不进入应用。
- 校验插件启用状态及通讯地址/端口/HTTPS，拒绝符号链接、异常属主和写权限；重复同步幂等，支持 Root 双 CA 信任。Docker/异机不猜测路径；Node 信任仍需独立分发/确认。
- 新增 8 项载荷边界测试及隔离 Laravel/SQLite 真 PHP 单/双 CA、原生页面控制器读取、非 Root 权限、私钥不可读、错误指纹/禁用插件/地址不一致/危险路径拒绝测试，不使用真实面板数据库。

## Nginx 解析兼容修复 — root-installer-v1.0.2 / tools 1.0.1

- 修复原解析器将合法 `${变量}`、词内 `#`、部分转义和已知 Lua 块误作 Nginx 结构的问题；按 Nginx token 规则保留参数，Lua 字符串/注释/嵌套表只扫描、不执行。
- 解析失败带配置文件/行/列，不输出正文或参数；包含文件保留来源，`nginx -T` 分段保留原文件起始空行以便正确定位。
- 新短命令固定校验新的 Python 安装器及 tools 1.0.1，使用独立摘要缓存，不覆盖旧包或要求重装面板插件。补丁工具包仅替换原固定工具包的 nginx.py、manager.py 和成员摘要清单。
- Windows/Linux 各 54 项回归通过；真实测试宝塔 Nginx 的隔离 `-T`（含 Lua）复现旧错误且修复通过，原面板只读发现及配置/权限/入口状态不变。生产具体触发指令尚未取得，未把同类复现视为已在生产验证。

## Root 短命令入口 — root-installer-v1.0.1

- 新增 `install.sh`，支持 Root SSH 使用 `curl … | bash` 自动下载并校验既有安装器，继续下载固定 tools、进入原向导；面板已启用的插件无需重新上传。
- 检查 Root 身份和安全目录，临时目录/脚本使用 0700/0600，自动清理；管道启动后从 SSH TTY 读取交互，不修改整站属主或权限。
- 原 v1.0.0 ZIP 和 root-installer-v1.0.0 Python 向导保持原字节。
- 测试面板真实下载、只读发现、非 Root/无交互拒绝、Root 临时文件权限、管道转 TTY 与 Ctrl+C 清理通过；原 Nginx 配置字节/权限及独立入口状态不变，未执行完整安装。

## Root 一键安装器补充 — root-installer-v1.0.0

- 新增独立 Root SSH 安装向导，先校验固定工具包与逐成员清单，再按原 Nginx AST 识别 FPM 站点、后端、路径及 WS；多候选交互选择，不确定布局转入手工菜单。
- 首条命令校验安装器 SHA-256；Root 缓存拒绝篡改、链接与外来目录，下载不关闭 TLS 校验。原 v1.0.0 三个 ZIP 不替换。
- Windows/Linux 各 8 项测试与实际面板只读发现通过；没有对生产执行安装，也未宣称新增向导完整交互安装已验收。

## 1.0.0 — 安装文档与首次发布准备

- README 增加空服务器安装 Xboard 的固定基线路线，以及已有原版面板备份、保留数据接入插件、独立入口、CA 分发和分批迁移步骤。
- 明确面板主程序升级、NodeEndpoint 插件升级和节点地址迁移的区别；补齐新装命令、FPM/WS 示例和验收清单。
- 增加发行说明，随三种发行包一同提供；保留上游 UUID 更新缺陷、未测范围和机器恢复差异。

## 1.0.0 — 2026-10-17 清理恢复

- run007 迁移/回滚业务通过，初次清理失败保留；辅助脚本正确处理空自有 drop-in 目录，13 项本地回归通过，独立清理恢复另有证据。
- 用户恢复虚拟机后，记录初始 PID、外来 nft 及 9 个原站文件元数据差异，核验原文件字节与恢复后状态；完成原生插件/Root 入口/合成数据/临时认证和 SSH 清理。

## 1.0.0 — 2026-10-10 完整两 VM 联测

- 执行实际宝塔 Nginx/PHP-FPM、现有 Xboard/MariaDB/Redis/队列/原版 NodeWorker 与固定 AMD64 Go 的 node/Xray/VLESS、machine/sing-box/SOCKS5 四绑定联测，冷启动认证、流量计量及设备断连清理通过，Redis UUID 事件送达与认证更新失败分别记录。
- 实际完成原生插件权限/生命周期/单双 CA API、Root 双 10 年 PKI/加密导出/换钥/回滚/双 CA 过渡、选择迁移/回滚、新节点安装及最终清理恢复。
- 修复显式网站 Nginx 二进制选择下不同控制服务 master 的误判，保留同 executable/inode 多 master 的拒绝；修复 Nginx quoted brace 解析。Windows 与面板 Linux 回归各 36 项通过。
- 保留 run001 隔离规则失败、run002 重复同叶检查的辅助断言失败及安全清理历史；接续轮完成其余测试，同叶幂等真实回归证据保留。独立记录 unattended Redis 升级及原队列小时退出/依赖断连恢复。
- run003 实测固定 Go 的 Xray 同 ID UUID 全量更新缺陷；run004 模型 save 未观测指定用户推送，原因尚未定位；run005 原生 NodeSyncService/Redis 事件确已送达，但单用户 UUID 增量移除会停止 Xray 内核，后续添加及 fallback 失败。三个 FAIL 均保留，未改 Go，空用户集源码限制另列未测。
- 新增 FULL_E2E_TEST_REPORT 与可复现辅助源码/公开 JSON；最终重建三种 ZIP、成员 manifest、SHA-256 和两平台原生包复测。未执行生产部署或参考仓库推送；未测范围继续保留。

## 1.0.0 — 2026-10-10 VM 验收补充

- 在用户授权的 Ubuntu 24.04.3 x86_64 专用 VM 执行真实 Nginx、Linux UID/mode/flock、Root CLI / 独立 PHP-FPM、原生组件与发行 ZIP 验证；原有 Docker、FlowScope 采集器和系统 PHP-FPM 保持运行。
- 追加 VM_TEST_REPORT，区分首轮基线、修复后的最终回归、合成 Python 面板 Go 测试和独立 PHP 原生路由测试，避免累计为新增唯一用例。
- 修复新装协议选择：Xray 拒绝 tuic/anytls，保留并规范 hysteria2 auth alias；prepare 根据每个实际REST协议/版本在WSS认证与stage前拒绝不兼容配置。Windows/VM Node36、PHP26与Windows UI9回归通过。
- 真实固定AMD64 Go/systemd最终11项通过：新装、双内核、认证WSS、换证、标准多实例选中URL迁移及CLI回滚；公开记录失败轮次、独立asset staging与仅fixture自有unit网络隔离。
- 此历史阶段使用合成面板，完整实际 Xboard / NodeWorker / Go 联测在后续报告单独记录；其他部署方式、ARM64、公网/IPv6、实际 DNS 故障与生产连续性边界继续保留。

## 1.0.0 — 2026-10-09

- 新建 Xboard 原生 NodeEndpoint，标识 `node_endpoint`，自有设置、管理员鉴权和独立管理页。
- 增加 IPv4/IPv6/DNS、HTTPS/HTTP 显式确认、完整 REST/WS URL、公共 IP 检测与独立连接状态。
- 增加新入口请求局部 handshake WSS 地址处理，保持原全局 `server_ws_url` 与旧节点行为。
- 增加双 10 年私有 PKI、离线 CA 管理、主动重签、紧急换钥、CA 渐进轮换和到期检查。
- 增加安全 Linux Root 入口检测、安装、备份、reload、回滚与单独确认卸载工具。
- 增加独立 Node 固定版本/摘要对接、node/machine、sing-box/Xray 与 AMD64/ARM64，以及严格实例迁移/恢复工具。
- 完成六仓库源码审计、本地组件与协议测试，交付原生 ZIP、工具 ZIP、源码 ZIP、版本与 SHA-256。
- 原生卸载保留自己的恢复设置，提供显式 CLI purge；初始 Windows 验收与后续 Linux VM 结果分别见 TEST_REPORT 和 VM_TEST_REPORT，生产验收尚需执行。
