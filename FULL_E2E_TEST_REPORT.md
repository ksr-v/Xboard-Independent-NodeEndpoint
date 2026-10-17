# NodeEndpoint 1.0.0：完整 Xboard / NodeWorker / Go 联测

测试日期：2026-10-10；清理恢复完成于 2026-10-17（Asia/Shanghai）。**两 VM 实机验收及最终清理已完成；发现两项上游 Xray 用户更新限制，CLI UUID save 的 Observer 触发仍有未定位缺口。** 实际 Admin HTTP 属性更新链通过；成功检查、失败历史、恢复过程和未测范围保留，不能称所有联测通过，证据以本文及公开 JSON 为准。此前单 VM 的 139 项检查和 Windows 110 项属于历史基线，包含组件、协议及合成面板测试，不与本轮实际联测累加为唯一用例。

2026-10-17 续测完成：run006 为 PASS/22 项；run007 的 14 项迁移、逆向回滚和计量检查通过，但初次测试清理为 FAIL。修正辅助脚本并通过 13 项本地回归后，已独立完成清理恢复，原失败状态未改写。机器恢复造成初始 PID、节点外来 nft 快照及 9 个原站文件元数据变化；原站文件字节仍相同，续测仅固定并保护恢复后的状态。原节点备份已移回，面板自有插件/入口/规则/工作进程、合成数据及临时认证均清理或恢复，SSH 凭据已撤销。证据见 [清理恢复](docs/verification/e2e-node-cleanup-recovery.json)、[最终清理](docs/verification/e2e-final-cleanup-result.json)、[Admin 属性链](docs/verification/e2e-admin-user-sync-result.json)。

## 实际环境与授权

用户明确授权两台本地专用虚拟机的 SSH / sudo、依赖、测试环境创建与清理。`192.168.128.128` 是已经运行的完整 Xboard 面板；`192.168.128.129` 运行固定版本的实际 Go Node。没有访问生产服务器或修改参考仓库。

面板使用 Ubuntu 24.04.3、宝塔网站 Nginx 1.30.5、现有 PHP-FPM 8.3、PHP CLI 8.3.33、Laravel 12.54.1、Sanctum 4.2.0、Workerman 5.1.9、workerman/redis 2.0.5，以及实际 MariaDB 和 Redis。应用根为 `/www/wwwroot/xboard`，应用 UID/GID 为 1001，FPM socket 为 `/tmp/php-cgi-83.sock`。同时运行的宝塔控制面板 webserver 与网站 Nginx 是不同二进制；Root 工具显式选择实际网站 Nginx，不修改控制面板服务。

实际部署的 72 个关键 PHP 文件与已审计参考源码逐字节匹配；不能仅由这些匹配推断整个部署的 Git commit。原版 NodeWorker、ServerController、TrustProxies、PluginManager、队列、数据库和 Redis 均参与实际执行，未修改 Xboard / Go / 代理内核业务代码。

Node 使用用户维护的独立发行 `v1.13-orphan.4`，AMD64 二进制 SHA-256 为 `2b37ea5fdfeb721d08b53330134b7a2e072447c10913620da13ceba600d7b772`，来源 commit `8dbee14fb2f32a4f9324929fc24828cea6e29b38`。采用两实例：传统 node / Xray / 两个 VLESS 绑定，以及 machine / sing-box / 两个 SOCKS5 绑定。仅使用本次创建的合成套餐、用户、机器和节点，不读取既有管理员密码。

## 已完成的实际检查

- 真实 PluginManager 上传、安装及启用基线原生 ZIP（SHA-256 `8df4a4a28138019266f6b5080e520a14f58ac406e92e34837d2d9e473dd53533`）；52 个成员全部逐字节匹配。匿名和普通用户设置请求返回 403，既有管理员通过本次临时 Sanctum token 请求返回 200；HTTPS IP 设置保存、管理页面、JS/CSS 资源均成功。既有 Admin、公有资源、外来插件与受保护数据库记录保持原样。
- 原 HTTP 接入基线：由面板机实际连接节点机四个代理端口，每个绑定双向回显 1 MiB。真实 TrafficFetch/stat 队列排空后，用户 `u/d` 和 StatUser 各精确增加 4,194,304 字节；四个 StatServer 的 `u/d` 各增加 1,048,576 字节。HTTP 200 没有被当作计量成功。
- Node 端独立 nftables 表限制四个测试代理端口；实际节点机非 loopback 源地址访问被拒绝，面板访问正常，既有 nftables 表保持。原有标准节点目录仅含备份，已原样暂存供结束后恢复。
- 实际独立 HTTPS 入口已部署，首次 CA/leaf 到期时间精确对齐 `2036-10-10T06:42:15Z`，IP SAN 正确；相同配置重复安装不改变 PKI/监听。加密 Root 私有 CA 导出、独立指纹验证及在线状态目录 CA 私钥移除完成，移除后 TLS 仍正常。
- 从节点机对实际证书进行 TLS 正负验证：未知 CA、错误 SAN 被拒绝，正确私有 CA 与 IP SAN 被接受；全程没有关闭证书验证。真实插件 API 单/双 CA 导入/导出、禁用/重新启用、重复启用及原生同版本 ZIP 拒绝均通过，设置和公共 CA 保持。原生插件阶段 24 项检查通过，最终卸载及 purge 另有清理证据。

