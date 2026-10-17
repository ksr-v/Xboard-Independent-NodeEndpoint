# Linux Root 管理工具

工具位于 `tools/panel/node-endpoint-panel.sh`，版本 **1.0.0**。仅由管理员通过 SSH 使用；Web 插件不执行 Shell、不持有 Root 权限。依赖 Linux、Python 3.10+、OpenSSL 1.1.1+、Nginx/OpenResty、`ss`；原生 PHP 部署需要 `php`、`runuser`，Docker 部署需要 Docker CLI。工具不安装依赖、不升级系统、不下载或替换 Node 二进制。

先校验完整发行包的 SHA-256、解压后执行本地文件。不要 `curl | bash`。示例路径必须替换为只读检测确认的真实路径。

```bash
bash tools/panel/node-endpoint-panel.sh detect
bash tools/panel/node-endpoint-panel.sh menu
```

`detect` 不写文件，也不执行应用 PHP。报告选定的唯一 Nginx master、真实主配置、`nginx -T` 加载文件及哈希、HTTP 层 include、监听端口、原生 FPM / HTTP proxy / WS 迹象、Docker 已发布端口和本机防火墙概况。宝塔同时运行控制面板 webserver 与网站 Nginx 时，使用 `--nginx /已核对的网站/nginx` 选择实际二进制；工具验证其 executable/inode 与唯一运行 master，仍拒绝同一二进制的多个 master。未指定二进制而存在多个 master、未知语法、无法解析 include 或无法确认运行二进制时拒绝。云安全组无法本机检测或修改，需管理员单独允许目标 TCP 端口。本机防火墙只读，不自动添加规则。

安装采用单独 `node-endpoint.conf`，只允许已被真实主配置 HTTP 层 include 匹配的目录，不修改原站 server block、原 443 证书、PHP、Redis、数据库或 Xboard 全局配置。默认 HTTPS 8443，拒绝 80/443 和已占用端口。

```bash
# 已确认的原生 PHP-FPM 示例
bash tools/panel/node-endpoint-panel.sh install \
  --host 203.0.113.10 --port 8443 \
  --include-dir /etc/nginx/sites-enabled \
  --site-config /etc/nginx/sites-enabled/xboard.conf --site-name panel.example.com \
  --app /srv/xboard --app-user www-data --php /usr/bin/php \
  --deployment fpm --backend unix:/run/php/php8.2-fpm.sock

# 已确认的 Octane 示例
bash tools/panel/node-endpoint-panel.sh install \
  --host node.example.com --include-dir /etc/nginx/conf.d \
  --site-config /etc/nginx/conf.d/xboard.conf --site-name panel.example.com \
  --app /srv/xboard --app-user www-data \
  --deployment octane --backend http://127.0.0.1:7010 \
  --enable-ws --ws-backend http://127.0.0.1:8076
```

安装展示具体计划并要求输入 `INSTALL <host>:<port>`；CA 口令使用隐藏交互输入，最少 16 字符，不接受参数形式。`modify` 使用同样参数，修改地址时生成新服务器私钥及证书，端口修改沿用有效证书。同一输入重复执行不创建第二个监听。`--confirm` 可传入上述完整确认文字供受控 SSH 自动化使用。

交互菜单修改已有入口时，从原计划载入地址、端口、部署方式、后端、HTTPS 和 WebSocket 默认设置，并分别允许保留或更改。CA 已离线时，重签菜单要求提供离线加密 CA key 路径；菜单还提供导出/验证/删除在线 CA key 和分阶段 CA 轮换。最终计划及确认文字仍会显示。

后端必须精确匹配选定的已加载原站：FPM document root 必须等于指定应用的 `public`，socket 或 TCP 后端必须实际监听；Octane/Docker 必须是一个已观察到的字面 `http://127.0.0.1:port` HTTP 后端。当前上游 TrustProxies 不信任 `::1`，因此后端 `::1`/`localhost` 安全拒绝；这不影响入口自身支持 IPv6。HTTP proxy 安装还必须严格匹配已审计的 TrustProxies 文件 SHA-256 `f259274043cc68d609bb826d5efac4c2f547c734585dbd43462635304a18f5f3`，在计划中记录该 profile；未知 middleware 构建拒绝，不修改原文件。FPM 模式直接设定 HTTPS/FastCGI 参数。安装以应用非 Root 身份执行 `artisan route:list --json`，校验全部审计过的 Node API；缺失、重复、未知路由拒绝。不会只凭目录名猜测环境。

Docker 额外指定 `--container <id> --container-app /srv/app --container-user <nonroot_uid>`，并确认宿主 `--app` 与容器挂载路径严格一致、容器确实发布后端端口。镜像没有可核实的宿主应用挂载、需要远程/动态 upstream、多个负载均衡后端、或容器 Route 表不能以非 Root 身份确认时，V1 安全拒绝安装，需单独审计适配。这是支持边界，不代表所有 Docker / 宝塔布局已验收。

