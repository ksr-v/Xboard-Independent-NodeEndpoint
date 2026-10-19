# 私有入口 FlowScope IPv4 上报修复

日期：2026-10-19。目标版本：NodeEndpoint 1.0.6、root-installer-v1.0.8 / tools 1.0.5、内置后端 v2026.10.19.2。

## 状态

DISCOVERED：真实独立 Node 源码 `internal/panel/address.go` 固定使用 `POST /api/v2/flowscope/node-address` 上报出口 IPv4；旧 Root 工具独立 Nginx 白名单没有该路径。机器状态使用另一个接口，因此核心对接正常不代表 IPv4 报告成功。

DISCOVERED：Node 对 404 结果保留 10 分钟负缓存，后续正常通讯成功驱动自动重试。IP.SB 探测失败 5 分钟后重试。探测不带面板凭据，使用正常 TLS 校验；节点服务信任文件合并系统根与私有公共 CA。

VERIFIED：Windows PHP / Laravel / Sanctum / SQLite 原生组件 36 项、Root 命令生成与鉴权 9 项通过。当前版本为 1.0.6，生成命令固定 root-installer-v1.0.8，旧版插件升级保留设置、公共 CA、机器凭据及启用状态。

VERIFIED：Headless Chrome 的 Root 命令复制 9 项、CA 口令 UI 7 项通过；使用真实插件 HTML / JavaScript / CSS 和真实剪贴板，HTTPS API 使用隔离 mock。覆盖保存后权威配置、未保存修改、保存失败、并发编辑、移动端和鉴权失效；没有连接生产面板。

VERIFIED：Root 工具 Windows 154 项通过，包含 5 项可选精确路径审计和 7 项已有入口刷新检查。确认无 FlowScope 的原版路由表可继续安装；存在的额外接口必须匹配 POST、实际 Controller 和启用状态中间件，未知路径、异常方法及重复路由均拒绝。

VERIFIED：专用虚拟机的真实 Nginx / PHP-FPM / SQLite / 私有 CA 链路复现旧入口 404。执行刷新后，同一 HTTPS 请求返回 200，IPv4 已持久化，原网站管理员地址查询返回 `source=node`、`stale=false`；错误 Token 为 401、GET 为 405、禁用插件后为 404，其余采集和管理路径继续返回 404。

VERIFIED：刷新前后 CA PEM、加密私钥、服务器证书及私钥、托管凭据 ID / 密文 / Root 映射、APP_KEY、插件设置和原信任文件保持；重复刷新返回 `changed:false`。测试使用与 Node 相同 JSON 协议的合成机器报文，不运行代理业务，也不调用外部 IP 检测服务。固定发行包的逐文件校验及 CLI 复核结果另见 Release 附件 `VERIFICATION.json`。

已执行命令：

```text
php -c .runtime/php.ini tests/integration/run.php
php -c .runtime/php.ini tests/integration/root_command.php
node tests/ui/root-command.cjs
node tests/ui/ca-passphrase.cjs
```

## 已安装入口修复

在面板服务器以 Root 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.8/install.sh | bash -s -- --refresh-entry
```

`--refresh-entry` 校验已有安装归属，从记录读取原地址、端口、站点、PHP 和 WS 模式，核对计划后输入完整确认短语。支持原生 HTTPS FPM / Octane，不自动刷新 Docker。此操作保留 CA、服务器证书和托管口令，不写插件设置或防火墙；不更换节点 Token，不需要重装或强制重启 Node。可以先上传 NodeEndpoint 1.0.6 以复制新版普通 Root 向导命令，再选择同配置修改，但仅上传插件不足以更新服务器入口。只运行 `--sync-ca` 或 `--configure-firewall` 也不修改路径白名单。

确保 FlowScope 2.4.0 已启用，Node 使用独立 v1.13-orphan.4。完成后等待最多 10 分钟的旧 404 负缓存，刷新服务器详情。此地址代表公共检测服务看到的节点出口，不保证等于节点监听地址。

## 无凭据最小诊断

以下前两条在节点服务器运行，不展示配置或 Token：

```bash
systemctl is-active xboard-node
curl -4 -fsS --connect-timeout 3 --max-time 5 https://api-ipv4.ip.sb/ip
```

首条应为 active；第二条应返回真实公网 IPv4。检测失败时先检查该节点的 DNS、IPv4 出站与系统公共 CA。

独立入口的无凭据 POST 应到达应用鉴权并拒绝，而不是由旧入口统一返回 404。诊断时仅输出 HTTP 状态码，不粘贴已安装命令、配置、Token 或日志全文；实际地址和已导入公共 CA 路径由管理员在本机核对。

## 未验证边界

NOT RUN：生产机器、真实代理客户端连续性、ARM64 实机、全部宝塔 / OpenResty / Docker / Octane 布局。未修改 Node 二进制、协议、机器身份或 FlowScope 流量采集逻辑。
