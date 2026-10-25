# 已有面板与节点更新教程

本次配套版本为独立 Xboard **v2026.10.25.2**、NodeEndpoint **1.0.9**、独立 xboard-node **v1.13-orphan.6**。本版包含限速／路由修复、Hysteria2 自动 UDP 范围转发和 node 管理菜单；自动转发需要后端和 Node 配套更新。插件 1.0.9 负责让以后复制的域名 / IP 安装命令使用新 Node，不能替已运行的节点更新二进制。

已有 NodeEndpoint 入口、IP、端口、CA、托管口令、机器 Token 与用户 UUID 均沿用当前配置。无需卸载其他插件，也无需重建证书。Root 面板入口继续使用 `root-installer-v1.0.9 / tools 1.0.6`；本次不需要为限速和路由修复重新运行它。独立下载的 Node 迁移工具包更新为 tools 1.0.8，固定下载 Node v1.13-orphan.6。

按“备份 → 面板 → 一台 Node 验证 → 其余 Node”的顺序，在维护窗口操作。Node 二进制升级会重启服务并中断本机现有连接；升级完成后，单独调整限速可在原连接上应用。以后修改路由会主动关闭本节点旧流，让重连使用新规则。

## 1. 备份现有数据

在面板服务器备份数据库、原 `.env`（含原 `APP_KEY`）、源码、`plugins/`、`storage/`、Admin 资源及 NodeEndpoint Root 状态 / CA 恢复材料。数据库备份应能恢复，存放在 Root 专用目录，普通文件权限设为 `0600`。不能只备份 `.env` 而遗漏数据库中的加密 CA 口令。

在每台 Node 备份 `/etc/xboard-node/`、当前 Node / xbctl 二进制及实际 systemd unit；保留权限和其中的私有证书信任。自定义 unit、Docker、多实例布局或非标准安装路径先按本机实际布局备份和制定替换步骤，不套用下方标准安装命令。

## 2. 更新面板

### 宝塔 / aaPanel 原生 PHP 部署