入口只开放精确匹配的 V1 UniProxy `config/user/push/alive/alivelist/status`；V2 `handshake/report/config/user/push/alive/alivelist/status`；V2 machine 仅 `nodes/status`，没有 `machine/config`。HTTP 方法受限，其余路径返回 404。需要 WS 时单独确认实际 `/ws` 后端或已经处理 Upgrade 的 all-in-one 根 proxy；入口精确转发 `/ws`、保留新 Host 和 scheme。Xboard handshake 返回的新 WSS 地址仍需 Web 插件的入口限定能力及实际节点验证，不能仅凭代理配置视为通过。覆盖转发 Host / Proto / Port / For 并清空可伪造的 Forwarded / Prefix / NodeEndpoint 标记。访问日志关闭，避免旧客户端查询 Token 被记录。

HTTP 必须额外提供 `--http --confirm-http TOKEN_AND_CONTENT_SENT_IN_PLAINTEXT`。Token 和通信内容将可能明文传输；HTTPS 失败不会自动降级。

## 双十年证书与离线 CA

CA 与服务器使用 ECDSA P-256 / SHA-256。CA 具有 critical `CA:TRUE,pathlen:0` 与 `keyCertSign,cRLSign`，服务器具有 `CA:FALSE`、`digitalSignature`、`serverAuth` 和正确 IP/DNS SAN。初次证书截止时间精确对齐至生成时刻后十个日历年；生效时间允许五分钟时钟容差，实际起止时间会展示。重新签发以 `min(当前时刻+10年, CA NotAfter)` 为上限，绝不为了凑满十年超出 CA。

默认 Root 状态目录 `/var/lib/node-endpoint` 为 0755，只有公共 `public-status.json` 为 0644 可供 Web 读取。`pki`、`backups` 目录 0700，CA 加密私钥和服务器私钥 0600。私钥不进入网站树、公开资产或数据库，备份不复制 CA 私钥。公钥 CA 文件/双信任 bundle 可经独立可信渠道分发，Node 首次信任必须对照完整 SHA-256 指纹。

`--state` 必须指向专用绝对目录，位于网站/Xboard 应用树之外。首次初始化只接受不存在或为空的目录；已有非空目录必须通过本工具的 ownership/version marker 校验，不能把 `/etc/nginx` 等已有目录直接当作状态目录。归属检查在创建子目录、修改权限和写状态文件之前完成，未通过时保留原目录及内容。公共状态文件只包含证书元数据和检查结果；Web 插件读取固定公共路径或导入该 JSON，不读取 Root 私钥。

```bash
bash tools/panel/node-endpoint-panel.sh status
bash tools/panel/node-endpoint-panel.sh check-expiry
bash tools/panel/node-endpoint-panel.sh export-ca --destination /mnt/offline/node-endpoint-ca
bash tools/panel/node-endpoint-panel.sh verify-export \
  --destination /mnt/offline/node-endpoint-ca --fingerprint <独立保存的CA指纹>
bash tools/panel/node-endpoint-panel.sh remove-online-ca-key \
  --destination /mnt/offline/node-endpoint-ca --fingerprint <独立保存的CA指纹>
```

导出保留加密私钥，清单记录两个文件哈希及 CA 证书指纹。验证完整哈希、CA 指纹和私钥/证书匹配后，才能在再次输入 `REMOVE ONLINE CA KEY <指纹>` 后删除在线副本。工具不能证明介质已物理离线，管理员应卸载/移走并确认离线保存；SSD/COW/快照上删除不能保证擦除历史副本。CA 必须分别备份到可信离线介质。轮换旧 CA 时可使用 `--ca-id` 指定其身份。

```bash
# 同 CA 下主动换新服务器私钥，Node 无需重新信任 CA
bash tools/panel/node-endpoint-panel.sh renew --ca-key /mnt/offline/node-endpoint-ca/ca.key.pem
# 紧急操作使用同样换钥路径，--emergency 明确运维用途
bash tools/panel/node-endpoint-panel.sh renew --emergency --ca-key /mnt/offline/node-endpoint-ca/ca.key.pem
```

**V1 不实施 CRL/OCSP 吊销验证。** 同 CA 换服务器私钥不会使泄露的旧证书自动失效。如果旧私钥泄露，先隔离风险，评估并执行 CA 轮换与旧信任移除；不得把同 CA 换钥当成旧证书已撤销。十年有效期需要持续安全维护。

## 新旧 CA 渐进轮换

```bash
bash tools/panel/node-endpoint-panel.sh rotate-prepare
# 将 pki/trust-bundle.pem 通过可信方式分发给所有 Node，独立核对新旧指纹
bash tools/panel/node-endpoint-panel.sh rotate-activate --fingerprint <新CA指纹>
# 检查所有指定实例 REST、WS 和服务健康，再确认移除旧信任
bash tools/panel/node-endpoint-panel.sh rotate-finish --fingerprint <新CA指纹>
```

prepare 保持旧证书在线，只生成新 CA / 新服务器证书和双信任 bundle。activate 要求输入 `ALL NODES TRUST BOTH CA <新指纹>`，成功测试并 reload 后服务切换新证书。finish 要求输入 `ALL NODES USE NEW CA <新指纹>` 才把 Root 工具分发 bundle 收敛为新 CA；节点端仍需使用迁移工具逐台更新 trust bundle。工具不会自动访问/重配现有节点。每步先备份，可回滚证书状态与配置；旧加密 CA 密钥请按离线流程单独导出和移除。