| 实际阶段 | 结果与证据 |
| --- | --- |
| 两种身份、两个内核、四个绑定 | node/Xray/VLESS 与 machine/sing-box/SOCKS5 均通过；真实原版 NodeWorker、Go 新鲜 metrics、四路代理及数据库计量共同验收 |
| 选择迁移与回滚 | 两次实际 CLI 只迁移指定实例；其他 URL、绑定、token、二进制、稳定目录/sentinel 保持，metadata 仅允许与 URL 相关的派生身份字段变化；反向 CLI 回滚恢复原 HTTP 配置及原 metadata |
| 同 CA 紧急换钥及 Root 回滚 | 实际换服务器私钥，CA 保持；Go fresh restart 接受新叶及回滚后的原叶，信任文件逐字节不变 |
| 新 CA 过渡 | 实际双根导入、原 newCA/leaf 激活、Go fresh WSS、仅新根信任及 Root rotate-finish 均通过；旧根撤除由测试辅助程序执行明确的管理员操作，产品没有自动撤旧能力 |
| Redis 原生服务用户推送 | 合成 User2 的真实 save 后显式调用原版 NodeSyncService::notifyUserChanged，经 Redis/原版 NodeWorker 分发；传统 Xray 两绑定收到 delta:add，machine 两绑定收到 mailbox 全量事件。但单用户 UUID 增量替换使 Xray 内核停止，新认证失败；不能将事件送达当作业务更新成功，CLI UUID save 的 Observer 触发仍有未定位缺口 |
| 实际 Admin HTTP 属性更新 | 保持 User2 UUID 不变，通过原版 Admin HTTP UserController 更新 device_limit 并恢复私密快照中的原值；两个独立窗口均观察 Observer/队列/Redis/NodeWorker 的两条传统 delta 和两条 machine 全量事件、四路原 UUID 认证成功、Go PID 保持、无 WSS 断连或 REST reconciliation；不代表 UUID 热更新通过 |
| 设备断连状态 | 实际代理保持连接时设备条目大于零；停止本次 Go 后原版 NodeWorker 断连处理使 WSS false、设备条目归零，再启动恢复 WSS |
| 新节点安装 | 实际 prepare 无下载路径、独立 SHA/ELF 核对已上传固定资产、实际 install-new；两个新 unit 顺序承接四个绑定，仅信任新 CA，冷启动核验当前用户认证、WSS、设备与流量；不宣称 UUID 热更新通过 |
| 清理及保护 | Node/echo/unit/自有 nftables 清理；原节点备份目录原样移回；原生插件卸载/purge、合成记录/临时认证恢复、Root 入口/UFW/桥接/own worker/queue 移除、临时 SSH/Sanctum 凭据撤销均通过 |

成功业务阶段及独立清理恢复汇总包含 37 项执行记录（run006 的 22 项、run007 的 14 项业务检查及单独恢复检查；run007 整轮仍为 FAIL），其中包含同一运行状态的重复核验，不作为唯一用例数量。run002 的有效 HTTP 基线、同 CA 换钥/回滚等历史证据保留在独立 JSON；该轮整体仍为 FAIL。

本轮完整面板原生上传的是上述基线包，真实 Root CLI 使用实验目录内的修复工具。最终重建 ZIP 的精确摘要、成员数及 Windows/Linux 各 6 项原生组件导入复测见 `dist/verification/final-package-results.json`；不能将重建 ZIP 误写为本轮已在完整面板上传的同一字节包。

## 联测设计及证据

Root 产品工具通过独立站点创建 HTTPS 8443 入口。实验桥接站点只监听 loopback，连接原 FPM 和原版 NodeWorker；原 HTTP 网站配置保留。实验辅助程序只允许节点机访问新端口，产品 Root 工具仍只读检查防火墙，不自动修改规则。

