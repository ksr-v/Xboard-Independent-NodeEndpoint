# 已有面板与节点更新教程

2026-10-30 再次补充：[同地址入口刷新失败修复](ENTRY_REFRESH_CA_FIX_20261030.md)使用 `root-installer-v1.0.12 / tools 1.0.13 / web-manager 1.0.2`。同地址刷新复用现有证书，仅读取托管记录校验应用绑定，不再获取 CA 口令或恢复待处理凭据。此前助手已升级到 1.0.1、但入口事务回滚的服务器，也可重新执行该文的 `--upgrade-entry` 命令，沿用地址、证书、CA、助手授权及启用状态。此前发布资产和下列历史说明保留。

2026-10-30 补充：[设备限额账号连接失败的入口修复](DEVICE_ADMISSION_ENTRY_FIX_20261030.md)已独立发布为 `root-installer-v1.0.11 / tools 1.0.12 / web-manager 1.0.1`。已安装独立入口的原生面板使用其中的 `--upgrade-entry` 命令，补齐设备准入路由并升级已安装助手；不需要重复升级 Node 或重建 CA。下文的 v9 配套版本与历史升级流程保留原发布信息。

本次配套版本为独立 Xboard **v2026.10.30.2**、NodeEndpoint **1.1.2**、独立 xboard-node **v1.13-orphan.9**。本版目标为 ACME 域名大小写一致性与已确认的旧 nft null 空表 / NAT 能力兼容修复，实际验证范围以本轮报告为准。保留 v8 INPUT、在线 IP 与设备限制行为，需要更新实际运行的 Node。插件 1.1.2 负责让以后复制的域名 / IP 安装命令使用新 Node，不能替已运行的节点更新二进制。

已有 NodeEndpoint 入口、IP、端口、CA、托管口令、机器 Token 与用户 UUID 均沿用当前配置。无需卸载其他插件，也无需重建证书。Root 面板入口继续使用 `root-installer-v1.0.10 / tools 1.0.6`；以 Root 执行面板归档升级且不加 `--skip-endpoint` 时，会为已有有效原生入口安装或更新受限助手，授权范围与全新初始化相同。只有仅上传插件、手动源码升级或显式跳过而助手仍未安装时，才需单独执行固定 Root 安装器的 `--enable-web-manager`。独立下载的 Node 迁移工具包更新为 tools 1.0.11，固定下载 Node v1.13-orphan.9。

按“备份 → 面板 → 一台 Node 验证 → 其余 Node”的顺序，在维护窗口操作。Node 二进制升级会重启服务并中断本机现有连接；升级完成后，单独调整限速可在原连接上应用。以后修改路由会主动关闭本节点旧流，让重连使用新规则。

## 1. 备份现有数据

在面板服务器备份数据库、原 `.env`（含原 `APP_KEY`）、源码、`plugins/`、`storage/`、Admin 资源及 NodeEndpoint Root 状态 / CA 恢复材料。数据库备份应能恢复，存放在 Root 专用目录，普通文件权限设为 `0600`。不能只备份 `.env` 而遗漏数据库中的加密 CA 口令。

在每台 Node 备份 `/etc/xboard-node/`、当前 Node / xbctl 二进制及实际 systemd unit；保留权限和其中的私有证书信任。自定义 unit、Docker、多实例布局或非标准安装路径先按本机实际布局备份和制定替换步骤，不套用下方标准安装命令。

## 2. 更新面板

### 宝塔 / aaPanel 原生 PHP 部署

