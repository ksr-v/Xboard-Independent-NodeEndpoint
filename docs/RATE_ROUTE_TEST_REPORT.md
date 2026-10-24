# 限速与路由实际流量测试（2026-10-24）

此文保留原发行版本的历史失败结果；后续修复及复测见 [限速与路由修复验证](RATE_ROUTE_FIX_REPORT.md)。

结论：**当前版本不能判定为全部正常，存在已复现的限速和路由更新缺陷。** 本次完成测试与问题定位，没有修改节点、内核或面板业务代码；缺陷尚未修复。

## 固定环境与范围

- 用户授权的专用 VM：`192.168.128.128`，Ubuntu 24.04、aaPanel、Docker 29.9.2、Compose 5.6.1。
- 新建独立 SQLite 面板容器，入口仅绑定 `127.0.0.1:17004`；不使用既有面板数据库。镜像：`sha256:f8d45147fe23db138f0922b6137d162b283f90c2129ca6775c507ba084e00d73`。
- 后端审计基线：`Xboard-Custom-Source` 的 `e2dd8d75407fb9f6386cf7cf2938a4c015db68fe`（`v2026.10.23.1`）。四插件实际安装并启用：NodeEndpoint 1.0.7、SmartExpiry 1.2.0、QRCode Extend 1.2.0 审计副本、FlowScope 2.4.0。
- 实测使用自有仓库发布的 `v1.13-orphan.4/xboard-node-linux-amd64`，SHA-256：`2b37ea5fdfeb721d08b53330134b7a2e072447c10913620da13ceba600d7b772`。节点审计源码：`17cd9f0af2815beeb20c24aa8a845e1fb6207c5e`。
- 真实 sing-box / Xray 内核、TCP 代理与受控数据服务器；sing-box standalone 使用 SOCKS，Xray standalone 和两内核面板联测使用 VLESS。节点以前台独立进程运行，不安装或替换 systemd 服务。
- 真实流量只访问本机测试端点。下载/上传计时以应用收到全部数据或上传确认作为结束；Mbps 为十进制。常规计时样本 4 MiB，9 Mbps 阈值复核使用 16 MiB，容许约 20% 测量偏差。初始突发、TCP 缓冲会影响短样本。
- 面板联测经真实 API、Eloquent 观察器及配置同步；不模拟面板 API。为隔离功能，通讯使用 loopback HTTP，本轮不重复验证生产 HTTPS/私有 CA 安装流程。

## 已复现的运行缺陷

| 编号 | 实际结果 | 影响与定位 |
| --- | --- | --- |
| R1，优先修复 | sing-box 配置 8 Mbps 后，上传、下载及并发传输均超时；相同实例不限速基线超过 9 Gbps。9 Mbps、16 MiB 对照下载约 9.59 Mbps、上传约 8.94 Mbps，可完成。 | Linux splice 单次回调可达 1,048,576 字节，超过 8 Mbps 桶的 1,000,000 字节 burst；`makeCountFunc` 不检查失败的 `ReserveN`，等待 `InfDuration`。固定 `x/time v0.12.0` 的离线 Go 复现也确认该条件。节点 `internal/kernel/singbox/conntracker.go:647`。不能据 9 Mbps 对照推断所有更高速率或其他协议均正常。 |
| R2，优先修复 | Xray VLESS 配置 8 Mbps，下载约 8.000 Mbps；上传约 4,439 Mbps，仍为未限速量级。目标服务器收到全部上传后才返回确认。 | VLESS 走固定内核的 `DispatchLink → WrapLink`；该路径只给下行 Writer 加带宽限制，上行 Reader 只有统计。双向限制存在于另一条 Dispatch 路径。不能推广为所有 Xray 协议均有相同表现。 |
| R3 | 面板保存有效的 sing-box 原生规则及新 SOCKS 出站，实际请求失败；同一配置全新启动节点后返回受控 `OUTBOUND`。Xray 的相同场景可以应用。 | sing-box `Reload` 更新路由及入站，未将 `opts.Outbounds` 应用到出站管理器。路由引用新 tag，但运行中没有对应出站。`internal/kernel/singbox/singbox.go:195–315`。Xray 配置改变时完整重建内核，不能把这一通过结果解释为同等的增量更新实现。 |
| R4 | 两内核都出现：0→16 Mbps 时，新连接约 16 Mbps，已有连接仍超过 4 Gbps；16→12 Mbps，原有限速连接约 12 Mbps；12→0，新的连接恢复不限速，旧连接仍受原桶限制。 | 连接建立时缓存限速桶或 nil。启用/取消限速不会立即改变已有连接，正数之间修改可以生效。sing-box `conntracker.go:242`、`speedtracker.go:65`；Xray 固定内核流包装同样缓存桶。测试没有强制断开用户连接。 |
| R5 | 本地结构化 `domains: [exact.fixture.test]`：sing-box 仅匹配该域名；Xray 也将 `sub.exact.fixture.test` 送入指定出口。 | 文档声称精确域名，但 Xray 将裸值交给核心，成为关键词匹配，缺少精确前缀。`internal/kernel/xray/config.go:734`。该结构化规则属于 Node 本地配置，本面板目前没有对应存储/下发字段。 |

