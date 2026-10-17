# 变更记录

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