`check-expiry` 检查 CA/leaf 180、90、30 天窗口并输出状态。到期、尚未生效或进入提醒窗口时退出码 2，其余成功为 0；执行失败为 1。管理员可用 cron 每日运行本地命令并接入自有通知通道，V1 不擅自发送邮件或替管理员配置 cron。

## 备份、失败恢复和卸载

```bash
bash tools/panel/node-endpoint-panel.sh backup
bash tools/panel/node-endpoint-panel.sh rollback
bash tools/panel/node-endpoint-panel.sh uninstall
```

每次变更先校验已有文件归属与完整 SHA-256、执行原始 `nginx -t`，然后备份仅本工具文件/状态，生成独立配置，执行 `nginx -t`、`nginx -T` 验证加载，再安全 reload。HTTPS 本机探测使用受信 CA、正确 SAN 以及实际证书指纹验证，绝不跳过 TLS。REST 未认证可达、真实节点认证成功、真实 WS 成功分别记录；本机探测不能冒充已验证生产节点。

失败恢复配置、PKI 指针、trust bundle 和公共元数据，再测试/安全 reload。无法自动恢复时，在当前备份保留 `RECOVERY_REQUIRED.json`，工具不会清空生产配置。备份目录中的文件名 `nginx_config`、`installation`、`pki_state`、`trust_bundle`、`public_status` 是明确恢复证据；`manifest.json` 包含目标路径及每个文件的哈希。先校验清单，再由管理员本机检查 `nginx -t` 和 reload，必要时从清单恢复原文件；勿覆盖未确认归属的配置。

`rollback` 要求 `ROLLBACK NODE ENDPOINT`。包括卸载后恢复入口；若入口文件被外部修改或卸载后出现同名新文件，则安全拒绝覆盖。历史版本服务器密钥位于 Root 私有版本目录，可用于状态回滚；工具不自动删除密钥历史，不把目录清理与卸载捆绑。

卸载要求 `UNINSTALL NODE ENDPOINT ALL NODES MIGRATED`，只删除归属确认的独立配置并测试/reload，保留原站、原证书及 Root PKI/备份。**禁用/卸载 Web 插件不会自动卸载 Root HTTPS 入口。** 必须先确认所有生产节点不再使用该入口。

## 实际验证边界

Root 状态目录归属标记为 `.node-endpoint-state.json`；路径规范化后保存并校验，拒绝 `..` 和其他目录拷贝的标记。状态目录中的 `protected-apps.json` 保留已核对应用根路径，独立入口卸载后也不会解除 CA 私钥导出到这些应用目录的限制。配置、命令及 include 目录的完整祖先须为 Root 所有且不可被组/其他用户写入。

`tests/panel/test_panel.py` 将真实 OpenSSL/loopback TLS 测试与 Nginx 单元 fixture 分开：证书扩展和期限、错误 CA/过期/错误 SAN 拒绝、同 CA 换钥、离线导出/校验/删除与离线重新签发、双 CA 轮换、180/90/30 提醒，以及 loopback TLS REST/WSS 使用真实密码库和连接；Nginx 故障/幂等/卸载恢复使用明确标记的单元 fixture。Windows 本地实际执行次数及最终结果以 `TEST_REPORT.md` 为准。手工备份、重复执行和失败变更不会覆盖最近真正变更的回滚指针；完整备份校验通过后才开始恢复。

`sudo python3 tests/panel/linux_integration.py` 已在专用 Ubuntu 24.04.3 x86_64 VM 实际执行，6 项 PASS：真实隔离 Nginx、监听冲突、TLS、WSS、禁止路径、配置/重载故障及恢复。脚本依赖已安装的 Nginx、OpenSSL、`ss`，遇到已运行 Nginx 即拒绝执行。另完成 `tests/panel/cli_integration.py` 的 7 项真实 Root CLI / 独立 PHP-FPM 检查，使用真实 Laravel、审计的原版路由表和实际 route:list，验证无 Token 422、TLS、安装/幂等/修改/回滚/卸载及原站保持。已有 Docker、FlowScope 采集器和系统 PHP-FPM 的 PID / start 保持不变；测试进程清理完成。Linux UID/mode/flock 的独立权限套件为 14 项 PASS。

历史证据见 [VM_TEST_REPORT.md](../VM_TEST_REPORT.md)，后续真实宝塔 Nginx/PHP-FPM、完整 Xboard 数据库/队列/Redis/原版 NodeWorker 与 Go 联测和清理恢复见 [FULL_E2E_TEST_REPORT.md](../FULL_E2E_TEST_REPORT.md)。多 Nginx master 选择及引号内大括号解析的产品修复已通过 Windows/Linux 各 36 项回归。Octane/Docker/其他宝塔或 OpenResty 布局、ARM64、公网/IPv6、真实旧 DNS 故障、生产流量连续性及云安全组仍 **NOT TESTED**；Independent `403604e` overlay 未测。本次专属 UFW 规则不证明所有生产防火墙布局。
