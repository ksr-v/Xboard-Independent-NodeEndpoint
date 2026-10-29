# 管理员登录与网页 Root 管理

NodeEndpoint 1.1.0 在同域 `/node-endpoint` 复用面板管理员登录。页面读取面板现有的登录记录，经原生管理员 API 验证后使用；退出登录、登录过期或切换账号会清理当前页面的认证和敏感内容。页面不把凭据放入 URL，也不另存一份 Token。仍支持手动登录。

## 安装

全新安装 [Xboard v2026.10.29.2](https://github.com/ksr-v/Xboard-Custom-Source/releases/tag/v2026.10.29.2) 的原生 PHP-FPM 路线时，面板初始化会一并安装并启用五个内置插件，创建独立 HTTPS 入口，安装受限 Root 助手，并同步公共 CA。核对真实通讯地址、端口和权限说明后按安装器提示确认。`--skip-endpoint` 会延期入口和助手初始化。

已有面板先升级插件；已有独立 HTTPS 入口时，在同一台面板服务器以 Root 执行一次：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.10/install.sh | bash -s -- --enable-web-manager
```

此命令只为已安装入口启用网页管理，不重跑 MODIFY、不重建 CA。完整 SSH 向导仍可用于首次创建入口。本版助手不支持 Docker 容器部署，相关部署继续使用 SSH 工具。

固定版本为 `root-installer-v1.0.10`、已校验的 `tools 1.0.6` 和 `web-manager 1.0.0`。旧 tag 和资产保持原样。更新已有面板保留设置、CA、应用密钥和管理员禁用插件的选择；更新不会自动给旧面板增加 Root 授权。

## 网页操作

助手连接成功后，先在插件页保存通讯设置，再预览任务、核对目标和步骤、输入页面显示的完整确认文本后执行。页面显示异步任务以及入口、公共 CA、端口规则各阶段结果；入口成功而后续同步失败会显示部分完成，可检查后补做。

网页只提供这些固定任务：

- 按已保存的 HTTPS 通讯设置配置入口，修正服务器证书 SAN。
- 刷新已绑定站点的通讯路由。
- 使用现有托管 CA 续签服务器证书。
- 确保 Root 策略已经批准的通讯 TCP 端口规则存在并持久保存。
- 同步当前公共 CA 和公共 Root 状态。

本机防火墙规则允许所有来源访问该通讯端口。助手不配置云安全组。不能确认本机规则时会报告需要人工处理，不会把它显示为公网验证成功。Root 安装时只授权当前通讯端口；新增端口需要 SSH 修改策略。网站 80/443 不在助手授权范围内。

更换 CA、导出 CA 私钥、导入私钥、处理旧版未托管 CA 口令、改变应用目录/运行用户/后端绑定，以及系统服务或任意命令操作仍通过 SSH 完成。新版 CA 口令随机生成并由面板 APP_KEY 加密保存，已有未托管 CA 仍使用原口令。

## 权限与故障排查

助手没有 HTTP 监听。面板通过固定 Unix socket 连接，Root 通过内核提供的进程 UID 核对调用者，并校验固定工具摘要、Root 策略、站点绑定、计划有效期和执行前状态。网页不能传入 Shell、程序路径或任意任务。任务所有权保存在数据库；清空缓存后仍可恢复本人已提交的任务。

运行用户是安全边界。若多个站点共用宝塔 `www` UID，它们共享这个系统身份的信任。需要站点隔离时，应使用专用系统用户，并使 PHP-FPM、队列和面板运行用户一致后，再从 SSH 安装助手。

可在 SSH 只读检查助手：

```bash
systemctl status nodeendpoint-web-manager.service --no-pager
python3 -I /opt/nodeendpoint-web-manager/1.0.0/web_manager.py status-policy
```

停用网页 Root 能力可执行 `systemctl disable --now nodeendpoint-web-manager.service`，随后刷新插件页。独立入口仍由 Nginx 提供服务。再次启用前核对绑定和策略。不要把 Root 私有备份、CA 私钥、口令或面板 Token 发到公开日志。

本机 TLS 与无凭据 REST 检查不代表节点认证和 WSS 已通过；节点端仍需分别验证。普通 PHP-FPM 部署使用 REST fallback，不会凭空启动 WebSocket 后端。
