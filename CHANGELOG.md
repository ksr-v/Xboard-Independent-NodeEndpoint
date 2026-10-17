# 变更记录

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
