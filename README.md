# NodeEndpoint 1.0.0

为正在运行的 Xboard 增加独立的 IP / 域名节点通讯入口。插件标识 `node_endpoint`，默认 HTTPS TCP 8443，双 10 年私有 CA / 服务器证书策略。面板网页域名、`app_url`、登录、订阅、原 443 站点和其他插件的配置保持原有设置。

当前交付为**隔离环境验收版本**。Windows 与专用 Ubuntu VM 的真实 Nginx、Linux 权限、Root CLI / 独立 PHP-FPM 验证见 [TEST_REPORT.md](TEST_REPORT.md) 和 [VM_TEST_REPORT.md](VM_TEST_REPORT.md)。原生 AMD64 Go Node 在合成面板上完成新装、双内核、WSS、换证、标准多实例迁移与 CLI 回滚的 11 项检查。两 VM Xboard / NodeWorker / Go 实机验收及清理恢复已完成，已测冷启动认证、设备清理、四绑定计量、CA 轮换和选择迁移/回滚通过；固定 Go 的 Xray 全量及单用户增量 UUID 更新失败，CLI UUID save 的 Observer 触发仍有未定位缺口；实际 Admin HTTP 属性更新及恢复通过，不能称全部联测通过。详见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。范围为所列宝塔 PHP-FPM / IPv4 / AMD64 与协议组合，生产环境仍需独立验收。

## 交付物

- `dist/NodeEndpoint-1.0.0.zip`：Xboard 原生上传包，唯一根目录 `NodeEndpoint/`，内含插件、工具和文档。
- `dist/node-endpoint-tools-1.0.0.zip`：独立 SSH 工具包，解压后包含 `tools/panel/`、`tools/node/` 和说明文档。
- `dist/xb-node-endpoint-1.0.0-source.zip`：源码和测试；不包含参考仓库、开发运行器、vendor、凭据或生成的私钥。
- `dist/SHA256SUMS`、`dist/release.json`：版本与 SHA-256。

## 选择安装路线