从 [Xboard v2026.10.25.2 Release](https://github.com/ksr-v/Xboard-Custom-Source/releases/tag/v2026.10.25.2) 下载 `xboard.tar.gz` 和 `SHA256SUMS`，核对发行页面的摘要并执行 `sha256sum --check --ignore-missing SHA256SUMS`。将归档放在 Root 私有目录，使用归档中的保留数据升级器。以下路径、用户和数据库备份名必须替换为实际值：

```bash
install -d -m 700 /root/xboard-v2026.10.25.2-upgrade
tar -xzf /root/xboard.tar.gz -C /root/xboard-v2026.10.25.2-upgrade

python3 -I /root/xboard-v2026.10.25.2-upgrade/scripts/deploy-release.py \
  --archive /root/xboard.tar.gz --sha256 '已核对的64位SHA256' \
  --app /www/wwwroot/你的面板目录 --app-user www \
  --php /www/server/php/83/bin/php \
  --composer /opt/xboard-tools/composer-2.10.3.phar \
  --database-backup /root/你的数据库备份.sql
```

升级器保留 `.env`、`storage/`、现有配置和数据库，校验后同步随包插件。已有同版本插件的启停状态保留；未知的本地修改会停止并要求处理。升级完成后，按实际部署方式重载 PHP / Octane、队列与 NodeWorker 常驻进程，让它们加载新后端代码，再刷新浏览器。仅更新磁盘文件不能证明旧常驻进程已经加载修复。

不要运行 `init.sh`、删除 `.env`、重新生成 `APP_KEY` 或用新面板初始化覆盖旧数据库。完整流程、失败恢复和代码已部署时的 `update.sh --deployed-code` 用法见[后端保留数据升级说明](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.25.2/docs/installation/bundled-native.zh-CN.md#已有面板升级)。

### 宝塔 / aaPanel + Docker 部署

更新到固定 v2026.10.25.2 源码并重新构建本地镜像，沿用原 Compose 文件、项目名、`.env`、数据库与所有运行卷，再替换原服务。不要创建第二套项目，也不要执行 `docker compose down -v`。`docker-install.sh` 是新装入口，不能用于不同源码版本之间的更新；仅 `docker compose pull` 也不会更新本地构建的源码。

容器替换后，在宿主机 Root SSH 运行已有入口的 `--docker` 向导，审阅新容器绑定，用原 CA 私钥验证并沿用现有 CA。具体参数、持久卷及恢复方法见[固定版本 Docker 教程](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.25.2/docs/installation/aapanel-docker.zh-CN.md)。

### 只需单独升级 NodeEndpoint 插件

全插件后端已包含 NodeEndpoint 1.0.9，不必重复上传。使用其他兼容后端时，从 [NodeEndpoint v1.0.9 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.9) 下载 `NodeEndpoint-1.0.9.zip`，校验同次 SHA256SUMS 后在原生插件管理执行升级。保留原插件记录、设置和 CA，勿先卸载。刷新浏览器；Octane 等常驻应用按宿主方式重载。完整限速 / 路由修复仍需要相应后端改动和下方 Node 更新。

## 3. 逐台升级已有 Node

以下命令在**节点服务器**以 Root 执行，适用于标准 `/etc/xboard-node` 与 `xboard-node.service` 安装。它使用固定的私有部署仓库，先核对安装器摘要，再执行 `upgrade`：

```bash
install -d -m 700 /root/xboard-node-v1.13-orphan.6-upgrade
cd /root/xboard-node-v1.13-orphan.6-upgrade
curl -fsSLO https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.6/install.sh
curl -fsSLO https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.6/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS && \
  bash install.sh upgrade --version v1.13-orphan.6
```

首次下载需要信任仓库和 GitHub HTTPS，可另从可信渠道核对 SHA-256。升级保留当前配置及绑定，不要求重新输入 Token、IP 或 CA；安装器备份当前状态、核对固定二进制摘要后重启服务，健康检查失败按其恢复流程处理。出现自定义 / 不完整安装拒绝时先检查本机布局，不改用新装覆盖。

**不要把面板“复制安装命令”用于升级已有 Node。** 那是新机安装流程，输入 `REPLACE` 会替换本机旧对接。本节使用明确的 `upgrade`，不需要输入 `REPLACE`。

检查版本和服务：

```bash
/usr/local/bin/xboard-node -v
systemctl is-active xboard-node.service
```

版本应包含 `v1.13-orphan.6`，服务应为 `active`。回到面板确认机器重新在线、状态与流量继续上报，并用真实客户端验证。第一台通过后再处理其余节点；不要一次替换全部生产节点。

## 4. 验证限速与路由

- 保存用户限速后，在同一连接连续传输足够数据，确认总速率接近设置；取消限速后确认恢复。同一用户的上传、下载和并发连接共享限速桶。短下载可能消耗突发额度，不能用瞬时峰值判断限速是否失效。
- 保存路由 / 出站后，确认已有旧流断开，新连接命中新出口或阻断规则。规则按面板选择顺序下发；自定义结构化规则的多个匹配字段沿用 OR 语义。
- 确认 WebSocket / NodeWorker 正常。在线控制连接可及时收到提交后的更新；未建立 WebSocket 时仍取决于 HTTP 轮询周期，离线节点要先重连。面板保存成功不等同于每台节点已经确认应用。

专用 VM 的最终有效 80 项 TCP 联测、Linux 并发检查、13 个后端场景及测量边界见[修复验证报告](RATE_ROUTE_FIX_REPORT.md)。公网延迟、所有协议 / UDP 限速和长时间压力仍需按实际业务验证。若升级失败，保留安装器输出和恢复备份，按对应后端 / Node 恢复流程处理；不要通过重建 CA、改 Token 或清空数据库尝试修复。

## 5. 使用新版管理菜单和 Hysteria2 自动转发

首次使用本页固定v6的`upgrade`，同时更新Node、xbctl和本地脚本；成功后输入小写 `node`。选择1在线更新到本仓库最新正式Release，选择2重启，选择4修改面板地址／Token，选择5查看最近100行日志，选择6查看状态。Token隐藏输入、空项保留；先认证再保存，保留实例身份、其他绑定、数据目录和私有CA，失败尝试回退。自定义systemd drop-in、复杂YAML或standalone配置拒绝自动编辑，需人工按实际布局处理。

后续可直接输入 `node` 选择1，不再手写下载命令。更新器核对固定仓库、同一tag、完整下载及SHA256；网络或校验失败停止，不退回执行dev脚本。详细边界见[菜单说明与测试](NODE_MANAGEMENT_TEST_REPORT.md)。

Hysteria节点选择版本2，连接端口填写如 `42450-42453`，服务监听端口 `42443`，跳跃间隔 `5` 秒。新版Node根据面板自动管理UDP转发，无需手写NAT；范围变更或关闭自动更新自身规则。节点需有nftables和NET_ADMIN权限，主机／云安全组仍需允许对应UDP范围和监听口。不要重新运行面板TCP8443的Root入口向导替代UDP放行。更新范围可能中断当前代理连接，客户端重连使用新配置。详见[自动转发报告](HYSTERIA2_AUTO_FORWARDING_REPORT.md)。
