# NodeEndpoint V1.0 源码审计

审计日期：2026-10-09（Asia/Shanghai）。下列 GitHub 仓库只读克隆到 `.audit/`，未修改参考源码、未推送，也未访问任何生产服务器。版本结论来自实际文件与当前浅克隆 HEAD；不将其他项目文档中的历史验收当作本插件测试。

## 实际来源与版本

| 项目 | 分支 / 版本 | 当前 commit | 核对范围 |
| --- | --- | --- | --- |
| [官方 Xboard](https://github.com/cedar2025/Xboard) | master；基于 commit 标识版本 | `4f48e61a2cbc6db5338872b6bdb45ef954ec1256`（2026-08-30） | PluginManager、AbstractPlugin、HTTP Kernel、路由、Admin / ServerV2 middleware、MachineController、ServerController、NodeWorker、配置服务 |
| [Xboard-Independent](https://github.com/ksr-v/Xboard-Independent) | main；恢复资料与安装器 | `85c10eed412cb300b2ee280486c14c38009ae341`（2026-10-09） | 部署/版本记录、保留 Admin dist、node-installer |
| [独立 Node](https://github.com/ksr-v/Xboard-Node--Custom-Source) | dev / `v1.13-orphan.4` | `8dbee14fb2f32a4f9324929fc24828cea6e29b38` | 独立 Node 工具审计：配置、安装器、HTTP / WS 客户端、TLS、实例标识与发布资产 |
| [SmartExpiry](https://github.com/ksr-v/xb-SmartExpiry) | main / 1.2.0 | `6f22f456512d1a0cdcad9c46e75e06e5793e6bb6`（2026-10-07） | 生命周期与精确 Admin bundle patch |
| [QRCodeExtend](https://github.com/ksr-v/xb-qrcode-extend) | main / 1.2.0 | `3b30042a0e16047a67dddb73ffaf85e4a2a54918`（2026-10-07） | 生命周期、资源、patch 所有权与共享锁 |
| [FlowScope](https://github.com/ksr-v/xb-flowscope) | main / 2.4.0 | `9ae54a38e695d3b6acc429bbcdfbafba35ccd4d6`（2026-10-09） | 动态 Admin view composer、路由鉴权、采集器 panel_url 与资产兼容性 |

官方 `composer.json` 要求 PHP `^8.2`、Laravel `^12.0`、Sanctum `^4.0`、Octane `2.11.*`。Xboard 本身没有可依赖的稳定 `1.x` 产品 semver；`UpdateService` 使用 commit / 日期生成展示版本。PluginManager 的 `require.xboard` 兼容性检查只是未实现的占位，插件必须自行检查真实接口，不能把 metadata 当作兼容性保证。

Independent 的 `Xboard/` 目录只含部署资料和安装文档，不含最新 PHP Controller。它记录官方基线为上述 `4f48e61a…`，独立面板部署 overlay 为 `403604e`。因此基线接口已核对，**403604e 实际 PHP 源码 / 生产运行状态 NOT TESTED**。保留 Admin dist 的 `assets/index-CEIYH7i8.js` SHA-256 实际为 `0cfb12e1ec9afee9e439744671de2c14cb1af1b3118c291f0a87348492948666`，与 FlowScope 审计基线一致。不得因此推断所有未来独立面板构建都兼容。

## 原生插件接口与安全边界

`node_endpoint` 对应 `plugins/NodeEndpoint/`、`Plugin\\NodeEndpoint\\Plugin`。Composer 已配置 `Plugin\\` PSR-4 根目录 `plugins/`。包内使用 `Plugin.php`、`config.json`、`routes/api.php`、`routes/web.php`、`resources/views`、`resources/assets`、`database/migrations` 和可选 `Providers/PluginServiceProvider.php`。`config.json` 必须包含 name/code/version/description/author，code 为小写数字下划线，version 为三段数字，type 可为 feature。

全局 `InitializePlugins` 在路由 dispatch 前调用请求作用域 PluginManager。每个请求加载启用插件的 provider、routes、views、commands 并调用 boot。**插件 routes/api.php 只获得 api middleware，不自动添加 /api 前缀**。管理 API 必须显式加 `admin` middleware。官方 Admin middleware 使用 `Auth::guard('sanctum')->user()` 并要求 `is_admin`，路径保密或静态页面本身不提供鉴权。原生 web group 的 session 与 CSRF middleware 是注释状态，独立页面应使用显式 Bearer token API 鉴权。

生命周期实际为：install 运行自有 migrations、创建默认禁用数据库记录、调用 install、发布自有 assets；enable 调用 boot；disable 调用 cleanup；update 先 disable/cleanup，运行 migrations/update，再 enable/boot；uninstall 先 disable/cleanup，再尝试回滚自有 migration 并删除自有插件记录。**cleanup 同时服务禁用和升级，不能删除仍被 Node 使用的独立入口、证书、Root CA 或生产配置**。原生 uninstall 不会自动删除 published assets；插件自己的资源可由明确所有权规则移除，API/动态资源必须每次检查启用状态，避免常驻 worker 的旧路由仍生效。

真实隔离生命周期测试发现：宿主 `runMigrationsRollback` 使用 Laravel `migrator->rollback([$pluginPath])`，但 Laravel 先选全局最后 migration batch。若之后安装了其他有 migration 的插件（实测 FlowScope），NodeEndpoint 的更早 batch 不在回滚选择中，自有表不会删除。不能修改共享 batch 或在 cleanup 删表掩盖宿主限制。V1 自有 migration 的 down 明确保留恢复设置，保证安装顺序不同也一致；需要清理时由独立本地 `tools/plugin/purge.php` 在已禁用/卸载且输入精确确认后仅移除自有恢复表、迁移标记及导入的公共 CA。Root 入口/PKI不受影响。

原生 PluginConfigService 仅按 config.json 已知键过滤并覆盖数据库 JSON，没有业务校验。因此 HTTP 风险确认、地址格式与端口冲突校验应在插件自有 setting 表/API 中执行，不依赖通用配置保存；无需更改 app_url、server_ws_url、订阅、登录、用户或机器数据。

`HookManager` 存在 action/filter/intercept，但此次目标 handshake、机器安装命令和 Admin menu 没有对应原生 filter。不得注册不存在的 hook 后宣称功能有效。上传器直接 ZipArchive::extractTo，再删除旧插件目录和 copyDirectory，缺少包签名和原子升级保证；NodeEndpoint 交付独立根目录 ZIP、SHA-256 和先备份升级说明，不能把宿主上传器当成完整供应链验证。

## REST 与 WebSocket

官方 `app/Http/Routes/V2/ServerRoute.php` 定义：

- `GET|POST /api/v2/server/handshake`（server.v2），`POST /api/v2/server/report`（server.v2）。
- `GET /api/v2/server/config`、`user`、`alivelist`，`POST push`、`alive`、`status`（server.v2）。
- `POST /api/v2/server/machine/nodes`、`status`（Controller 自行验证 machine_id/token）。
- V1 `server/UniProxy` 保留 config/user/push/alive/alivelist/status，仍用旧 `server` middleware；老协议仅在明确支持时开放。

`ServerV2` 在 machine_id 存在时验证机器 Token/is_active，并校验请求 node_id 归属；node 模式验证全局 server_token。handshake 允许缺省 node_id，其他 node API 要求对应 node。`MachineController` 本身返回机器节点列表与 pull/push intervals，未改变节点绑定。插件不能创建/重置这些身份来迁移 URL。

`V2/Server/ServerController::handshake` 仅在 `server_ws_enable` 为真且 `Cache::has(NodeWorker::HEARTBEAT_CACHE_KEY)` 时返回 enabled=true。该 cache key 为 `ws_server:heartbeat`，WS worker 每 10 秒续期，TTL 30 秒。若 `server_ws_url` 非空，响应固定采用它；否则通过当前请求的 isSecure/getHttpHost 生成 `/ws`。没有插件 hook。`V2/Admin/Server/MachineController::buildInstallCommand` 从 app_url 生成 panel URL，并直接生成官方 dev 安装脚本 `curl | bash` + argv Token 指令；NodeEndpoint 必须提供自己的受控安装/迁移命令，不能直接复用或修改这个 Controller。

可在插件 boot 对实际 router 调用 `pushMiddlewareToGroup('api', 独立响应 middleware)`，先检查实际 middleware group 去重。全局 InitializePlugins 位于 route dispatch 前，当前请求可用。middleware 仅在**官方准确 route action、handshake URI、成功 JSON 响应、插件启用、独立入口 host/port 与配置匹配**时修改返回 `websocket.ws_url`；保留官方 enabled 判断与所有其他字段。每次读取当前插件状态 / setting，不能用静态首次注册标志或跨请求保存请求地址。禁用后常驻 router 即使仍有 middleware 也必须立即无作用。不写全局 server_ws_url，不改 Controller 文件。直接给常驻 Route 追加 middleware 需注意其 computedMiddleware 缓存；api group 解析方式更适合启用前已处理过 handshake 的 worker。

入口标记 header 不是认证。Root 代理必须覆盖来自客户端的同名 header；地址匹配仍要校验 host / port。官方 TrustProxies 信任 RFC1918 / loopback 和列出的 CDN 网段，接受 X-Forwarded-Host/Port/Proto，因此 Root 代理须准确设置这些头；勿用被 app_url forceRootUrl 改写的 URL generator 推导节点地址。

Workerman `/ws` 是独立服务，node/machine Token 当前协议在 query string 中，并以 `auth.success` 确认身份。HTTPS SAN、CA 和 HTTP handshake 成功均不能证明 WSS 完成；需独立 TLS 验证、101 Upgrade、认证事件及机器/节点身份验证。后端 NodeWorker 无 heartbeat 或 Root 未发现 WS 后端时，必须明确 REST fallback，不能返回假的 WSS ready。Root Nginx 应限制允许的 REST 路由及 `/ws`，禁用包含 Token 的 access log，不代理 Admin/用户页面。

## 现有插件共存

SmartExpiry / QRCodeExtend 共享 `storage/framework/xboard-admin-patch.lock`，对上述 Admin bundle 做私有精确 marker 变换与逐字节撤销。它们 boot/install/update 会重建变换，cleanup 只删除自己片段；不能恢复整文件，也不能删除共享锁。NodeEndpoint 使用独立页面和动态链接时无需写 bundle、manifest、这个锁或它们的资源目录。

FlowScope 2.4.0 在 `composing: admin` 上注册可识别 class listener，通过实际 dispatcher raw listeners 去重；view composer 捕获 original view path/data，以自己的 wrapper render original，再往完整 HTML 的 `</body>` 前插入 loader。它不写核心 admin.blade.php 或 bundle。采用同类动态机制必须使用 NodeEndpoint 独立字段名和 wrapper，捕获当前 view path/data以保留之前 composer，并运行多顺序组合测试；也可以直接提供独立 Admin Bearer 鉴权页面以避免构建依赖。

FlowScope public assets/status 不含身份或 Token；敏感 dashboard routes 使用 EnsureEnabled + admin。采集器有独立 panel_url、collector Token、SQLite状态，且校验 HTTPS（HTTP 仅 loopback 测试允许）。其 panel_url 不会自动跟随 Node panel.url；NodeEndpoint 不应重写采集器配置、流量数据库或机器绑定。迁移说明须指出这个独立入口和私有 CA信任需求，采集器迁移为独立操作。

## 独立 Node 安装与迁移结论

当前独立 Node 固定发行 `v1.13-orphan.4`，但旧 install 接受 latest，下载阶段仅 curl + 二进制 version 检查，不具备下载 SHA 校验；GitHub release 当前不是 immutable。NodeEndpoint 应锁定实际 commit、release、asset digest，在执行前验证完整下载，不能委托未受控最新版本安装器。

独立安装器安装模式会替换全部 bindings，不能作为既有多实例迁移入口。Node 配置实际含 `instances`、`nodes` 多绑定，`panel.url`、`panel.token_env` / `machine.token_env`、`kernel.type=singbox|xray`。AutoInstanceID 由 URL+mode+target 派生，修改 URL 可能改变实例 ID和隐式数据目录；迁移需保持准确目标身份及显式 kernel.config_dir，缺少可证明的数据目录时安全拒绝，不能重建机器或清空流量。

Go REST 和 Gorilla WS 默认使用系统可信根并校验 SAN；不需要修改 Go 业务代码。可用 per-service `SSL_CERT_FILE` 指向明确合并现有系统根的受控 bundle，首次 CA import 必须验证独立渠道指纹，Root CA 私钥永不分发。Token 输入使用 stdin/getpass/0600 file；现协议 GET与WS query仍可能包含Token，节点 debug logging可能打印WS完整URL，因此代理禁访问日志、节点 info/warn等级与保护日志为必要操作限制，不能宣称 Token 完全不进入 URL。

## 可直接实现 / 必须保留的验收边界

可直接实现：原生 plugin生命周期、独立 namespaced设置与 API、独立 Admin页面、请求局部 handshake response middleware、独立 Root Nginx入口、独立私有PKI、Node信任导入与精准配置迁移。无需覆盖官方Controller、Admin bundle、全局配置或其他插件文件。

必须隔离实际验收：首次安装/升级/禁用/卸载、多插件多顺序 Admin composer、常驻router启用/禁用后反复请求、Sanctum非管理员拒绝、node/machine REST、真实NodeWorker WSS、Nginx冲突/回滚、原域DNS不可用后的IP TLS连接、多实例生产结构迁移、Root隔离Linux部署。源码审计与Laravel隔离组件测试不代替真实Nginx/Go/WS或生产部署；未执行项目标注 **NOT TESTED**。

2026-10-09 实际原生组件集成结果：`tests/integration/run.php` 的 25 项场景 PASS，使用 PHP 8.2.33、真实 Laravel 12.69.3/Sanctum 4.3.3、官方未修改 PluginManager/Admin/ServerV2/ServerController 与 SQLite。发现并修复了 Controller private fail 与宿主 public fail 继承冲突、route action 前导反斜杠导致 handshake 不改写、绝对DNS网站别名绕过冲突检测、nested validation 剔除 Root status 已允许元数据四个真实问题；加入 CA 现代算法/密钥强度检查和完整双CA指纹命令。该结果仅证明组件场景，详细跨平台/真实服务边界仍以 TEST_REPORT 为准。