| 你的情况 | 从哪里开始 |
| --- | --- |
| 空服务器，还没有 Xboard | [从零安装](#从零安装空服务器) → [安装 NodeEndpoint 与独立入口](#安装-nodeendpoint-与独立入口) → [新增 Node](#新增-node-与已有实例迁移) |
| 已有正常运行的官方 Xboard | [从原版面板接入](#从原版面板接入保留现有数据) → 安装插件和入口 → 逐个迁移已有 Node |
| 已经安装旧版 NodeEndpoint | [插件版本升级](#禁用升级与卸载)，不重新安装 Xboard |

NodeEndpoint 是附加插件。已有面板“升级到使用 NodeEndpoint”指安装插件、增加独立通讯入口并迁移节点地址；不替换面板程序或重建数据库。单独升级 Xboard 主程序应使用官方流程，先验证其插件接口兼容性，再安装本插件。

## 从零安装：空服务器

以下主流程使用 **Nginx + PHP-FPM + MySQL/MariaDB + Redis**，与本项目实际联测布局一致。空机安装 Xboard 本身的步骤依据审计版本整理；本轮未重新执行宝塔安装器或整个空机装机过程。Docker、Octane 等部署另见 [官方部署文档](https://github.com/cedar2025/Xboard/tree/4f48e61a2cbc6db5338872b6bdb45ef954ec1256/docs/en/installation) 和 [部署边界](PRODUCTION_DEPLOYMENT.md)，不能直接套用下面的 FPM 后端参数。

### 1. 准备面板服务器

准备 Linux 服务器、面板网页域名及其正常 HTTPS 证书。在宝塔/aaPanel 中创建新站点、独立数据库账号及空数据库，安装 Nginx、PHP 8.2+（本轮面板实测 8.3）、Redis，以及 Git、Composer 2、Python 3.10+、OpenSSL、`unzip`、`ss`、`runuser`。PHP 按 Xboard 的依赖检查启用 PDO/MySQL、Redis、mbstring、XML、cURL、ZIP、fileinfo 等扩展；队列/WS 还需要可用的 pcntl、posix 和进程执行函数。

站点运行用户、CLI PHP 与 FPM 版本必须匹配。下文的 `www`、PHP 路径、域名和网站目录都是示例，使用实际值。通过宝塔官方渠道安装宝塔；不要关闭下载的 TLS 校验。

### 2. 在新建空站点安装 Xboard

确认这是**没有旧面板和业务数据的空目录**；新建站点的占位文件先通过面板管理界面移走。示例站点目录已由宝塔创建并允许 `www` 写入：

```bash
cd /www/wwwroot/panel.example.com

# 固定到本项目审计的官方接口基线；其他版本须重新核对兼容性。
sudo -u www git clone --no-checkout https://github.com/cedar2025/Xboard.git .
sudo -u www git checkout --detach 4f48e61a2cbc6db5338872b6bdb45ef954ec1256
sudo -u www git submodule update --init --recursive

# 使用已安装、已验证来源的 Composer 2，并以站点用户安装依赖。
sudo -u www /www/server/php/83/bin/php /实际路径/composer install \
  --no-dev --prefer-dist --optimize-autoloader --no-interaction
sudo -u www /www/server/php/83/bin/php /实际路径/composer check-platform-reqs --no-dev

# 仅用于全新面板：交互填写新数据库、Redis 和管理员邮箱。
sudo -u www /www/server/php/83/bin/php artisan xboard:install
```

将 `/实际路径/composer` 替换为可信 Composer PHP 可执行文件的绝对路径，可先用 `command -v composer` 核对。安装器生成面板密钥和初始管理员密码；妥善保存输出。不要将示例 `.env` 密钥照搬到其他实例。

已有面板不要运行这一节的 `xboard:install`，也不要删除 `.env`、重新生成 `APP_KEY` 或清空数据库。

### 3. 配好原站点、队列和计划任务

在宝塔设置网站运行目录为 `public/`、PHP 版本与上一步一致，关闭目录浏览，沿用宝塔正确生成的 PHP-FPM 配置，并为 Laravel 配置：

```nginx
location / {
    try_files $uri $uri/ /index.php$is_args$query_string;
}
```

在受保护的 `.env` 中设置实际网页 `APP_URL`，确认数据库和 Redis 正常；仅让站点用户写必要的应用运行目录，按官方部署要求保护 `.env`。用 Supervisor/systemd 以站点用户运行队列，工作目录设为 Xboard 根目录，例如：

```bash
/www/server/php/83/bin/php artisan horizon
```

以同一站点用户每分钟运行计划任务：

```cron
* * * * * /www/server/php/83/bin/php /www/wwwroot/panel.example.com/artisan schedule:run >> /dev/null 2>&1
```

需要 WSS 时，另建一份以站点用户运行的守护任务，工作目录同上，明确绑定 loopback：

```bash
/www/server/php/83/bin/php artisan ws-server start --host=127.0.0.1 --port=8076
```

在原站点已核对的位置加入精确 `/ws` 转发，并测试、重载实际网站 Nginx：

```nginx
location = /ws {
    proxy_pass http://127.0.0.1:8076;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 90s;
    access_log off;
}
```

先确认原网页 HTTPS、管理员登录、订阅、队列和计划任务正常，再继续安装插件。不需要 WS 时可仅配置 REST；插件页选择“新入口仅使用 REST fallback”，不添加 `--enable-ws`。

## 从原版面板接入：保留现有数据

1. 记录现有 Xboard 版本、部署方式、应用根目录、运行用户、PHP 路径、Nginx 网站配置、FPM/HTTP/WS 后端和 Node 服务布局。旧版本没有原生插件管理或缺少已审计 Node API 时，先按官方说明单独升级 Xboard，并在副本上验证；NodeEndpoint 不执行面板主程序升级。
2. 保存可恢复备份：数据库、`.env`（特别是原 `APP_KEY`）、原站配置和证书、`plugins/`、Admin 资源、现有 Node 配置/凭据/元数据/固定数据目录，以及 FlowScope 采集器配置。备份须放在受保护位置，先核对恢复方式。
3. 保留原数据库、账号、机器 Token、节点 ID、用户 UUID、订阅、流量统计和插件记录。不要执行上面的空机安装命令、原版重装脚本或数据库初始化。旧网站域名和现有节点 URL 此时保持原值。
4. 按下一节原生上传、安装并启用 NodeEndpoint。先部署独立入口和 CA，再用一个**新建且未运行的测试身份**验证；真实代理、计量和 WSS 都正常后再安排已有节点迁移。
5. 在维护窗口对已有 Node 执行 `inspect`，核对目标实例 ID、全部绑定和当前 URL，再逐个 `migrate`。迁移会短暂停止对应服务；多实例共享服务时，该服务承载的实例都会经历停启。不要用 `install-new` 或上游重装脚本覆盖现有 Node。
6. 每个实例迁移后分别核对原网站、订阅、其他插件、绑定/Token/数据目录、代理认证、WSS、设备和流量计量；失败时使用该次迁移输出的备份执行 `rollback`。全部验证前保留旧入口和旧证书。FlowScope 的 `panel_url` 单独处理。

完整迁移、限制和回滚命令见 [Node 工具说明](docs/NODE_TOOL.md) 与 [生产部署与恢复](PRODUCTION_DEPLOYMENT.md)。固定 Go 的 Xray UUID 热更新失败是已知上游限制，不能把地址迁移成功等同于 UUID 轮换无中断。

## 安装 NodeEndpoint 与独立入口

### 1. 下载并核对发行包

从 [v1.0.0 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.0) 下载 **NodeEndpoint-1.0.0.zip**、**node-endpoint-tools-1.0.0.zip** 和同一 Release 的 **SHA256SUMS**。GitHub 自动生成的 “Source code” 压缩包不是原生插件上传包。通过可信渠道核对摘要，然后在下载目录检查：

```bash
# 若下载了完整三件套，可使用 sha256sum -c SHA256SUMS；仅下载两包时：
sha256sum --ignore-missing -c SHA256SUMS

# 解压工具到新建的专用目录；不要解压覆盖原 Xboard 目录。
install -d -m 700 /root/node-endpoint-1.0.0
unzip node-endpoint-tools-1.0.0.zip -d /root/node-endpoint-1.0.0
cd /root/node-endpoint-1.0.0
sha256sum -c MANIFEST.sha256
```

工具包在面板服务器和 Node 服务器各准备一份。不要将 `.env`、Token、CA 私钥或服务器私钥放进插件上传包。

### 2. 在原生插件管理中安装

1. 备份现有数据库、`plugins/` 和 Admin 资源；核对实际 Xboard 版本。无需重装面板。
2. 通过可信渠道取得 ZIP 和其 SHA-256，在上传前核对摘要。
3. 在 Xboard 原生插件管理中上传 ZIP、安装、启用 NodeEndpoint。插件仅创建自己的 `node_endpoint_settings` 表，使用原生 `v2_plugins` 记录。
4. 打开当前面板域名下的 `/node-endpoint`，用 Xboard 管理员登录；启用验证码时可使用已有管理员 Bearer Token。会话只保存在页面内存中。
5. 填写纯 IPv4 / IPv6 / DNS 地址及端口，HTTPS 默认开启。HTTP 必须显式接受 Token 与内容明文传输风险。保存设置不会自动修改服务器监听。
6. 通过 SSH 以 Root 在面板服务器执行工具包中的 `tools/panel/` 管理器，先只读检测，再部署入口。具体命令见 [docs/PANEL_TOOL.md](docs/PANEL_TOOL.md)。
7. 从 SSH / 可信离线渠道取得公共 CA 证书和独立 SHA-256 指纹，在管理页核对导入。服务器私钥和 CA 私钥不得上传。
8. 运行页面连接检查，再在目标 Node 上执行独立工具完成 REST 身份与 WSS 认证预检。

管理页是独立安全入口，不修改编译后的 Admin bundle，也不会重写 SmartExpiry / QRCodeExtend 的 patch 或 FlowScope 的动态页面。入口链接见原生插件配置描述。

### 3. 在面板服务器创建独立 HTTPS 入口

以下以宝塔原生 FPM 为例；所有占位符先用 `detect` 和实际网站配置核对。使用一个未被占用且可从 Node 访问的 IP/域名和 TCP 8443，域名方式先正确解析；本机防火墙和云安全组由管理员放行所需来源。

```bash
cd /root/node-endpoint-1.0.0
sudo bash tools/panel/node-endpoint-panel.sh --nginx /www/server/nginx/sbin/nginx detect

sudo bash tools/panel/node-endpoint-panel.sh --nginx /www/server/nginx/sbin/nginx install \
  --host '你的通讯IP或域名' --port 8443 \
  --include-dir /www/server/panel/vhost/nginx \
  --site-config /www/server/panel/vhost/nginx/panel.example.com.conf \
  --site-name panel.example.com \
  --app /www/wwwroot/panel.example.com --app-user www \
  --php /www/server/php/83/bin/php \
  --deployment fpm --backend unix:/实际PHP-FPM套接字 \
  --enable-ws --ws-backend http://127.0.0.1:8076

sudo bash tools/panel/node-endpoint-panel.sh --nginx /www/server/nginx/sbin/nginx status
```

没有已运行且在原站明确配置的 WS 后端时，删去最后两个 WS 参数并在插件页选择 REST fallback。`--include-dir` 必须实际被 Nginx 的 HTTP 层 include 加载；FPM socket 必须是本机正在监听、与原站相同的真实值。未知部署先停止核对，不替换核心配置绕过检测。

安装时确认展示的计划，输入 `INSTALL 地址:8443`，再隐藏输入至少 16 字符的 CA 口令。插件页面的 host/port/HTTPS/WS 模式必须与此配置一致。默认 Root 状态位于 `/var/lib/node-endpoint`；公共证书 bundle 为 `/var/lib/node-endpoint/pki/trust-bundle.pem`，公共状态为 `/var/lib/node-endpoint/public-status.json`。

### 4. 分发 CA，再接入节点

从可信 SSH 渠道取得上述公共证书和 `status` 显示的 CA SHA-256 指纹，在管理页核对导入；指纹须独立确认。只传公共证书，私钥留在受保护的 Root/离线保管位置。

在 Node 服务器将收到的公共证书放到 `/root/received-node-endpoint-ca.pem`，先验证并创建工具命令约定的信任文件：

```bash
cd /root/node-endpoint-1.0.0
sudo python3 tools/node/node_endpoint.py ca-import \
  --ca-file /root/received-node-endpoint-ca.pem \
  --ca-fingerprint '独立确认的64位SHA256指纹' \
  --output /root/node-endpoint-ca.pem
```

新建节点：在原面板创建 node 或 machine 身份，记录 ID 和对应 Token；确认没有其他运行实例使用它，再执行管理页生成的 `prepare`/`install-new`，Token 仅在隐藏提示中输入。原节点：从下一节的 `inspect`/`migrate` 开始，不创建新机器或更换旧 Token。

完成后用真实客户端验证四类结果：原网页和订阅正常、Node REST 身份正常、启用 WS 时持续 WSS 正常、实际代理流量与数据库计量正常。仅看到 HTTPS 200 不代表这些验收都通过。

## Root 管理与证书

Linux 需要 Python 3.10+、OpenSSL 1.1.1+、现有 Nginx / OpenResty、`ss`、可读取的实际应用和路由。工具不自动升级或安装系统软件。它核对运行 Nginx 的实际路径、参数、`nginx -T` include、站点和后端，而非依据测试机路径猜测。未知部署会停止。

Root 工具负责入口安装/修改、状态、备份、最近变更回滚、同 CA 重签、紧急服务器私钥更换、CA 双信任轮换和明确确认卸载。首次签发 CA 与服务器证书精确对齐 10 年截止时间；后续重签的到期时间取签发时 10 年与 CA 到期时间的较早者。状态展示真实 UTC 时间和 180/90/30 天提醒。参阅 [SECURITY.md](SECURITY.md)。

## 新增 Node 与已有实例迁移

管理页在确认身份尚未运行后生成不含 Token 的 `prepare` / `install-new` 指令，兼容 node / machine、sing-box / Xray、AMD64 / ARM64，固定用户维护的独立 Node `v1.13-orphan.4` 及资产 SHA-256。不执行上游未校验安装器，不自动使用 latest，也不将地址变更与二进制升级捆绑。认证 WSS 会注册节点连接并清理设备状态，已有实例必须先备份并停服迁移。

已有实例使用 `inspect` / `migrate`。工具要求确认实例 ID、原 URL、受保护配置/凭据/元数据和已审计 systemd unit；只替换指定实例的 `panel.url`，保持其他绑定、凭据与业务数据。多实例缺少明确固定数据目录、Docker、自定义服务或不受支持 YAML 时安全拒绝。查看 [docs/NODE_TOOL.md](docs/NODE_TOOL.md)，不要对生产多实例运行重装命令。

FlowScope 采集器有独立 `panel_url` 与证书信任；需单独审计迁移，不会跟随本插件自动修改。

## 禁用、升级与卸载

禁用/升级不删除 Root 入口或任何私钥；旧网站和旧节点行为不受作用域 middleware 影响。禁用会停止 NodeEndpoint 对新入口 handshake 的地址重写，因此已迁移节点需要先确认 REST fallback / 原生 WS 地址可用，再禁用。不能仅凭 HTTPS 监听仍在断言 WSS 不受影响。

升级前备份自己的设置与工具状态，上传更高版本的同标识原生包；当前 1.0.0 的重复包会被宿主原生上传器拒绝。常驻 Octane 环境按宿主管理流程 reload 应用 worker，确认路由与资产加载。

原生卸载停止插件 API 与 handshake 修改，保留 `node_endpoint_settings` 和已导入公共 CA 供恢复。宿主迁移回滚器存在全局最后 batch 限制，插件选择一致保留自己的恢复设置。若明确不再需要，先备份，作为应用运行用户执行：

```bash
php tools/plugin/purge.php --app=/已核对的/Xboard根目录 --confirm=PURGE_NODE_ENDPOINT
```

该命令拒绝清理仍启用的插件，仅删除自己的表、自己的迁移 marker 和导入的公共 CA。独立监听卸载需 Root 工具的单独确认和安全检查；不会因原生插件卸载而自动拆除生产 Node 正在使用的入口。

## 开发、构建与参考

`python3 tools/build/build.py` 在 `dist/` 构建可重复 ZIP 并生成摘要；`python3 tools/build/verify.py` 核对三个包的 SHA-256、路径、逐文件清单和私钥排除。每个包内含 `MANIFEST.sha256`，解压后可在对应根目录运行 `sha256sum -c MANIFEST.sha256`。测试命令与环境见 [TEST_REPORT.md](TEST_REPORT.md)。架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，生产部署与故障恢复见 [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)。

全部六个指定仓库的审计 commit、扩展点与共存审计见 [docs/SOURCE_AUDIT.md](docs/SOURCE_AUDIT.md)。Independent 提供的是恢复资料，最新独立 PHP overlay 不在该仓库中；不能据其资料宣称运行 overlay 已通过兼容测试。开发和验收未访问或修改生产服务器。
