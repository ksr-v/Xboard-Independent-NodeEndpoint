# NodeEndpoint

为 Xboard 增加独立的节点通讯入口，让节点通过 **IP + 私有证书**连接面板，也保留使用原面板域名的独立部署命令。默认独立端口为 **HTTPS TCP 8443**。原网站、登录、订阅、`app_url` 和其他插件保留原有配置。

当前插件为 **NodeEndpoint 1.0.8**，新机器命令固定使用独立 **xboard-node v1.13-orphan.5**。配套全插件后端为 **v2026.10.25.1**，修复限速与路由更新；已有面板按[更新教程](docs/UPGRADE.zh-CN.md)先升级后端，再逐台升级 Node。只上传本插件不能修复已运行的后端或 Node。Root 一键入口继续使用 **root-installer-v1.0.9 / tools 1.0.6**，其面板入口配置无需重复升级或重建 CA；本次独立 Node 迁移工具包为 **tools 1.0.7**。

新建 CA 的口令自动随机生成，并使用面板 `APP_KEY` 加密保存；管理员可在 HTTPS 管理页面按需查看和复制。已有配置、CA 和其他插件保留，无需卸载。全新独立 Xboard 用户使用 [内置插件版宝塔教程](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.25.1/docs/installation/aapanel.zh-CN.md)，不必单独上传插件。

## 选择安装路线

