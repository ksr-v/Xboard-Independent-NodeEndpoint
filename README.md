# NodeEndpoint 1.0.2

为正在运行的 Xboard 增加独立的 IP / 域名节点通讯入口。插件标识 `node_endpoint`，默认 HTTPS TCP 8443，双 10 年私有 CA / 服务器证书策略。面板网页域名、`app_url`、登录、订阅、原 443 站点和其他插件的配置保持原有设置。

当前交付为**隔离环境验收版本**。Windows 与专用 Ubuntu VM 的真实 Nginx、Linux 权限、Root CLI / 独立 PHP-FPM 验证见 [TEST_REPORT.md](TEST_REPORT.md) 和 [VM_TEST_REPORT.md](VM_TEST_REPORT.md)。原生 AMD64 Go Node 在合成面板上完成新装、双内核、WSS、换证、标准多实例迁移与 CLI 回滚的 11 项检查。两 VM Xboard / NodeWorker / Go 实机验收及清理恢复已完成，已测冷启动认证、设备清理、四绑定计量、CA 轮换和选择迁移/回滚通过；固定 Go 的 Xray 全量及单用户增量 UUID 更新失败，CLI UUID save 的 Observer 触发仍有未定位缺口；实际 Admin HTTP 属性更新及恢复通过，不能称全部联测通过。详见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。范围为所列宝塔 PHP-FPM / IPv4 / AMD64 与协议组合，生产环境仍需独立验收。

## 新机器安装命令：域名或 IP 双选（1.0.2）

已有 1.0.0 / 1.0.1 用户：备份插件设置，在原生插件管理上传 [NodeEndpoint-1.0.2.zip](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/download/v1.0.2/NodeEndpoint-1.0.2.zip) 升级并确认启用，然后刷新面板页面。无需卸载 NodeEndpoint 或其他插件，无需升级 Xboard 主程序、重装 Root 入口或重新生成 CA。原生升级会保留已保存的通讯 IP、端口、公共 CA 和机器 Token；常驻 worker 按宿主流程 reload。

原生 **机器管理 / 服务器管理 → 服务器详情 → 安装 xboard-node**，以及新建机器后的 Token 弹窗，都提供两个选项：

| 选项 | 对接地址与安装源 | 证书信任 |
| --- | --- | --- |
| **原版：域名对接**（默认） | 完整保留宿主原生命令：原面板 app_url 与 cedar2025 安装源 | 使用原版安装流程，不加入私有 CA |
| **新版：IP + 私有证书** | 使用 NodeEndpoint 保存的独立 IP / 端口与 ksr-v 独立固定安装源 | 自动带入私有 CA 的公共证书及指纹，安装时配置节点服务信任 |

要让新机器通过 IP 接入：

1. 完成一次面板独立 HTTPS 入口安装与公共 CA 同步；在 NodeEndpoint 页面保存通讯 IP / 端口 / HTTPS，并确认公共 CA 已导入。
2. 刷新面板页面，在原生机器管理新建机器，或打开现有机器详情的安装区。
3. 选择 **新版：IP + 私有证书**，核对显示的对接地址，再点 **复制安装命令**。
4. 在新的 Node 服务器 SSH 终端执行命令，菜单选择 **1 安装**。地址、机器 ID、Token 和 CA 已带入，无需重复填写或手工传 CA。

独立入口未启用、尚未导入有效 CA 或配置为 HTTP 时，仅禁用新版 IP 选项并显示原因；原版域名命令与新建机器仍正常可用。更改通讯设置、导入或轮换公共 CA 后，刷新面板页面再复制命令，清除原生 React Query 的旧命令缓存。

