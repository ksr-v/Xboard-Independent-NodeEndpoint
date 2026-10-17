# NodeEndpoint v1.0.2 发布说明

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
