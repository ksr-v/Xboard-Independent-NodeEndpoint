# 生产部署、回滚与故障恢复

本次开发没有访问、修改或部署生产服务器。专用 Ubuntu 24.04 VM 已完成真实 Nginx、Linux 权限、Root CLI / 独立 PHP-FPM 和原生组件验证，详情见 [VM_TEST_REPORT.md](VM_TEST_REPORT.md)。生产上线前仍需在目标部署的隔离副本上完成完整 Xboard / NodeWorker / Go 联测及业务验收，再分批迁移。

后续两 VM 实机联测已完成，已测冷启动认证及 Admin HTTP 属性更新通过并确认上游 Xray 全量及单用户增量 UUID 更新限制：真实宝塔 Nginx/PHP-FPM、现有 Xboard/Laravel/MariaDB/Redis/队列/原版 NodeWorker 与固定 Go 的四绑定认证、计量、Admin HTTP 属性更新、设备清理、CA 过渡、迁移/回滚和新装，见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。这些结果不覆盖生产部署。Octane/Docker/其他 OpenResty 或宝塔布局、ARM64、公网/IPv6、真实旧 DNS 故障及生产流量不中断仍需独立验收；Independent `403604e` overlay 仍未测试。

## 安装前核对

保留面板数据库、原网站 Nginx 配置与证书、`plugins/`、Admin bundle、Node 的配置/凭据/实例元数据/稳定数据目录和 FlowScope 采集器配置的受保护备份。记录各组件实际版本、摘要、应用运行用户与服务状态。不同机器的路径、IP、部署方式不能沿用开发机假设。

从可信渠道取得 1.0.0 ZIP 和摘要，先核对 SHA-256 再解压。工具不自动下载并执行 Shell，不升级 Nginx、PHP 或系统软件。以 Root 通过 SSH 执行检测：

```bash
bash tools/panel/node-endpoint-panel.sh detect
```

检测展示运行 Nginx/OpenResty、主配置和加载文件、HTTP include、真实监听、Docker 端口与本机防火墙的证据。不主动打开防火墙；云安全组由云管理端明确放行独立端口。没有唯一 Nginx master、可证明 HTTP include、准确站点/后端或完整 Node 路由时停止，不改核心配置绕过检查。

## 部署独立入口

先原生安装并启用插件、保存相同 host/port/HTTPS/WS 模式。Root 菜单可交互填写；CLI 示例为已核对 Octane / loopback HTTP 后端的参数模板，所有路径和服务地址都必须来自本机检测：

```bash
bash tools/panel/node-endpoint-panel.sh install \
  --host 你的IP或域名 --port 8443 \
  --include-dir /实际HTTP层include目录 \
  --site-config /实际已加载网站配置 --site-name 原网站server_name \
  --app /实际Xboard根目录 --app-user 实际应用用户 \
  --deployment octane --backend http://127.0.0.1:实际后端端口
```

若原站 `/ws` 后端已被识别且监听正常，加 `--enable-ws --ws-backend http://127.0.0.1:实际WS端口`。FPM 模式使用 `--deployment fpm` 与原站 `fastcgi_pass` 完全相同的已监听 socket / loopback 地址；Docker 需要明确 container、container-app/user 与实际 bind mount，详见 PANEL_TOOL。不明确的 Docker/custom proxy 配置安全拒绝。V1 Root 保留 80/443，独立入口使用其他空闲端口。

安装会展示具体 plan，要求键入目标地址确认，再隐藏输入 CA 加密密码。先快照，再签发、写自己的 `node-endpoint.conf`、验证实际 include、测试配置、reload 并校验实际服务证书与无凭据 API。原网站 server block 与原证书不写入。任何失败都会恢复原状态或保留 RECOVERY_REQUIRED 证据；不要因失败清空生产配置。

无 WS 入口时，管理页选择“新入口仅使用 REST fallback”，不能将 REST 正常当作 WSS 成功。保留原全局 `server_ws_url`，不要为了新 IP 改全局设置。

## CA 保管与验证