| 当前情况 | 操作 |
| --- | --- |
| 空服务器，没有 Xboard | [安装独立全插件版 Xboard](#从零安装空服务器)，默认安装并启用四插件 |
| 已有正常运行的原版 Xboard | [保留数据接入插件](#原版-xboard-面板接入保留现有数据)，不运行面板初始化或重装脚本 |
| 已安装旧版 NodeEndpoint | 上传 [NodeEndpoint-1.0.8.zip](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/download/v1.0.8/NodeEndpoint-1.0.8.zip) 原生升级、启用并刷新；保留设置与 CA |
| 已有面板需要限速、路由修复 | 按[配套更新教程](docs/UPGRADE.zh-CN.md)升级后端和 Node；保留机器身份及当前入口 |
| 插件和独立 HTTPS 入口都已安装 | 防火墙问题可[只补规则](#已有入口只补防火墙或同步-ca)；IPv4“待获取”按[入口修复说明](#私有证书对接后-ipv4-待获取)处理 |
| 为新的节点服务器安装 xboard-node | 在原生机器管理[选择域名或 IP 命令](#新机器接入域名或-ip-双选) |
| 将已有生产节点改为 IP 对接 | 使用[保留配置的迁移工具](#已有节点迁移保留配置)，不要执行新装命令 |

## 从零安装：空服务器

宝塔 / aaPanel + Docker 用户请使用[Docker 一键安装与已有面板接入说明](docs/DOCKER_INSTALL.md)，包含宿主 Root 自动配置、公共 CA 导入和容器重建后的保留信任流程。

新装使用用户维护的 **Xboard v2026.10.25.1 内置全插件版**，包含 NodeEndpoint 1.0.8、SmartExpiry 1.2.0、二维码 1.2.0（审计副本）和 FlowScope 2.4.0，无需再单独上传插件。完整步骤见[宝塔 / aaPanel 教程](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.25.1/docs/installation/aapanel.zh-CN.md)，本节给出主要命令。

### 1. 准备面板环境

准备 Linux 服务器、网页域名及其有效 HTTPS 证书。通过宝塔 / aaPanel 创建空站点、独立数据库账号和空数据库，安装 Nginx、PHP 8.3、Redis、Git，以及 Root 工具需要的 Python 3.10+、OpenSSL、curl、`ss`、`runuser`。按宝塔教程准备并校验固定 Composer 2.10.3 PHAR；PHP 扩展和 CLI `symlink` 按同一教程检查。CLI PHP 与 FPM 版本、站点运行用户应一致。

下文 `www`、PHP 路径和站点目录均为示例，必须换成本机实际值。

### 2. 拉取独立全插件版并配置网站

以 Root 登录 SSH，确认目录中没有已有面板或业务数据，新站点占位文件已移走：

```bash
cd /www/wwwroot/panel.example.com

git clone --branch v2026.10.25.1 --single-branch \
  https://github.com/ksr-v/Xboard-Custom-Source.git .
```

将网站运行目录设为 `public/`，选择 PHP 8.3，配置 Laravel 伪静态 `try_files $uri $uri/ /index.php$is_args$query_string;`，检查配置并启动 / 重载 Nginx 和 FPM。Root 安装器需要发现该站点的真实运行配置；不要拉取原作者子模块或单独以 Root 运行 Composer。

### 3. 交互安装并选择通讯地址

在该新站点目录以 Root 执行，**不传 `--host`，也不填写示例 IP**：

```bash
bash init.sh --app-user www \
  --php /www/server/php/83/bin/php \
  --composer /opt/xboard-tools/composer-2.10.3.phar \
  --port 8443
```

首次配置会让你选择：

```text
1. 自动检测本机公网 IPv4（默认，回车选择）
2. 手工输入 IP / 域名
```

自动模式显示候选地址，回车接受或输入其他实际地址；检测失败时必须手工输入。手工模式不请求公网检测服务。已有真实 Root 入口或显式 `--host` 跳过该菜单。`203.0.113.10` 属于文档示例网段，不能用于实际通讯。

安装器先准备文件权限，再以站点用户完成 Composer、数据库和四插件安装，最后配置独立 HTTPS 入口、本机防火墙和公共 CA。按提示填写数据库、Redis、管理员信息，审阅计划并输入完整 `INSTALL <实际地址>:<端口>` 确认短语。完成后按宝塔教程配置队列与计划任务，并检查网页、插件及节点通讯。已安装的面板不要重跑 `init.sh`、删除 `.env` 或更换 `APP_KEY`；升级使用[已有面板升级说明](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.25.1/docs/installation/bundled-native.zh-CN.md#已有面板升级)。

## 原版 Xboard 面板接入：保留现有数据

这里的“升级”是为现有面板添加插件和独立节点入口。**NodeEndpoint 不替换 Xboard 主程序，也不重建数据库。**

1. 记录现有 Xboard 版本、应用目录、运行用户、PHP 路径、Nginx / FPM / WS 后端和已有 Node 服务布局。
2. 备份数据库、`.env`（包括原 `APP_KEY`）、网站配置与证书、`plugins/`、Admin 资源、Node 配置 / 凭据 / 数据目录，以及 FlowScope 等其他插件配置；确认可恢复。
3. 保留现有账号、机器 Token、节点 ID、用户 UUID、订阅和流量记录。不要运行上一节的全新面板安装命令。
4. 按下一节上传并启用插件，再通过 Root 一键命令部署独立入口。无需先卸载其他插件；现有网站域名和旧节点地址此时保持原值。
5. 先用一个新建、未运行的机器身份验证，再[逐个迁移已有节点](#已有节点迁移保留配置)。

若当前 Xboard 过旧，没有原生插件管理或所需 Node API，应先按 Xboard 自己的升级流程另行处理，并在副本验证兼容性；Root 工具不会代为升级面板。

## 安装或升级 NodeEndpoint 插件

从 [v1.0.8 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.8) 下载 **NodeEndpoint-1.0.8.zip** 和同次 **SHA256SUMS**，核对 ZIP 摘要。GitHub 自动生成的 “Source code” 压缩包不能用于原生插件上传。

1. 在 Xboard 原生插件管理上传 ZIP，安装或升级，确认 NodeEndpoint 已启用。已有 1.0.0 / 1.0.1 / 1.0.2 直接升级，不先卸载；原有设置和公共 CA 保留。
2. 打开面板域名下的 `/node-endpoint`，以 Xboard 管理员登录。
3. 保存通讯 IP / 域名、端口和 HTTPS 设置，推荐使用 **IP + HTTPS + TCP 8443**。这些值必须与随后 Root 安装的入口一致。保存插件设置本身不会创建服务器监听。
4. 有已配置的 WS 后端时启用对应模式；没有 WS 时选择“新入口仅使用 REST fallback”。

原生机器管理中的安装双选通过插件动态资源加载，不修改 Xboard 核心或编译后的 Admin bundle。常驻 Octane 环境按宿主流程 reload 应用 worker；升级后刷新浏览器页面。

已是 **1.0.8** 时，无需重复上传同一个 ZIP。本版将域名和 IP 两种新机器安装命令固定到独立 Node v1.13-orphan.5；Root 命令仍使用支持 Docker 自动配置的 v1.0.9。原设置、公共 CA 和托管口令保留。

### 修改通讯 IP 后复制 Root 命令

保存设置后，在“SSH 配置入口”点击 **一键复制 Root 工具命令**。命令携带本次已保存的 IP / 域名，首次安装和修改已有入口均可使用。在面板服务器以 Root 登录 SSH 后粘贴执行，按向导选择站点和 PHP，核对页面所示端口及 WS 模式；已有入口需确认修改，再输入完整安装确认短语。完成后返回页面点击“验证已保存入口”。

表单仍有未保存修改、保存失败或使用 HTTP 时不会提供可复制的新命令。复制失败可手工选择上方完整命令。页面不会代为执行 Root 操作；普通 HTTPS 改址沿用现有 CA，为新地址更新服务器证书。

## Root 一键安装独立入口

在**面板服务器**以 **Root** 登录交互 SSH，执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash
```

一键入口下载固定版本安装器，校验内置摘要，再下载并校验 **tools 1.0.6** 工具包。它不会安装缺失依赖、升级系统包、升级 Xboard 或替换 Node 二进制。首次下载仍需信任仓库和 GitHub HTTPS；可先下载脚本、独立核对后在本机执行。

标准宝塔 / PHP-FPM 向导会检测实际网站 Nginx、站点、应用目录、FPM / WS 后端和运行用户，多候选时按提示选择。以下自动后续步骤适用于该标准 FPM 向导：

1. 未有 Root 安装记录且未指定 `--host` 时，先选择通讯地址输入方式：`1` 自动检测本机公网 IPv4（默认，回车选择）或 `2` 手工输入 IP / 域名。自动模式显示候选，回车接受或输入其他实际地址；检测失败必须手工输入。手工模式不请求公网检测服务。核对通讯 IP / 端口 / HTTPS / WS 模式，与插件页面保持一致。已有真实 Root 入口地址或显式 `--host` 跳过该菜单，不因公网检测结果变化而自动替换。
2. 审阅安装计划，按提示填写完整的 `INSTALL <实际地址>:<端口>` 并回车，地址和端口以本机计划为准。只输入 `INSTALL` 会重新询问；`CANCEL`、EOF 或 Ctrl+C 取消。这一行是安装器要求的回答，不是 Shell 命令。
3. 新建 CA 自动生成随机口令，并先加密保存到面板，再签发证书，不再要求输入和重复输入 CA 口令。已有 CA 不会改口令或重新签发；旧版手动创建、尚未托管的 CA 在维护时仍使用原隐藏输入流程。
4. 工具创建独立 HTTPS 入口，再处理本机 TCP 防火墙规则，最后尝试将**公共 CA**自动导入面板。
5. 查看每一步结果，刷新插件页面，确认 CA 已导入；从节点服务器验证连接后再接入业务。

标准 Compose + 宝塔反代现在可加 `--docker` 自动识别，以宿主 Root 一次确认后同步设置、防火墙和公共 CA，详见 [Docker 简化安装](docs/DOCKER_INSTALL.md)。仅在无受支持候选时回退到手工菜单；该菜单仍需单独处理后续步骤。

首次根 CA 和服务器证书采用双 10 年策略，后续服务器证书不超过 CA 的实际到期时间。CA 私钥应按[Root 工具说明](docs/PANEL_TOOL.md#双十年证书与离线-ca)加密导出、验证并离线保管。私钥始终保存在网站之外的 Root 私有目录或离线介质；面板仅保存公共证书和加密后的口令。管理员在 HTTPS `/node-endpoint` 的证书区显式查看 / 复制口令；普通设置、节点命令和日志均不含口令。请将数据库与原 `.env` / `APP_KEY` 一同备份，丢失原 APP_KEY 将无法解密已保存的口令。

### 自动防火墙处理范围

工具按安装记录中的 TCP 端口添加**允许所有来源访问**的本机规则，不要求端口一定为 8443。需要限制节点来源 IP 的自定义策略时，使用 `--skip-firewall` 并自行管理规则：

| 本机管理器 | 处理方式 |
| --- | --- |
| 正在运行的 firewalld | 核对默认及活动 zone 的 runtime / permanent 端口规则，补齐并验证持久化放行 |
| UFW | 保存该 TCP 端口的允许规则；若 UFW 未启用，明确显示 inactive，不自动启用整个防火墙 |
| 未支持、冲突或无法确认的策略 | 明确报告 `manual_required`，由管理员按自己的策略处理 |

工具不会操作云安全组、供应商防火墙或路由器。若这些位置有入站限制，仍需允许节点访问该 TCP 端口。使用宝塔时也应核对安全页显示与实际防火墙状态。

防火墙处理失败或需要人工操作时，工具仍会尝试公共 CA 导入，最终列出未完成步骤并返回非零；**已安装的独立入口不会因此自动回滚或重装**。解决对应问题后按下节补做，不要反复生成新 CA。

如已有自定义策略并希望自行管理本机端口，可在一键命令后加 `-s -- --skip-firewall`。该选项仅跳过防火墙步骤，不代表端口已经可达。

### 已有入口：只补防火墙或同步 CA

已安装入口的用户无需重新安装、输入 CA 口令或重新生成证书。在**同一台面板服务器以 Root**执行：

```bash
# 只按已保存的入口端口配置本机防火墙，不重新安装入口或生成证书。
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --configure-firewall

# 只将现有公共 CA 同步到本机插件，不修改防火墙或重新签发证书。
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --sync-ca

# 只读检测部署，不安装入口、不添加防火墙规则、不导入 CA。
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --detect-only
```

`--detect-only` 会准备已校验的下载缓存，但部署检测本身只读。`--sync-ca` 要求插件启用，且插件通讯地址 / 端口 / HTTPS 与 Root 安装记录一致；不一致时先核对并修正插件设置，再重试。

Root 缓存位于 `/opt/nodeendpoint-quick/` 的专用版本目录。入口检查 Root 身份、属主、路径和权限，使用 `umask 077`，临时目录 `0700`、文件 `0600`，无需额外 `chmod +x`；不会对整个 Xboard 目录执行 `chmod` / `chown`。管道启动后的交互从 `/dev/tty` 读取。

### 已安装独立面板：修复误用的示例 IP

`203.0.113.10` 是文档示例地址，不能作为真实节点通讯地址。旧教程若将它写入已安装入口，在**同一台独立 Xboard 面板服务器以 Root**执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --repair-address
```

先选择 `1` 自动检测公网 IPv4 或 `2` 手工输入地址。自动模式核对候选，回车接受或输入实际地址，再按计划确认；手工模式不请求公网检测服务。此命令仅适用于提供 `xboard:configure-endpoint` 的独立后端；它沿用现有 CA 和口令，为新地址重签服务器证书，并以站点用户保存通讯设置、同步公共 CA。只在网页修改 IP 无法修正服务器证书。修复完成后刷新插件页面，再复制新的节点安装命令；不要重跑 `init.sh` 或重建 CA。

## 新机器接入：域名或 IP 双选

刷新面板，在原生 **机器管理 / 服务器管理 → 服务器详情 → 安装 xboard-node**，或新建机器后的 Token 弹窗中选择：

| 选项 | 对接地址 | 证书信任 |
| --- | --- | --- |
| **域名对接（独立部署）**（默认） | 原面板 `app_url` | 默认系统 / 公有 CA 信任，不携带 NodeEndpoint 私有 CA |
| **新版：IP + 私有证书** | NodeEndpoint 保存的独立 IP / 端口 | 自动携带已导入的公共 CA 和指纹，安装时配置该节点服务的信任 |

两种方式均使用你维护的 [独立 Node v1.13-orphan.5 固定安装源](https://github.com/ksr-v/Xboard-Node--Custom-Source/blob/v1.13-orphan.5/install.sh)，Node / xbctl 固定 **v1.13-orphan.5**。域名选项也使用独立部署命令。域名 / IP 的区别是对接地址和证书信任方式。

选择方式后点击 **复制安装命令**，在**新的节点服务器** SSH 终端执行，菜单选择 **1 安装**。机器 ID、Token 和对接地址已带入；IP 方式还带有公共 CA，无需逐机手工上传证书。

IP 入口或 CA 未就绪时，只禁用 IP 选项并显示原因，独立域名命令和新建机器仍可用。修改通讯设置或 CA 后应刷新面板再复制命令，避免使用旧缓存。禁用 NodeEndpoint 插件本身后恢复宿主原生安装命令行为。

两种机器命令均沿用 `--token` 参数，命令和 Shell 历史包含敏感信息，请勿公开。公共 CA 可以分发，私钥不能分发。新版安装器在执行前核对 Node / xbctl 的固定 SHA-256；IP 分支还验证私有 CA、证书链及 HTTPS 主机名，详见 [SECURITY.md](SECURITY.md)。

**新装命令检测到已有安装时会要求输入 `REPLACE`，并替换本机全部旧对接。它不是保留配置的迁移流程。**

### 私有证书对接后 IPv4 待获取

旧版独立 Nginx 入口只开放 Node 的核心通讯接口，遗漏了 FlowScope 使用的精确 `POST /api/v2/flowscope/node-address`。因此节点认证及机器状态可以正常，但 IPv4 上报返回 404。新版 Root 工具修复该路径；不会放开整个插件 API 或管理页面。

在**面板服务器以 Root**执行以下专用修复命令：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --refresh-entry
```

原生 HTTPS FPM / Octane 的 `--refresh-entry` 从安装记录读取站点、PHP、IP、端口和 WS 模式，完整确认后刷新路径规则，保留原 CA、服务器证书、托管口令及节点身份，不写插件设置或防火墙。标准 Docker 使用宿主 Root 自动流程；容器重建后会验证原 CA 再换绑，并补齐通讯设置、防火墙和公共 CA。

也可先原生升级 NodeEndpoint 1.0.8，再通过网页按钮复制同版普通 Root 向导命令，对已有入口选择修改并保留原配置。必须完成入口刷新或修改；只更新插件、只运行 `--sync-ca` 或 `--configure-firewall` 不会补齐 Nginx 路径。改通讯 IP 时仍使用普通向导，并按实际地址更新服务器证书。

确认 FlowScope 2.4.0 已启用，节点使用独立 Node v1.13-orphan.5。Node 对此前 404 有 10 分钟负缓存，修复后等待自动重试再刷新服务器详情；无需重新安装节点、更换 Token 或强制重启。地址是节点直接访问公共 IPv4 检测服务所见的出口；该服务不可达时也会暂时待获取。验证范围和诊断见 [私有入口 IPv4 测试报告](docs/FLOWSCOPE_PRIVATE_ENDPOINT_TEST_REPORT.md)。

## 已有节点迁移：保留配置

已有生产节点使用 [Node 工具](docs/NODE_TOOL.md)的 `inspect` → `migrate`，失败时使用对应备份 `rollback`，不执行上面的新装命令。

1. 在维护窗口核对实例 ID、原 URL、全部绑定、机器 Token、配置和稳定数据目录；确认同一身份没有在其他服务运行。
2. 通过可信 SSH / 离线渠道取得公共 CA 与独立确认的指纹，在 Node 端导入信任。
3. `inspect` 确认受支持的 systemd / 配置布局后，针对指定实例 `migrate`；该流程保留业务身份、其他绑定和数据目录，只改指定入口及必要信任。迁移会短暂停止服务，共享该服务的实例会经历停启。
4. 逐项验证原网站、订阅、REST 身份、持续 WSS、实际代理和数据库计量；失败按该事务备份回滚。全部完成前保留旧入口和旧证书。

认证 WSS 会向 NodeWorker 注册连接并清理设备状态；不要在业务运行时用同一身份另开认证测试抢占连接。未知 YAML、Docker、自定义 unit 或缺少固定数据目录时工具拒绝迁移，不能回退为覆盖重装。FlowScope 的 `panel_url` 和私有 CA 信任需独立处理。

CA 轮换时，面板导入只影响后续复制的新机器命令，不会自动更新已运行节点。按 Root 工具的双 CA 过渡流程为已有节点分发和确认信任。

## 高级操作、卸载与验证边界

需要离线下载或手工部署时，从 [v1.0.8 Release](https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/tag/v1.0.8)取得 **node-endpoint-tools-1.0.7.zip** 和同次 SHA256SUMS，校验后解压到专用受保护目录，再校验包内 `MANIFEST.sha256`。该包的 Node 下载锁为 v1.13-orphan.5；Root 面板引导仍固定其已验证的 tools 1.0.6。不要解压覆盖 Xboard 目录。直接运行 `tools/panel/node-endpoint-panel.sh install / modify` 属于底层手工流程，**不自动配置防火墙**；一键向导的防火墙处理不能套用于这些手工命令。

禁用或卸载插件不会自动移除 Root HTTPS 入口；禁用会停止新入口 handshake 的地址重写，需先确认已迁移节点的 REST / WSS 行为。拆除入口前先迁移全部使用者，再按 Root 工具单独确认卸载；保留所需 PKI 和恢复备份。原生卸载保留插件自己的恢复设置及公共 CA，显式清理另见工具说明。

| 文档 | 内容 |
| --- | --- |
| [Root 工具](docs/PANEL_TOOL.md) | 手工 FPM / Octane / Docker 参数、状态、备份、换证、离线 CA、轮换、入口卸载 |
| [Node 工具](docs/NODE_TOOL.md) | node / machine 新建、已有实例迁移与回滚 |
| [更新教程](docs/UPGRADE.zh-CN.md) | 后端、插件与 Node 配套更新，保留配置、身份和 CA |
| [生产部署与恢复](PRODUCTION_DEPLOYMENT.md) | 上线核对、业务验收和失败恢复 |
| [安全与维护](SECURITY.md) | 首次信任、密钥、Token、权限与信任边界 |
| [变更记录](CHANGELOG.md) / [发行说明](RELEASE_NOTES.md) | 当前与历史版本 |
| [架构](ARCHITECTURE.md) / [源码审计](docs/SOURCE_AUDIT.md) | 扩展点、审计版本、共存与支持范围 |

本项目为**隔离环境验收版本**，不能宣称所有联测通过。历史两 VM 联测已记录冷启动认证、四绑定计量、设备清理、CA 轮换和迁移 / 回滚；固定 Go 的 Xray 全量和单用户增量 UUID 更新存在失败，CLI UUID save 的 Observer 仍有未定位缺口。实际 Admin HTTP 属性更新及恢复通过，不能覆盖上述限制。完整证据见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)；ARM64 执行、其他部署布局、公网 IPv6、真实旧 DNS 故障及生产连续性仍需独立验收。开发和验收未访问或修改生产服务器。

已有插件与安装源验证分别见 [NATIVE_DOMAIN_TEST_REPORT.md](NATIVE_DOMAIN_TEST_REPORT.md)、[NATIVE_INSTALL_CHOICES_TEST_REPORT.md](NATIVE_INSTALL_CHOICES_TEST_REPORT.md)、[NATIVE_MACHINE_TEST_REPORT.md](NATIVE_MACHINE_TEST_REPORT.md)；Root / Linux 权限历史证据见 [TEST_REPORT.md](TEST_REPORT.md) 和 [VM_TEST_REPORT.md](VM_TEST_REPORT.md)。Root 防火墙补丁的实际测试范围以同次 Release 报告为准。

开发者可运行 `python3 tools/build/build.py --plugin-only` 与 `python3 tools/build/verify.py --plugin-only` 构建和核验当前插件，输出在 `dist/v1.0.8/`。Root 工具独立发布，保留已发布的插件与历史包字节。
