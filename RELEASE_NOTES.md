# NodeEndpoint 1.0.4 / root-installer-v1.0.5

新 CA 的加密口令自动随机生成，先按面板原 `APP_KEY` 加密保存到独立凭据表，再由 Root 签发证书。管理员在 HTTPS `/node-endpoint` 证书区按需查看和复制；普通设置、状态、节点命令和日志不包含口令。CA 私钥继续放在网站之外的 Root 私有目录或离线介质。

原有独立面板先原生上传 `NodeEndpoint-1.0.4.zip` 升级并确认启用，再在面板服务器以 Root 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.5/install.sh | bash
```

已有 CA 不换口令、不重新签发，也不要求重新对接节点；未托管的旧 CA 维护时继续使用原口令。新 CA 不再出现两次隐藏口令输入。Root 仍要求确认完整的 `INSTALL IP:端口`，误输可重试，`CANCEL` 取消。面板数据库和原 `.env` / APP_KEY 必须一起备份。

本版保留宝塔 Nginx 解析和防火墙一体化，并修复 UFW 空规则表不能 `insert 1` 的情况。内置插件后端版同时保留 Composer 版本检查、缓存权限、PHP `symlink` 前置检查和已安装面板恢复流程。

- 独立插件安装及升级：[README](README.md)
- 全新内置插件后端及原版面板升级：[宝塔教程](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.18.4/docs/installation/aapanel.zh-CN.md)
- 托管、备份及验证边界：[CA_PASSPHRASE_TEST_REPORT.md](CA_PASSPHRASE_TEST_REPORT.md)

## 历史 Root 防火墙一体化：root-installer-v1.0.4

面板 Root 一键安装现在串联 **独立 HTTPS 入口 → 本机 TCP 端口放行 → 公共 CA 自动导入**，使用固定校验的 tools 1.0.3。

新入口在面板服务器以 Root 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.4/install.sh | bash
```