Root `status` 查看公共 CA / server 指纹、SAN、真实起止时间与状态，将公共 CA 从可信 SSH 渠道复制到管理员和 Node。公共 `public-status.json` 可以导入页面；不要导出私钥到网站、静态目录或数据库。

使用 Root `export-ca` / `verify-export` 导出并验证加密 CA 私钥和 manifest，独立确认公共 CA 指纹，再执行 `remove-online-ca-key`。离线介质应独立保管；同机其他目录仍属于在线副本。Root 状态保留证书和不可变历史私钥目录用于恢复，审计并离线保管每个 CA 的密钥。后续重签提供离线加密 `--ca-key` 路径，密码仅在终端输入。

## 新增与分批迁移

先新增一个测试 Node：从已验证工具包目录运行管理页的 `prepare` 命令，隐藏输入 Token，验证 CA / REST / WSS；确认报告与架构后执行 `install-new`。核对二进制确实为用户维护的固定发行，未落回官方渠道。确认实际协议端口、内核、在线用户、流量和服务健康。

已有 Node 不重装。用 `inspect` 确认当前实例 ID 与原 URL，确认该身份没有在其他服务运行，保存配置/凭据/元数据和服务原状态。多实例确认已有显式固定数据目录，再执行 `migrate`，要求 `MIGRATE:指定实例ID`，先 CA / TLS / REST 预检；备份并停止目标服务后才做认证 WSS，再只改该实例地址并启动。官方 WS 认证可能替换业务连接和清理设备状态，启动后不能用另一测试连接抢占业务 WS。失败恢复原文件、信任和服务状态。未知 YAML、Docker、自定义 unit / overrides 应使用独立经验证适配方案，不能回退重装。

迁移前后逐项核对指定实例及其他实例绑定、机器 Token、用户信息、订阅、历史流量、数据目录；不重建机器身份。Node 的本地 AutoInstanceID 包含 URL，工具同步相应安装元数据而保留实际稳定目录与业务身份。旧域名无法解析的 IP 通讯需在隔离环境独立验证。FlowScope 采集器的 `panel_url` 与私有 CA 信任由单独流程处理。

## 维护、轮换和回滚

定期运行 `check-expiry` 并接入自己的监控：180/90/30 天或异常状态会返回检查结果与非零退出码。未配置外部监控不等于自动通知已启用。服务器密钥事件使用 `renew --emergency`（同 CA 新 key）；CA 事件按 `rotate-prepare` → 每个 Node 分发双信任并独立核对指纹 → `rotate-activate` → 全部 Node 复核 → `rotate-finish` 进行。明确旧 CA 未实现强制吊销，按风险决定轮换。

Root `backup` 保留自己的入口配置和 PKI 状态指针。`rollback` 恢复最近变更并验证配置/reload；失败保留救援快照。若出现 RECOVERY_REQUIRED，先停止新的自动操作，按私有 manifest 核对摘要恢复自己的文件，再运行当前 Nginx `-t` / reload。不可直接恢复整份原站 bundle、删除其他 server block 或重建数据库。

Node `rollback` 校验受保护快照及当前应用摘要，只恢复该事务的文件并恢复服务原态。若后续人工改动或服务布局变更，工具拒绝覆盖，需基于备份人工核对恢复。

## 卸载顺序

先迁移所有已使用独立入口的 Node / 采集器，分别验证 REST 与 WSS；记录确认清单。原生禁用/卸载只停止插件处理，Root 监听与 PKI 仍保留，但新入口 WSS 重写亦停止，需提前验证 fallback。

Root `uninstall` 要求键入 `UNINSTALL NODE ENDPOINT ALL NODES MIGRATED`，检查所有权并备份后只移除自己配置、测试/reload；不能自动证明所有远端 Node 已迁移，管理员的明确确认必须基于逐节点记录。保留原站和证书、Root PKI 与恢复记录。

自己的恢复表与导入公共 CA 可在确认无需恢复后通过 `tools/plugin/purge.php` 清理。不要删除共享 Admin patch lock、其他插件资源、FlowScope 采集数据或既有生产证书。
