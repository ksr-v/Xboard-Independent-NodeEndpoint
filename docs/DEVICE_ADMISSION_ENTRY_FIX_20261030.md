# 设备限额账号连接失败：独立入口修复

本次发布为 **root-installer-v1.0.11 / tools 1.0.12 / web-manager 1.0.1**，用于已安装 NodeEndpoint 独立 HTTPS 入口的面板服务器。修复不需要替换 Node 二进制或更改用户 UUID。

## 问题与修复

启用设备限额的账号（包括填写 `999`）需要先向面板的 `POST /api/v2/server/device/acquire` 请求准入。旧入口只暴露了原有通讯 API，遗漏 `device/acquire` 和 `device/release`；握手仍宣告支持设备准入，因此请求可能在到达面板鉴权前返回 404，Node 拒绝该连接。空值或 `0` 会跳过准入，因而可以表现为不限设备的账号正常、有设备限额的账号全部连接失败。

新工具重新审计 Laravel 的实际路由表。两条准入路由必须同时存在、只允许 POST、指向匹配的原生控制器方法，并包含 `ServerV2` 鉴权中间件，才生成对应的精确 Nginx location。未知路径继续返回 404。没有这两条路由的旧后端仍可使用原有通讯入口。

旧网页助手固定加载旧工具包，后续维护可能再次生成缺少准入路由的配置。因此本次一并发布匹配的新助手。升级流程先处理已安装的助手，再刷新入口；未安装助手时不新增网页授权。

## 已有原生面板一键升级

在 **Xboard 面板服务器**以 Root 打开交互式 SSH 终端，执行：

```bash
nodeendpoint_script=$(curl -fsS --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 60 --max-filesize 65536 https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.11/install.sh) && [ -n "$nodeendpoint_script" ] && bash -c "$nodeendpoint_script" -- --upgrade-entry
```

引导会验证固定安装器、工具包及助手摘要。按提示审阅计划，并输入要求的完整确认文本。升级沿用已记录的面板站点、入口 IP/端口、证书及 CA；已有助手的站点绑定、批准端口和启用选择按升级检查处理。发现不兼容或不完整的旧状态时会停止，并显示需处理的项目。

安装器及工具包文件更新后，必须完成入口刷新；仅下载文件不会更改正在使用的 Nginx 配置。刷新会保存已有配置备份，执行 Nginx 校验后重载。Docker 或特殊手动布局应使用对应已审阅的维护流程，不能把原生助手升级套用于容器。

## 升级后验证

在节点服务器复用已安装的 CA 信任，执行无凭据的 GET 检查。将示例地址换成实际面板通讯入口：

```bash
for p in machine/status device/acquire device/release; do
  curl --cacert /etc/xboard-node/node-endpoint-trust.pem \
    --connect-timeout 5 --max-time 8 -sS -o /dev/null \
    -w "$p HTTP %{http_code}\n" "https://YOUR_PANEL_IP:8443/api/v2/server/$p"
done
```

三项应返回 `405`，表示精确路径已识别、GET 被拒绝；这项检查不申请设备租约。`000` 表示连接或 TLS 失败，不能据此判断路由。随后恢复目标账号原来的设备限额，断开并重连客户端，以实际代理请求确认准入与数据转发。

## 验证边界

发布说明和同版验证记录分别列出路由审计、FPM/代理配置生成、升级状态保留、真实 Nginx HTTP 转发以及公开资产下载结果。Nginx 测试后端返回认证错误只证明请求已到达后端，不等于真实账号、Redis 租约或 Node 代理流量已经验证。用户生产服务器的最终结果以升级后现场测试为准。