已有入口只补本机防火墙，无需重装或输入 CA 口令：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.4/install.sh | bash -s -- --configure-firewall
```

默认允许所有来源访问安装记录中的 TCP 通讯端口；支持 firewalld 默认 / 活动 zone 的运行时和永久规则，以及 UFW 持久规则。未启用 UFW 时仅保存规则并明确报告，保持未启用。冲突或不支持的策略需要手动处理；云安全组 / 服务商防火墙仍需另行放行。自管策略使用 `--skip-firewall`，单独 CA 同步使用 `--sync-ca`。

本次只更新 Root 工具和文档。面板插件继续使用 **NodeEndpoint-1.0.3.zip**，无需重复上传、卸载其他插件、升级 Xboard 或重做 CA。README 已重写从零安装、原版面板保留数据接入、机器双选命令及保留配置迁移说明。原有 Release 及 ZIP 保持原字节。

验证范围和限制见 [ROOT_FIREWALL_TEST_REPORT.md](ROOT_FIREWALL_TEST_REPORT.md) 及本次 Release 的结果 JSON。

## NodeEndpoint v1.0.3 历史发布说明

默认域名选项现为 **域名对接（独立部署）**，也使用你的 ksr-v 独立部署安装源。两个选项分别为：

- **域名对接（独立部署）**（默认）：对接原面板 app_url，默认使用系统 / 公有 CA 信任，不携带 NodeEndpoint 私有 CA。
- **新版：IP + 私有证书**：对接 NodeEndpoint 保存的 IP / 端口，自动携带已导入的公共 CA 和指纹。

两种命令均使用 [Xboard-Independent/nodeendpoint-native-v1.0.0](https://github.com/ksr-v/Xboard-Independent/tree/nodeendpoint-native-v1.0.0/node-installer) 固定安装源，Node / xbctl 均固定 v1.13-orphan.4。默认 command / install_command 也改为独立域名命令，与 install_options.domain.command 一致。IP 入口或 CA 未就绪只影响 IP 选项，域名复制与新建机器仍可用。禁用 NodeEndpoint 插件本身后，宿主原生安装命令行为恢复。

从 [v1.0.3 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.3) 下载 **NodeEndpoint-1.0.3.zip** 和同次 **SHA256SUMS**。备份设置，上传 ZIP 原生升级、确认启用并刷新面板，然后重新复制安装命令。升级保留原有通讯设置和 CA；无需先卸载 NodeEndpoint 或其他插件、升级 Xboard 主程序、重装 Root 入口或重新生成 CA。

本次仅更新插件，不更新节点安装器或 Go 二进制。两种原生机器命令仍含 Token 参数，请勿公开命令或 Shell 历史。IP 私有 CA 分支保持原有证书与二进制摘要校验；无私有 CA 的域名分支采用独立源固定版本下载流程。验证范围见 [NATIVE_DOMAIN_TEST_REPORT.md](NATIVE_DOMAIN_TEST_REPORT.md)，不替代代理 / 计量 / UUID 的完整联测，历史 UUID / Observer 限制继续保留。

## v1.0.2 历史发行说明

原生机器管理的详情安装区和新建后的 Token 弹窗增加两种安装方式：

- **原版：域名对接**（默认）：完整保留宿主原生 app_url、cedar2025 安装命令和机器身份。
- **新版：IP + 私有证书**：使用 ksr-v 独立固定安装源和 NodeEndpoint 配置的 IP / 端口，自动携带私有 CA 的公共证书及指纹，并在节点安装时配置服务信任。

IP 入口未启用、公共 CA 无效或使用 HTTP 时，仅禁用新版选项，原版复制与新建机器仍可用。后端保留原生 command / install_command，仅追加 install_options；动态 View composer 加载 extensionless 外链资源，不修改 Xboard 核心或 Admin bundle，可与 FlowScope 的动态模板包装共存。

从 [v1.0.2 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.2) 下载 NodeEndpoint-1.0.2.zip 和同次 SHA256SUMS。原生上传升级、确认启用并刷新面板即可；无需先卸载 NodeEndpoint 或其他插件、升级面板主程序或重新生成 Root CA。此后在安装区选定方式再复制。修改通讯设置、导入或轮换 CA 后也应刷新面板，避免原生 React Query 使用旧命令。

本次只更新插件；独立安装源仍为 nodeendpoint-native-v1.0.0，Node / xbctl 仍固定 v1.13-orphan.4。两种原生机器命令均保留 Token 参数，请勿公开命令或 Shell 历史；私钥不进入面板或命令。

Windows / Linux 的 34 项原生组件与 6 项视图、资源和 FlowScope 共存检查通过；浏览器结果及测试边界单列于 [NATIVE_INSTALL_CHOICES_TEST_REPORT.md](NATIVE_INSTALL_CHOICES_TEST_REPORT.md)。这次双选验证不替代代理 / 计量 / UUID 的完整联测，固定 Go 的 UUID 更新与 Observer 历史限制继续保留。生产服务器未操作。

## v1.0.1 历史发行说明

原生机器管理的新建/复制安装命令自动使用你的独立固定安装源、通讯 IP/端口和已导入公共 CA，保留原有机器身份。节点端自动校验证书、预检 HTTPS 并配置单服务信任。独立 Node 安装源同步升级，二进制仍是固定 v1.13-orphan.4。

附件：NodeEndpoint-1.0.1.zip、SHA256SUMS、release.json、native-machine-install-results.json。上传 ZIP 原生升级，确认启用；无需先卸载插件、升级面板主程序或重新生成 Root CA。升级后重新复制原生机器安装命令，在新 Node 服务器执行并选择 1 安装即可。

本轮 30 项原生组件、19 项 Bash（另 1 项隔离路径跳过）、7 项 Linux Root CA 通过；真实两 VM 的 IP HTTPS/NodeWorker WSS 认证、新装、升级保留 CA、故障回滚和卸载通过。测试机器没有业务节点绑定，历史 UUID/Observer 限制继续保留，详见 NATIVE_MACHINE_TEST_REPORT.md。原生机器命令沿用 Token 参数，请勿公开命令或历史。原生产节点未进行操作。

## v1.0.0 历史发行说明

为现有 Xboard 增加独立 IP/域名 HTTPS 节点通讯入口，默认端口 8443。原生插件管理页与 Root/Node 工具配合使用，保留原网站及已有面板数据。

## 发行附件

- `NodeEndpoint-1.0.0.zip`：在 Xboard 原生插件管理中上传、安装、启用。
- `node-endpoint-tools-1.0.0.zip`：面板 Root 入口/PKI 管理与 Node 新装、选择迁移、回滚。
- `xb-node-endpoint-1.0.0-source.zip`：源码、测试和文档。
- `SHA256SUMS`、`release.json` 和 `final-package-results.json`：同次构建的摘要及验包结果。GitHub 自动生成的 Source code 归档不能替代原生插件 ZIP。

README 包含空服务器安装 Xboard、已有原版面板保留数据接入、插件安装、独立入口、CA 分发及 Node 对接步骤。已有面板不运行全新安装器、不删除 `.env`、不重建数据库，也不通过重装 Node 迁移地址。

## 主要能力

- 独立管理页、管理员鉴权和作用域 handshake，不覆盖 Admin bundle 或其他插件资源。
- 独立 Nginx HTTPS/REST/WSS 入口；双十年私有 CA/服务器证书、加密导出、同 CA 换钥、双 CA 分阶段轮换及回滚。
- 固定独立 Go Node 版本及资产 SHA-256；新实例准备/安装、已审计标准 systemd 多实例的定向 URL 迁移，保持业务身份、凭据与固定数据目录。
- 入口、Node 迁移及插件恢复状态拥有独立归属检查和卸载流程。

## 验收与限制

实际两台授权虚拟机完成冷启动认证、四绑定代理及数据库计量、原生 Admin 属性更新/恢复、设备断连清理、CA 轮换、定向迁移和逆向回滚。测试资源、合成记录及临时认证已清理，原节点备份已恢复。证据见 `FULL_E2E_TEST_REPORT.md`。

本版本是隔离环境验收版本，不能标作所有联测通过：

- 固定 Go 的 Xray 同 ID UUID 全量更新失败；单用户 UUID 增量更新会因移除唯一用户而停止内核，随后添加失败。未修改 Go/代理内核。
- CLI 模型 UUID 保存未观测到预期 Observer 事件，原因未定位；已通过的 Admin HTTP 属性更新保持 UUID 不变，不能覆盖此缺口。
- 外部 VM 恢复改变初始 PID、节点外来 nft 快照及 9 个原站文件元数据；清理保留原文件字节及恢复后状态，差异已记录。
- ARM64 执行、其他部署布局、公网/IPv6、实际旧 DNS 故障、生产不中断和 Independent overlay 等范围仍需独立验收。空机 Xboard 安装步骤未在本轮重新完整执行。
- V1 不提供 CRL/OCSP、自动撤除旧 CA 或自动迁移 FlowScope。加密 CA 导出不等同于物理离线保管。

部署前阅读 `README.md`、`SECURITY.md` 和 `PRODUCTION_DEPLOYMENT.md`，备份数据库、`.env`、原站配置、插件及 Node 配置/数据。最终附件的精确 SHA-256、成员清单、两平台验包和秘密排除结果以同次发行元数据为准。