新版命令的下载源仍固定为 [Xboard-Independent/nodeendpoint-native-v1.0.0](https://github.com/ksr-v/Xboard-Independent/tree/nodeendpoint-native-v1.0.0/node-installer)，节点二进制仍固定为独立源码仓库的 v1.13-orphan.4；本次 1.0.2 不更新节点安装器或 Go 二进制。后端保留原生 command / install_command 字符串，仅追加 install_options 双选数据。插件通过动态 View composer 加载 extensionless 外链资源，在原生安装区展示选择；不改 Xboard 核心或编译后的 Admin bundle，可与 FlowScope 的动态模板包装共存。

两种原生机器命令都沿用原有 Token 参数方式，属于敏感命令，不要公开粘贴或提交日志。新版命令内的公共 CA 和指纹来自管理员已导入的信任；安装器验证指纹、CA 用途和 HTTPS 主机名，再将 Root-only 信任文件写入节点服务，通过 SSL_CERT_FILE 使用。升级、失败回滚与卸载均处理这份信任。

这条安装流程适用于新机器。检测到旧安装时会要求 REPLACE，替换本机全部旧对接；已有生产节点仍按下文的实例迁移流程处理。CA 轮换仍需为已有节点安排双信任更新，新机器会带入面板当时已导入的 CA。1.0.2 双选验证见 [NATIVE_INSTALL_CHOICES_TEST_REPORT.md](NATIVE_INSTALL_CHOICES_TEST_REPORT.md)，独立节点安装器的原有测试范围见 [NATIVE_MACHINE_TEST_REPORT.md](NATIVE_MACHINE_TEST_REPORT.md)；历史 UUID / Observer 限制继续保留。

## 交付物

- [v1.0.2 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.2)：当前原生插件 NodeEndpoint-1.0.2.zip、SHA256SUMS、版本记录及测试报告，本地构建输出在 dist/v1.0.2/。
- `dist/NodeEndpoint-1.0.0.zip`：原始历史上传包，已发布的 1.0.0 / 1.0.1 文件保留；新装或升级使用 1.0.2。
- `dist/node-endpoint-tools-1.0.0.zip`：独立 SSH 工具包，解压后包含 `tools/panel/`、`tools/node/` 和说明文档。
- `dist/xb-node-endpoint-1.0.0-source.zip`：源码和测试；不包含参考仓库、开发运行器、vendor、凭据或生成的私钥。
- `dist/SHA256SUMS`、`dist/release.json`：版本与 SHA-256。
- 面板 Root 入口使用下方短命令：[root-installer-v1.0.3](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/root-installer-v1.0.3)，内含 tools 1.0.2 的本机 CA 自动导入及 Nginx 解析兼容修复，与插件 1.0.2 兼容。

## Root 一键安装独立入口

**NodeEndpoint 插件已安装并启用后**，在面板服务器以 Root 登录交互 SSH，直接粘贴下面这一行，即可下载 tools 并进入后续安装向导。需要 Python 3.10+、curl、OpenSSL、`ss` 和 `runuser`；不会自动升级系统、Xboard 或 Node。

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.3/install.sh | bash
```

该命令通过 HTTPS 取得固定标签的 `install.sh`；入口下载完整 Python 安装器并核对内置 SHA-256，然后下载、校验 **tools 1.0.2 工具包**的固定摘要和全部成员清单。该工具包以原 v1.0.0 ZIP 为固定摘要基线，保留面板侧 Nginx 解析修复并增加本机公共 CA 导入工具，不要求重新上传面板插件。原 v1.0.0 ZIP 和旧安装器的已发布文件保留。

如果旧命令报 `Malformed Nginx block`，使用上面的最新命令重试。新版修复合法的 `${变量}`、Nginx 转义、词内 `#`、带引号参数及已知 Lua 配置块的误解析；Lua 正文仅作字符串/注释/括号扫描，不执行代码或把其中内容当作 Nginx 指令。仍不支持的语法会显示配置文件、行号和列号，不打印配置正文。新版使用不同摘要的 Root 私有缓存，旧缓存无需手动清理。

入口自动检查 Root 身份及 `/root` 的属主/权限，设置 `umask 077`，临时目录 `0700`、脚本 `0600`，结束后清理临时文件。工具缓存放在 `/opt/nodeendpoint-quick/` 的专用版本目录，只有 Root 可访问；遇到外来文件、符号链接或可被其他用户写入的路径即停止。无需额外 `chmod +x`，不会对 Xboard 整站执行 `chown` 或 `chmod`。使用管道下载时会从 `/dev/tty` 读取后续交互；应用检查仍由原运行用户执行。

标准宝塔/PHP-FPM 布局会自动识别网站 Nginx、Xboard 目录、站点、FPM 后端、include 目录、应用用户及可识别的 WS 后端。你只需：

1. 多站点或多个 PHP 版本时选择正确候选；输入与插件页面一致的通讯 IP/域名和端口（默认 8443）。
2. 确认是否使用检测到的 WS 后端。未配置 WS 时选择插件页面的 REST fallback。
3. 核对管理器展示的计划，输入 `INSTALL 地址:端口`，隐藏输入至少 16 字符的 CA 口令。
4. 安装完成后工具会在本机校验并自动导入公共 CA，刷新插件页面即可查看；按需放行防火墙/云安全组，先验证测试节点，再迁移已有 Node。

### 已安装入口，只自动导入公共 CA

如果已完成 Root 入口安装，但页面仍显示“尚未导入根 CA”，在**同一台面板服务器以 Root** 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.3/install.sh | bash -s -- --sync-ca
```

该操作读取已有 Root 安装记录及可信 CA 公共证书，在本机验证来源和指纹，以安装记录中的非 Root 应用用户复用插件证书校验，原子写入插件自己的 CA 存储。无需管理员 Token、CA 口令或上传文件，不重新安装监听或生成证书。成功后刷新插件页面，手动上传控件仍可供异机/离线场景使用。插件页面的通讯地址、端口和 HTTPS 必须与 Root 安装记录一致，且插件已启用；不匹配会停止，不代改设置。

CA 文件权限为应用用户 `0600`，新建专用目录 `0700`；拒绝符号链接、外来属主和异常写权限，不更改整站属主或开放 Root PKI 目录。私钥和 CA 口令不传给应用。支持同机 FPM/Octane，Docker/异机布局仍使用明确的手动公共 CA 导入。Root CLI 轮换信任后可再次执行 `--sync-ca`；新机器选择新版 IP 命令时会自动带入当前公共 CA，面板导入不会更新正在运行的 Node，已有实例仍需独立分发/确认信任。

新增 8 项 CA 载荷安全测试；在隔离 Laravel/SQLite 的真实 PHP 应用用户下验证了单/双 CA、重复同步、现有原生控制器可见性、0600/0700、私钥不可读，以及错误指纹/禁用插件/地址不一致/符号链接拒绝。未使用生产数据库，原测试面板配置和 Root 入口状态不变。

脚本不读取管理员 Token、不自动迁移已有节点，也不配置防火墙。多站点不会盲选；无法确认的参数会询问。Docker/Octane 或特殊配置转入已有交互菜单，仍需核对真实后端；不承诺自动识别所有布局。已有入口须确认后才进入修改。CA 私钥仍按 [安全说明](SECURITY.md) 导出、验证并离线保管。

Windows/Linux 均通过 36 项面板回归、8 项安装器校验/发现和 10 项 Nginx 语法回归。真实宝塔 Nginx 的隔离 `-T` 配置（含 Lua）复现旧错误、新解析器通过，原面板只读 FPM 发现通过；这些检查未修改原站配置或安装独立入口。短入口另经真实下载、Root/非 Root 校验、0700/0600 权限、管道到 SSH TTY 交互及 Ctrl+C 清理检查。尚未重新执行新增向导的完整交互安装验收；生产此次错误的具体配置指令未取得，重试若仍报错可凭文件/行号继续定位。源码见 `install.sh`、`tools/panel/quick_install.py`，手工部署方式保留在下文。

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

从 [v1.0.2 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.2) 下载 **NodeEndpoint-1.0.2.zip** 和 **SHA256SUMS**，核对摘要后上传原生插件。GitHub 自动生成的 “Source code” 压缩包不是原生插件上传包：

```bash
sha256sum -c SHA256SUMS
```

面板 Root 部署优先使用本文开头的一键命令。需要手工部署时，从 [root-installer-v1.0.3](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/root-installer-v1.0.3) 下载 **node-endpoint-tools-1.0.2.zip** 及该 Release 的 SHA256SUMS，在单独的下载目录核对：

```bash
sha256sum --ignore-missing -c SHA256SUMS
# 解压工具到新建的专用目录；不要解压覆盖原 Xboard 目录。
install -d -m 700 /root/node-endpoint-1.0.2
unzip node-endpoint-tools-1.0.2.zip -d /root/node-endpoint-1.0.2
cd /root/node-endpoint-1.0.2
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
7. 新版 Root 一键安装会同机自动导入公共 CA；已有入口使用上面的 `--sync-ca`。异机或手动流程仍从 SSH / 可信离线渠道取得公共 CA 和独立 SHA-256 指纹，在管理页核对导入。服务器私钥和 CA 私钥不得上传。
8. 运行页面连接检查，再在目标 Node 上执行独立工具完成 REST 身份与 WSS 认证预检。

管理页是独立安全入口。原生机器安装双选由动态 View composer 追加外链资源，不修改编译后的 Admin bundle 或 Xboard 核心，也不会重写 SmartExpiry / QRCodeExtend 的 patch；与 FlowScope 的动态模板包装共存。入口链接见原生插件配置描述。

### 3. 在面板服务器创建独立 HTTPS 入口

以下以宝塔原生 FPM 为例；所有占位符先用 `detect` 和实际网站配置核对。使用一个未被占用且可从 Node 访问的 IP/域名和 TCP 8443，域名方式先正确解析；本机防火墙和云安全组由管理员放行所需来源。

```bash
cd /root/node-endpoint-1.0.2
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

### 4. 接入节点

**新增 machine 机器推荐使用原生机器管理的一键命令，并选择“新版：IP + 私有证书”**，步骤见本文开头；新版命令已带入 IP 与公共 CA，不需要以下手工分发步骤。默认“原版：域名对接”保留原安装流程；传统 node 模式或已有实例的工具迁移仍按下面流程准备 CA。

从可信 SSH 渠道取得上述公共证书和 `status` 显示的 CA SHA-256 指纹，在管理页核对导入；指纹须独立确认。只传公共证书，私钥留在受保护的 Root/离线保管位置。

在 Node 服务器将收到的公共证书放到 `/root/received-node-endpoint-ca.pem`，先验证并创建工具命令约定的信任文件：

```bash
cd /root/node-endpoint-1.0.2
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

升级前备份自己的设置与工具状态；从 1.0.0 / 1.0.1 升级时上传 NodeEndpoint-1.0.2.zip 并确认启用，无需先卸载 NodeEndpoint 或其他插件，也无需升级 Xboard 核心或重做 Root CA。相同版本的重复包会被宿主原生上传器拒绝。刷新面板后，在机器详情安装区或新建后的弹窗选择域名 / IP 方式；更新通讯设置或 CA 后同样刷新，避免原生 React Query 缓存旧命令。常驻 Octane 环境按宿主管理流程 reload 应用 worker，确认路由与资产加载。

原生卸载停止插件 API 与 handshake 修改，保留 `node_endpoint_settings` 和已导入公共 CA 供恢复。宿主迁移回滚器存在全局最后 batch 限制，插件选择一致保留自己的恢复设置。若明确不再需要，先备份，作为应用运行用户执行：

```bash
php tools/plugin/purge.php --app=/已核对的/Xboard根目录 --confirm=PURGE_NODE_ENDPOINT
```

该命令拒绝清理仍启用的插件，仅删除自己的表、自己的迁移 marker 和导入的公共 CA。独立监听卸载需 Root 工具的单独确认和安全检查；不会因原生插件卸载而自动拆除生产 Node 正在使用的入口。

## 开发、构建与参考

`python3 tools/build/build.py --plugin-only` 在 `dist/v1.0.2/` 构建当前原生插件 ZIP 和摘要；`python3 tools/build/verify.py --plugin-only` 核对其 SHA-256、路径、逐文件清单和私钥排除。原始三包构建模式固定用于 1.0.0，后续插件单独构建，保留已发布历史文件。每个包内含 `MANIFEST.sha256`，解压后可在对应根目录运行 `sha256sum -c MANIFEST.sha256`。双选测试见 [NATIVE_INSTALL_CHOICES_TEST_REPORT.md](NATIVE_INSTALL_CHOICES_TEST_REPORT.md)，既有工具测试见 [TEST_REPORT.md](TEST_REPORT.md)。架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，生产部署与故障恢复见 [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)。

全部六个指定仓库的审计 commit、扩展点与共存审计见 [docs/SOURCE_AUDIT.md](docs/SOURCE_AUDIT.md)。Independent 提供的是恢复资料，最新独立 PHP overlay 不在该仓库中；不能据其资料宣称运行 overlay 已通过兼容测试。开发和验收未访问或修改生产服务器。