## 后端生产路径审计

以下检查在本次独立 SQLite 数据库中运行真实 Laravel 服务、验证器和 Eloquent 观察器。

- **隐藏节点的即时通知遗漏**：`enabled=true, show=false` 的机器节点仍被运行集合选中，但编辑其关联路由没有发出 `sync.config`；显示节点会发出。测试拦截 Redis 发布、记录固定事件字段，并在事务中回滚。证明的是即时通知缺失，未推断节点后续轮询永远不会更新。依据：`ServerService.php:45`、`ServerRouteObserver.php:23`。
- **负数限速输入被验证器接受**：真实 `Admin/UserUpdate` 对 `speed_limit=-1` 验证通过；套餐保存规则则有 `min:0`。这是管理员输入校验缺陷，本次未通过管理 HTTP 写入负数，也不是未授权绕过。依据：`UserUpdate.php:35`、`PlanSave.php:34`。
- **路由 ID 数组不是可靠的优先级表达**：SQLite 中请求 `[2,1]`，实际返回 `[1,2]`。`getRoutes()` 只做 `whereIn()->get()`，没有按输入顺序排序。未找到保序的文档承诺，不能推断其他数据库总按相同顺序返回。依据：`ServerService.php:117`。

## 已通过及已确认的行为

- 两内核不限速上传、下载基线均能完成受控大流量传输。
- Xray 8 Mbps 下载约 8.000 Mbps；同一用户两条下载连接总计约 8.000 Mbps，不同用户两条连接总计约 15.996 Mbps。
- 面板实际输出正确用户限速值；两内核新连接应用 16 Mbps，正数 16→12 Mbps 也可调整已有连接。
- 单独端口分流、端口阻断、未匹配端口直连、域名后缀、首条匹配生效、受控 SOCKS 出站连接都已完成真实 TCP 请求验证。
- 面板下发后，两内核的规则删除均恢复直连；sing-box 新出站在全新启动时可用，Xray 新出站配置可在运行中应用。

## 语义及文档边界

结构化规则的不同匹配字段目前是 **OR**。实测“指定端口 + TCP”也会匹配其他 TCP 端口；两内核既有单测明确要求拆分为多条规则，而文档未承诺 AND。因此这是现有语义及文档缺口，不能把测试最初的 AND 假设直接定为实现错误。原始测量保留该假设未满足的记录。

当前后端只输出旧式 `routes`、`custom_routes`、`custom_outbounds`，没有 `custom_route_rules` 字段。不能把 Node 本地结构化规则测试说成面板已经接通该功能。

`custom_outbounds` 有统一的 `tag/protocol/settings/proxy_tag` 外层，但一般协议的 `settings` 仍需要相应内核格式。首次 sing-box 面板出站探测误用了 Xray 的 `servers` 参数，该次结果不作为格式正确的产品结论；最终使用 sing-box 的 `server/server_port/version` 格式重新验证，热更新仍失败、全新启动通过。`custom_routes` 则直接使用内核原生规则数组。

## 可复查材料和未测范围

- [安全测量数据](verification/rate-route-20261024.json)；[清理与宿主保护核对](verification/rate-route-final-host-20261024.json)。原始日志、安装密码、UUID、机器 token 留在 VM Root 私有目录，不进入本报告。
- `tests/e2e/rate_route_panel.py`：固定镜像、独立 SQLite、四插件、归属校验及仅本容器的清理。
- `tests/e2e/rate_route_seed.php`：本次合成身份及真实面板模型操作；仅 seed/credentials 输出私有 JSON，需要捕获保存，不能显示或提交。
- `tests/e2e/rate_route_traffic.py`：摘要固定的发布二进制、两内核 TCP、受控出口、限速与路由测量。
- `tests/e2e/rate_route_managed.py`：真实面板限速更新与路由配置测试。默认完整流程，`routes` 参数为最终的有效格式热/冷启动对照。

上述脚本是本轮固定实验辅助程序，路径、日期、镜像和归属标记专用于本次测试 VM；不是生产部署命令。在其他环境必须先审查和调整隔离边界。安全导出数据区分原始观察、最终复核及被替代的初始配置探测。

**未验证/延期**：UDP/QUIC、其他代理协议、Xray SOCKS/HTTP 带宽身份映射、跨节点全局总速率、外网链路吞吐、长时间压力及生产数据库。原因是本轮聚焦两种内核 TCP 核心路径，已发现需要优先修复的缺陷；在相应修复完成后恢复针对性回归，再扩展协议范围。

本轮自动审批拒绝读取 Root 私有诊断日志（包括关键词计数）；该动作没有执行。Xray 改用其实际声明支持的 VLESS 后完成实测，以上结论来自受控流量、公开测量数据和固定源码，不依赖被拒绝的日志读取。