从 [Xboard v2026.10.30.2 Release](https://github.com/ksr-v/Xboard-Custom-Source/releases/tag/v2026.10.30.2) 下载 `xboard.tar.gz` 和 `SHA256SUMS`，核对发行页面的摘要并执行 `sha256sum --check --ignore-missing SHA256SUMS`。将归档放在 Root 私有目录，使用归档中的保留数据升级器。以下路径、用户和数据库备份名必须替换为实际值：

```bash
install -d -m 700 /root/xboard-v2026.10.30.2-upgrade
tar -xzf /root/xboard.tar.gz -C /root/xboard-v2026.10.30.2-upgrade

python3 -I /root/xboard-v2026.10.30.2-upgrade/scripts/deploy-release.py \
  --archive /root/xboard.tar.gz --sha256 '已核对的64位SHA256' \
  --app /www/wwwroot/你的面板目录 --app-user www \
  --php /www/server/php/83/bin/php \
  --composer /opt/xboard-tools/composer-2.10.3.phar \
  --database-backup /root/你的数据库备份.sql
```

升级器保留 `.env`、`storage/`、现有配置和数据库，校验后同步随包插件。已有同版本插件的启停状态保留；未知的本地修改会停止并要求处理。升级完成后，按实际部署方式重载 PHP / Octane、队列与 NodeWorker 常驻进程，让它们加载新后端代码，再刷新浏览器。仅更新磁盘文件不能证明旧常驻进程已经加载修复。

不要运行 `init.sh`、删除 `.env`、重新生成 `APP_KEY` 或用新面板初始化覆盖旧数据库。完整流程、失败恢复和代码已部署时的 `update.sh --deployed-code` 用法见[后端保留数据升级说明](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.30.2/docs/installation/bundled-native.zh-CN.md#已有面板升级)。

### 宝塔 / aaPanel + Docker 部署

更新到固定 v2026.10.30.2 源码并重新构建本地镜像，沿用原 Compose 文件、项目名、`.env`、数据库与所有运行卷，再替换原服务。不要创建第二套项目，也不要执行 `docker compose down -v`。`docker-install.sh` 是新装入口，不能用于不同源码版本之间的更新；仅 `docker compose pull` 也不会更新本地构建的源码。

容器替换后，在宿主机 Root SSH 运行已有入口的 `--docker` 向导，审阅新容器绑定，用原 CA 私钥验证并沿用现有 CA。具体参数、持久卷及恢复方法见[固定版本 Docker 教程](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.30.2/docs/installation/aapanel-docker.zh-CN.md)。

### 只需单独升级 NodeEndpoint 插件

全插件后端已包含 NodeEndpoint 1.1.2，不必重复上传。使用其他兼容后端时，从 [NodeEndpoint v1.1.2 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.1.2) 下载 `NodeEndpoint-1.1.2.zip`，校验同次 SHA256SUMS 后在原生插件管理执行升级。保留原插件记录、设置和 CA，勿先卸载。刷新浏览器；Octane 等常驻应用按宿主方式重载。完整设备限制修复仍需要相应后端改动和下方 Node 更新。

## 3. 逐台升级已有 Node

以下命令在**节点服务器**以 Root 执行，适用于标准 `/etc/xboard-node` 与 `xboard-node.service` 安装。它使用固定的独立仓库，先核对安装器摘要，再执行 `upgrade`：

```bash
install -d -m 700 /root/xboard-node-v1.13-orphan.9-upgrade
cd /root/xboard-node-v1.13-orphan.9-upgrade
curl -fsSLO https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.9/install.sh
curl -fsSLO https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.9/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS && \
  bash install.sh upgrade --version v1.13-orphan.9
```

首次下载需要信任仓库和 GitHub HTTPS，可另从可信渠道核对 SHA-256。升级保留当前配置及绑定，不要求重新输入 Token、IP 或 CA；安装器备份当前状态、核对固定二进制摘要后重启服务，健康检查失败按其恢复流程处理。出现自定义 / 不完整安装拒绝时先检查本机布局，不改用新装覆盖。标准升级为受管服务启用 `XBOARD_NODE_MANAGED_INPUT=1`；已有 `0` 或本地 `firewall.managed_input: false` 保留关闭语义。可显式使用安装器 `--skip-firewall` 关闭自动 INPUT，具体规则归属与支持范围见[操作说明](HY2_INPUT_FIREWALL.zh-CN.md)。

**不要把面板“复制安装命令”用于升级已有 Node。** 那是新机安装流程，输入 `REPLACE` 会替换本机旧对接。本节使用明确的 `upgrade`，不需要输入 `REPLACE`。

检查版本和服务：

```bash
/usr/local/bin/xboard-node -v
systemctl is-active xboard-node.service
```

版本应包含 `v1.13-orphan.9`，服务应为 `active`。回到面板确认机器重新在线、状态与流量继续上报，并用真实客户端验证。第一台通过后再处理其余节点；不要一次替换全部生产节点。

## 4. 验证限速与路由

- 保存用户限速后，在同一连接连续传输足够数据，确认总速率接近设置；取消限速后确认恢复。同一用户的上传、下载和并发连接共享限速桶。短下载可能消耗突发额度，不能用瞬时峰值判断限速是否失效。
- 保存路由 / 出站后，确认已有旧流断开，新连接命中新出口或阻断规则。规则按面板选择顺序下发；自定义结构化规则的多个匹配字段沿用 OR 语义。
- 确认 WebSocket / NodeWorker 正常。在线控制连接可及时收到提交后的更新；未建立 WebSocket 时仍取决于 HTTP 轮询周期，离线节点要先重连。面板保存成功不等同于每台节点已经确认应用。

专用 VM 的最终有效 80 项 TCP 联测、Linux 并发检查、13 个后端场景及测量边界见[修复验证报告](RATE_ROUTE_FIX_REPORT.md)。公网延迟、所有协议 / UDP 限速和长时间压力仍需按实际业务验证。若升级失败，保留安装器输出和恢复备份，按对应后端 / Node 恢复流程处理；不要通过重建 CA、改 Token 或清空数据库尝试修复。

## 5. 使用新版管理菜单和 Hysteria2 自动转发

首次使用本页固定v9的`upgrade`，同时更新Node、xbctl和本地脚本；成功后输入小写 `node`。选择1在线更新到本仓库最新正式Release，选择2重启，选择4修改面板地址／Token，选择5查看最近100行日志，选择6查看状态。Token隐藏输入、空项保留；先认证再保存，保留实例身份、其他绑定、数据目录和私有CA，失败尝试回退。自定义systemd drop-in、复杂YAML或standalone配置拒绝自动编辑，需人工按实际布局处理。

后续可直接输入 `node` 选择1，不再手写下载命令。更新器核对固定仓库、同一tag、完整下载及SHA256；网络或校验失败停止，不退回执行dev脚本。详细边界见[菜单说明与测试](NODE_MANAGEMENT_TEST_REPORT.md)。

Hysteria节点选择版本2，连接端口填写如 `42450-42453`，服务监听端口 `42443`，跳跃间隔 `5` 秒。新版Node根据面板自动管理UDP转发，无需手写NAT；范围变更或关闭自动更新自身规则。节点需有 nftables、对应 iptables 工具和 NET_ADMIN 权限。标准安装器启用后，Node 自动为本实例实际 UDP 监听口维护 INPUT，含单端口 HY2；启动、配置变更和每 30 秒核对恢复。仅处理 iptables-nft / legacy 的 IPv4、IPv6，不保存全局规则或改提供商持久文件，额外原生 nftables INPUT / firewalld 需人工放行，云安全组仍需允许广告 UDP 范围。UFW 兼容布局与 persistent 重载后恢复有窗口。不要重新运行面板TCP8443的Root入口向导替代UDP放行。更新范围可能中断当前代理连接，客户端重连使用新配置。详见[本次 INPUT 操作说明](HY2_INPUT_FIREWALL.zh-CN.md)；[历史 NAT 报告](HYSTERIA2_AUTO_FORWARDING_REPORT.md)保留其原测试范围，不作为本次重跑。

## 6. 验证在线 IP 和设备限制

对已设设备限制的测试用户，先保持一个来源 IP 的连接，再从另一个 IP 尝试同节点和跨节点连接，确认总数不超过限制；同一 IP 的多个连接应共用一项。正常断开后检查下一次完整在线快照；客户端网络完全中断时等待 TCP 保活确认。不要用单纯“长时间没有业务流量”判定空闲连接离线。

面板不可达时，启用新租约能力的 Node 会停止放行新的限额连接，并在最后一次成功续约约55秒后关闭仍活跃的限额用户连接。面板恢复后再验证重连与在线状态。各项隔离 VM 实测时间和适用范围见[联动发布说明](DEVICE_LIMIT_RELEASE_20261028.md)。