临时认证 token 仅在原 token 为空且原机器/节点数量为零时设置。合成用户 UUID、机器 token 与临时 token 经过归属核验后，使用 SSH 传至 Root-0600 文件。临时 SSH key 的 forced command 限定六种固定回调（含 Observer 与显式原生服务轮换）及四个代理端口，禁止任意命令与转发；不复制本地 SSH 私钥。私密响应及本次诊断日志保留在 VM Root 目录，不进入 ZIP 或公开报告。原 HTTP 网站的既有 access log 策略保持；基线 Node 请求的 token query 可能按原配置被宿主记录，结束后合成 token 撤销，不改写或发布宿主日志。独立 HTTPS 入口禁用含 query 访问日志。

CA 测试真实执行加密导出、独立 checksum/指纹/key-match 验证、移除在线状态目录私钥，并通过显式 `--ca-key` 从加密导出重签。导出材料和测试口令保留在面板 VM 的 Root 私有独立目录，未迁到物理隔离介质；本轮证明加密导出、从在线状态目录移除私钥及使用显式加密导出重签；材料与口令仍在同一可联网 VM 的 Root 路径，未验证异机或物理隔离保管。生产需按 SECURITY 指引将导出和口令分别安全转移。

WSS 验收要求实际 worker heartbeat、Go 新鲜 metrics、NodeWorker 存活状态，并通过真实 Redis 用户 UUID 推送验证代理认证在不重启 Node 的情况下改变；REST 正常和握手地址正确不能替代此检查。认证探测只在身份停止后执行，避免干扰原版 NodeWorker 的连接和设备状态。

公开记录位于 `docs/verification/e2e-*-result.json`；试验原始日志、数据库一致性备份及恢复材料保持 Root 私有。复现辅助程序和边界见 [tests/e2e/README.md](tests/e2e/README.md)。

## 发现及修复

真实宝塔配置暴露了两个 Root 产品问题：未指定或显式指定二进制时的多 Nginx master 判定需要区分网站 Nginx 与控制面板 webserver；Nginx 解析器须保留引号语义，合法日志字符串中的 `{` / `}` 不能被当作配置结构。已修复并在 Windows、实际 Linux 面板 VM 分别完成 36 项面板工具回归，其中三个新增用例覆盖这些边界。

Node 联测 run001 正确发现实验隔离规则缺陷：访问机器自身非 loopback IP 的包仍可能走 `lo` 接口，仅按接口放行不够严格。该轮自动清理自有服务、echo 和网络表，保留失败证据。run002 将规则收窄到实际 loopback 源地址后，负例和完整 HTTP 流量/计量基线通过；这项修正仅属于测试隔离，不改产品 Go 或内核。

run002 已完成 29 项有效检查后，协调者重复安排要求叶证书必须刚发生变化的重连检查；真实运行与 WSS 通过，但旧辅助断言因叶证书未再次改变而失败。该轮最终为 FAIL/32 项记录，自动清理无失败；不能将清理通过当作整轮通过。辅助程序修正为准确记录 `leaf_changed`，Root 回滚复用原 CA/leaf 后以新 run003 重做两种迁移及新 CA 切换，changed-leaf 和 same-leaf 两种真实回归分别通过。

run003 实际完成仅新根信任后，在额外强制全量用户同步中发现固定 Go `v1.13-orphan.4` 的 Xray 缺陷：同一 ID 更换 UUID 时 `kernel.UserDiff` 仅加入 `toAdd`，没有把旧记录加入 `toRemove`；Xray UserManager 因重复 email 拒绝添加。实际四绑定均收到 exclusive EventSyncUsers，窗口无 WSS disconnect/REST reconciliation，但两条 Xray AddUser 日志明确 duplicate/already exists，新 UUID 认证未通过。该轮最终 FAIL/24 项记录，清理无失败。Go 二进制和源码保持原样，这项全量替换限制没有被修复或宣称通过。

run004 尝试模型 save→UserObserver 路径，不额外强制 full sync；新 UUID 未生效，窗口没有观测到指定用户推送事件，最终 FAIL/12 项记录，清理无失败。真实只读诊断确认 EventServiceProvider 已加载，UserDispatcher/created/updated/deleted Observer 各一条，User2 可用、组匹配四节点、DB transaction level/pending callbacks 为零，Redis 队列和相关 failed job 数均为零。原因尚未定位，不能写成 Observer 未注册，也不能宣称完整 Admin/Observer 触发链通过。

run005 明确使用模型 save 后显式调用原版 NodeSyncService::notifyUserChanged，经实际 Redis/NodeWorker 分发。两个传统绑定确有 DeltaAdd，两个 machine 绑定确有 FullUsers；但 `Xray.RemoveUsers` 移除唯一旧用户后因剩余用户为空调用 Stop，随后的 AddUsers/UpdateUsers fallback 均报 `not running`。保留日志有各四次对应失败，新 UUID 认证未通过；该轮最终 FAIL/12 项记录，清理无失败。它确认事件送达及第二项固定 Go 边界，没有证明四路认证更新成功，也没有覆盖 run004 的触发链缺口。后续成功检查使用当前 UUID 冷启动，独立完成新装、设备、流量和 CLI 回滚。Go 对 raw `sync.users=[]` 的拒绝及 machine 空列表 nil 处理另有源码审计观察，本轮未执行空用户集业务验收。Go 与代理内核保持原样。

