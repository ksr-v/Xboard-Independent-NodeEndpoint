# 两台授权虚拟机的实际 Xboard 联测

2026-10-24 的独立 aaPanel/Docker 限速与路由测试另见 [测试报告](../../docs/RATE_ROUTE_TEST_REPORT.md)。`rate_route_*` 四个辅助程序使用新 SQLite 容器及前台 Node，不使用下述历史标准服务实验的数据库或绑定；固定路径、镜像和日期仅适用于本轮专用 VM，不能直接作为生产命令执行。

此目录是验收辅助程序，不是生产安装器。它直接启动已安装 Xboard 的 Laravel、数据库、Redis、队列和原版 NodeWorker，使用摘要固定的实际 Go Node 与 Xray / sing-box 内核；不模拟面板 API 或 WebSocket 服务。

本轮固定面板为 `192.168.128.128`，节点为 `192.168.128.129`，通过用户明确授权的 `codex` 免密 SSH / sudo 操作。程序中的路径、日期、端口和 callback 限制与本轮实验对应；在其他环境必须先重新审查实际部署和归属，不能原样复制运行。执行结果和未测范围见根目录 `FULL_E2E_TEST_REPORT.md`。

- `panel_probe.php`：创建仅测试组可见的合成套餐、用户、机器及四个节点；检查真实计量、设备状态和队列。UUID 轮换默认额外调用原生全量同步；`observer-only` 只保存模型；`delta` 保存后显式调用原版 notifyUserChanged，三种模式分别记录。后者不证明完整 Admin/Observer 触发链。仅在原 token 为空且不存在机器/节点时设置临时随机认证 token，结束后按原始数据库行恢复。
- `plugin_control.php`：通过实际 PluginManager 上传、安装和启用原生 ZIP，逐文件核对；通过真实 HTTP 检查匿名、普通用户和管理员权限。只创建本次临时 Sanctum token，不更改既有管理员密码或记录。
- `observer_probe.php`：只读核对实际 Observer 注册、合成用户可用性、节点组匹配与队列状态；不发送更新事件。
- `admin_node_sync.py`：在实际 Admin HTTP 属性更新和恢复前分别记录私有日志 cursor、PID 和当前 UUID，更新后核验四个绑定的事件与代理效果；HTTP 更新由 `plugin_control.php` 执行，保持 UUID 不变。
- `node_runner.py`：创建带归属标记的标准多实例服务及临时代理客户端，执行实际迁移/回滚、WSS、业务字节与计量检查。所有 token 和 UUID 只走私密文件、环境或 stdin。测试请求及原始日志保留在 Root 私有目录。
- `callback.py`：临时 SSH forced command，只允许六种固定操作（含 Observer 与显式原生服务轮换），限定节点 IP、四个端口、协议、身份和 loopback echo 目标；禁止转发及任意命令。失败子进程输出仅保留在 Root-0700 诊断目录的 0600 文件，不返回原始错误。
- `deploy_node_runner.py`：仅部署 allowlist 中的六个公开源码文件，核对传输前后 SHA-256 并编译，不创建节点、读取凭据或修改服务/网络。
- `test_node_runner.py`：协议客户端、凭据边界、临时认证设置例外和 nftables 策略快照的本地辅助回归；不能计作真实联测验收。

执行前保存数据库一致性备份、原 Nginx 配置、服务 PID/start、既有插件/Admin 字节摘要和业务记录 HMAC。实际宝塔环境用独立 loopback 测试站点连接现有 PHP-FPM 和新建的原版 NodeWorker；原网站 server block 保持原样。Root 产品工具配置独立 HTTPS 8443 入口；测试辅助程序另建一条只允许节点机访问的 UFW 规则。

Node 端自己的 nftables 表仅限制四个测试代理端口，保留已有表和规则。标准迁移严格沿用 stock unit；新装的实验 unit 另加仅允许 loopback / 面板 IP 的 systemd 网络约束。这些是实验隔离策略，不是插件替生产节点自动配置的防火墙。

Node runner 读取 Root-0600 immutable manifest 和独立私密凭据文件。HTTPS CA 尚未创建时，`panel.trust_file` 可暂不存在；后续只允许加载四个 CA 文件/独立指纹字段，不允许改变 URL、回调或实例身份。示例阶段如下，所需路径必须由已审查的准备程序创建：

```bash
sudo python3 tests/e2e/node_runner.py --manifest /private/e2e-manifest.json --action start-rest
sudo python3 tests/e2e/node_runner.py --manifest /private/e2e-manifest.json --action traffic
# 在面板完成真实 Root 入口、NodeWorker 和独立指纹分发之后：
sudo python3 tests/e2e/node_runner.py --manifest /private/e2e-manifest.json --action migrate-node
sudo python3 tests/e2e/node_runner.py --manifest /private/e2e-manifest.json --action ws-check
```

所有阶段必须顺序协调。不要对仍在运行的同一 node/machine 身份进行额外认证探测：原版 NodeWorker 的注册、连接替换及设备清理具有实际副作用。证书切换前先部署双信任并验证；UUID 推送测试要求两种模式均已使用 HTTPS/WSS。

固定 Go 的 Xray 有两项实际 UUID 更新失败：全量替换同 ID UUID 时没有移除旧记录；单用户增量替换先移除唯一用户而停止内核，随后添加及 fallback 报 not running。CLI 模型 UUID save 的 Observer 事件未观测到，原因未定位，注册/用户可用性已核查。另一次真实 Admin HTTP device_limit 更新和精确恢复原值的两个独立窗口均通过：原生 Observer/队列/Redis/WSS 四绑定事件、四路原 UUID 代理、Go PID 保持，无断连或 REST reconciliation。这个属性验收不覆盖 UUID 热更新或设备上限阈值执行。历史失败保留。

原 HTTP 基线保留宿主 access log 策略，本次诊断保持 Root 私有，合成 token 结束后撤销，不改写宿主日志。

最后停止并清理归属确认的 Node/echo/unit/网络资源，等真实队列排空和 NodeWorker 设备状态归零，恢复临时面板设置，再删除合成记录和临时 Sanctum/SSH 凭据；通过原生生命周期卸载并显式 purge 本次插件，单独卸载 Root 入口并恢复原有备份目录。不得清空共享 Redis、整张业务表、既有插件、原网站配置或未知服务。数据库恢复备份及私密故障证据留在 VM Root 目录，不进入发行 ZIP。