原生安装测试辅助程序在安装前发现数字选项解析及既有 `public/storage` symlink 快照处理问题；修复后只读记录外来链接目标，不遍历或改动链接，新增自有插件目录仍拒绝 symlink。两个预检拒绝都发生在安装或数据库写入之前。

测试暂停期间，原 `xboard-queue.service` 按 `--max-time=3600`、`Restart=always` 和 5 秒间隔执行正常小时退出/自动拉起。恢复测试后，06:36 UTC 的实际系统包升级将 Redis `5:7.0.15-1ubuntu0.24.04.4` 更新为 `.5`，触发 Redis PID 更换；原队列随后因 Redis 短暂断连退出并按既有策略自动恢复。私有 dpkg/systemd 记录和公开白名单事件分别保存这两类重启，原队列 unit、PHP executable/inode、运行用户及既有 Redis jemalloc drop-in 保持。

测试程序未执行上述 Redis/原队列的 stop/restart 或系统包升级，不回滚系统维护变更；后续检查基于已重新核对的 Redis 版本及服务身份。不能将本轮结果写成 Redis/原队列 PID 全程不变或服务零重启。10 月 10 日阶段曾严格检查原控制面板 webserver、网站 Nginx、PHP-FPM 和数据库最初 PID/start；10 月 17 日外部恢复后这些 PID 已变化，后续固定并检查恢复后的身份。受保护的既有插件数据库记录实际为七条，包含三个用户指定插件以及其它既有插件。

## 最终恢复与公开证据

原站配置字节、Admin、原有七条插件记录及外来插件文件/业务记录保持。外部机器恢复后，9 个原站文件元数据变为 Root/0600，节点外来 nft 快照和原服务 PID 也已变化；这些差异保留，未声称回到初始 PID/元数据。清理前后核对当前原服务身份、恢复后的文件权限和外来 nft 策略均保持，未为消除差异改写旧快照。Redis/原队列系统维护记录另列。

合成用户/节点/机器/套餐/组及相关测试统计已删除，临时全局 node token 按原始数据库行恢复，队列排空，worker heartbeat 自然过期；没有 flush Redis 或整表删除。原 worker 文件恢复原字节、UID/GID/mode，不声称 inode 未变。节点机原 `/etc/xboard-node/backups` 经归属确认原样移回。本次 SSH forced-command 行删除，原 authorized_keys 字节和权限恢复，节点机临时私钥及凭据 relay 删除。测试依赖、数据库备份、加密 CA 导出及已撤销凭据的诊断证据保持 VM Root 私有，不进 ZIP。

- [成功 Node 阶段结果](docs/verification/e2e-node-result.json)、[run001 失败历史](docs/verification/e2e-node-run001-result.json)、[run002 失败历史](docs/verification/e2e-node-run002-result.json)、[run003 全量更新限制](docs/verification/e2e-node-run003-result.json)、[run004 未观测触发链](docs/verification/e2e-node-run004-result.json)、[run005 单用户增量更新限制](docs/verification/e2e-node-run005-result.json)
- [实际 Root 入口及服务恢复](docs/verification/e2e-panel-result.json)、[实际原生插件](docs/verification/e2e-plugin-result.json)、[环境与核心摘要](docs/verification/e2e-panel-environment.json)
- [实际 TLS 正负验证](docs/verification/e2e-tls-client-result.json)、[最终清理与临时凭据撤销](docs/verification/e2e-final-cleanup-result.json)
- [run007 初次清理失败](docs/verification/e2e-node-run007-result.json)、[独立清理恢复](docs/verification/e2e-node-cleanup-recovery.json)
- [实际 Admin 属性更新及恢复](docs/verification/e2e-admin-user-sync-result.json)

## 仍需独立验收的范围

本轮只覆盖实际宝塔 Nginx / PHP-FPM、局域网 IPv4、AMD64 和所列协议。Octane、Docker/OpenResty 作为应用部署、ARM64 执行、公网/IPv6 业务、实际旧 DNS 故障下的长期运行、订阅客户端、生产流量不中断、其他未知版本的 Admin/TrustProxies，以及 Independent `403604e` 的实际 PHP overlay 均未在本轮测试。云安全组不在本地虚拟机范围。

CRL/OCSP、自动撤销旧 CA、FlowScope 采集器自动迁移属于 V1 未实现边界；实际保留既有采集器不能宣称这些能力已经支持。
