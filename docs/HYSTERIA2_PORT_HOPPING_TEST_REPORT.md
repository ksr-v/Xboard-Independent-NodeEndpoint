# Hysteria2 端口跳跃实测（2026-10-25）

**结论：两种内核的端口跳跃均能工作，但当前版本需要额外配置节点 UDP 范围转发，尚不具备安装后自动跳跃的完整部署流程。** 仅在面板填写范围、放行防火墙，不会自动让范围内端口进入同一个 Hysteria2 服务。

在用户授权的专用 VM `192.168.128.128` 上，正式 Node **v1.13-orphan.5** 的 sing-box、Xray 服务端各完成六项检查，共 **12 项通过**。其中“无转发”是预期失败的负例，不应理解为无转发也可用。真实保存与订阅生成测试另完成六项正向断言，并发现输入校验及客户端输出缺口。本次没有修改业务源码、升级生产面板或发布新版本。

## 持续跳跃结果

测试连接范围 `42450-42453/UDP`，服务实际监听 `42443/UDP`，跳跃间隔 `5s`。真实面板保存、Node API构造器及 SingBox 1.11 订阅生成器的输出，被同一合成用户用于正式 Node 和测试客户端。服务端以 standalone 模式加载这些真实输出；本次没有通过在线面板 HTTP/WebSocket 同步配置。

客户端使用项目已经固定、离线缓存的 sing-box / sing-quic 依赖，选取面板生成的 Hysteria2 outbound，仅补充本次专用 CA 信任与 TLS 验证。地址是隔离 veth 上的 IPv4，证书含 IP 与 DNS SAN；未使用 `insecure`。TCP 与 UDP 并行运行，每 150 ms 分别进行 32 KiB 和 1 KiB echo，每个返回字节均与原数据比较。TCP 只调用一次 Dial，UDP 只创建一次会话，循环不重连。

| 服务端内核 | TCP 校验轮数 / 字节 | UDP 校验轮数 / 字节 | TCP / UDP 持续秒数 | 实际目的端口数 / 源端口数 |
| --- | --- | --- | --- | --- |
| singbox | 286 / 9,371,648 | 297 / 304,128 | 45.133 / 45.021 | 4 / 10 |
| xray | 282 / 9,240,576 | 297 / 304,128 | 45.027 / 45.006 | 4 / 10 |

两组均实际访问 `42450`、`42451`、`42452`、`42453` 全部四个目的端口，所有 payload 校验通过。首次观察到新源 socket 的时间约为 `0、5、10、15、20、25、30、35、40、45s`，与配置的 5 秒间隔一致。随机目标端口允许连续重复，所以源 socket 更换不必对应不同目标端口；旧 socket 的包与重传也可能交替，不能把所有报文端点切换次数当作精确 timer 次数。这里的“保持传输”表示同一会话完成并未发生 I/O 错误，不是任何公网条件下零丢包或无延迟保证。

## 对照与认证检查

以下六项在每种服务端内核各执行一次，TCP、UDP 均验证：

| 场景 | 实际结果 |
| --- | --- |
| 单端口直接连接 `42443` | 成功，数据一致 |
| 配置范围，尚未建立转发 | 连接被拒绝，零校验成功回显字节；预期负例通过 |
| 建立范围转发，单独访问 `42451` | 成功，证明范围端口确实映射到服务 |
| 范围转发 + 5 秒跳跃持续 45 秒 | 同一 TCP 与 UDP 会话成功，实际跨四个目的端口 |
| 错误合成用户密码 | 两种流均 `authentication`，零校验成功回显字节 |
| 错误 CA | 两种流均 `certificate`，零校验成功回显字节 |

负例不仅检查程序退出状态，也核对错误类别；没有用超时替代明确的认证或证书拒绝。

## 当前部署需要补上的步骤

例如本次有效配置是：面板连接端口 `42450-42453`、服务端监听端口 `42443`、Hysteria版本 `2`、间隔 `5` 秒；订阅中的 sing-box 表达为 `server_ports: ["42450:42453"]`、`hop_interval: "5s"`。

节点服务只监听一个 UDP 端口。需要在**节点服务器**同时完成 UDP 范围放行及范围到监听口的 DNAT/REDIRECT，并按实际系统持久化；云安全组若适用也需允许该 UDP 范围。当前 Node 安装器不生成这些规则。NodeEndpoint Root 工具处理的是**面板 TCP 通讯入口**，其 8443 放行不能替代节点的 Hysteria2 UDP 范围映射。

本次实际加载的是专用网络命名空间内的以下规则，仅用于说明已验证的映射语义，**不是可直接粘贴到生产宿主的部署命令**：

```nft
table inet codex_hy2 {
    chain ingress {
        type nat hook prerouting priority dstnat; policy accept;
        iifname "hys-1025" ip saddr 10.213.25.2 udp dport 42450-42453 counter redirect to :42443
    }
}
```

该命名空间只有测试客户端与服务器，无公网路由，不改变宿主 nft/iptables。两内核均核对了测试转发规则的计数增长。

官方 sing-box 文档将 `server_ports` 与 `hop_interval` 标为 1.11 起支持；本面板还存在精确客户端名称判断，详见下表。[sing-box 官方配置](https://sing-box.sagernet.org/configuration/outbound/hysteria2/) 最新独立 Hysteria 服务端的自动范围映射能力，不能视为本项目固定嵌入式内核与安装器已具有该能力；本次结论来自本项目源码及真实网络对照。[Hysteria 官方端口跳跃说明](https://v2.hysteria.network/docs/advanced/Port-Hopping/)

## 后端保存与订阅生成测试

测试源码为正式后端 `v2026.10.25.1`，提交 `2b3000781d95ae43255471688e9bb8d4220f9551`。使用真实 Laravel Validator、`ServerSave`、`ManageController::save()`、Eloquent、`ServerService` 和各协议生成方法，数据库为内存 SQLite，缓存为内存 array；未连接生产数据库、Redis或读取生产凭据。订阅方法直接调用或通过反射调用，客户端名与版本由测试显式指定，因此这些结果验证配置生成，不代表完整 HTTP 订阅识别、客户端导入或客户端实际跳跃均已通过。

复现命令：`php -c .runtime/php.ini .runtime/hy2-20261025/backend_subscriptions.php`。安全结果见同文档的 JSON 证据 `backend` 部分。六项正向断言全部通过：有效请求保存、范围与间隔持久化、订阅范围转换、单监听端口 Node API、SingBox范围与间隔、ClashMeta间隔。

有效测试请求主要字段为 `type=hysteria`、`port=42400-42403`、`server_port=42400`、`protocol_settings.version=2`、`protocol_settings.hop_interval=7`。数据库保留原范围；`getAvailableServers()` 随机选择范围内单个 `port`，同时附加原始 `ports`。Node API仅下发 `server_port=42400`，不下发范围或跳跃间隔。该数据模型的部署含义是：客户端使用整个 UDP 端口范围，节点服务监听单个 UDP 端口，由主机网络规则将范围转发至监听口。仅开放防火墙范围不等于配置了端口转发；实际规则及流量结果由独立 VM 联测记录确认。

| 测试客户端参数 / 生成器 | 端口范围输出 | 自定义间隔输出 | 本节可证明的范围 |
|---|---|---|---|
| SingBox `sing-box/1.11.0` | `server_ports=["42400:42403"]` | `hop_interval="7s"` | 生成正确；运行结果见 VM 联测 |
| SingBox `sing-box/1.10.7`、无版本 | 无，只保留随机单端口 | 无 | 生成器未启用跳跃 |
| 同一生成器的 `hiddify/2.5.0`、`sfm/1.12.0` | 无，只保留随机单端口 | 无 | 精确客户端名判断使跳跃字段缺失，未推断其内核能力 |
| ClashMeta | `ports="42400-42403"` | `hop-interval=7` | 配置输出已验证，未运行该客户端 |
| Stash | `ports="42400-42403"` | 无 | 自定义间隔未下发，未运行该客户端 |
| General URI | `mport=42400-42403` | 无 | URI输出已验证，未验证具体导入器 |
| Shadowrocket URI | `mport=42400-42403` | `keepalive=7` | 已确认映射键，未运行客户端证明其跳跃间隔语义 |
| Surge、Loon | 无，只保留随机单端口 | 无 | 当前生成器未表达跳跃范围 |

十组边界输入中，仅 `hop_interval="bad"` 被拒绝。其余九组均被校验接受：间隔 `-1`、`0`；版本 `3`；反向范围 `42403-42400`；连接端口 `0`、`70000`；混合列表 `42400-42402,42405`；纯列表 `42400,42401`；监听端口填写范围 `42400-42403`。负数、超界端口和不支持版本缺少语义校验；`0` 的含义也未在保存端约束。列表可能是某些客户端支持的语法，但本后端未正确标准化：反向范围随机选端口时交换上下界，原 `ports` 仍会生成反向 `42403:42400`；纯逗号列表被转为第一个整数且不保留 `ports`；混合列表被整体写入一个 SingBox范围字符串；监听端口范围被 Node API强制转为首个整数。

用于 VM 联测的另一个私有导出由 `.runtime/hy2-20261025/panel_runtime_export.php` 生成，仍使用真实内存 Laravel保存与订阅链路。参数为地址 `10.213.25.1`、连接范围 `42450-42453`、单监听端口 `42443`、版本 `2`、间隔 `5s`；客户端密码直接来自本次新建的合成测试用户。导出只存于 ignored 的 `panel-runtime.private.json`，stdout仅输出成功字段与配置键名。正式报告不包含该私有文件或认证值。

相关源码定位：

- `.runtime/xboard-bundled/app/Http/Requests/Admin/ServerSave.php:66`：版本与间隔仅作整数校验；`:126`：连接端口与监听端口仅必填。
- `.runtime/xboard-bundled/app/Http/Controllers/V2/Admin/Server/ManageController.php:52`：真实保存路径。
- `.runtime/xboard-bundled/app/Models/Server.php:263`：Hysteria配置字段及类型；`:354`：协议设置读取和写入转换。
- `.runtime/xboard-bundled/app/Services/ServerService.php:70`：范围转随机单端口及附加 `ports`；`:253`、`:311`：Node API生成。
- `.runtime/xboard-bundled/app/Utils/Helper.php:145`：端口范围解析及反向范围交换。
- `.runtime/xboard-bundled/app/Protocols/SingBox.php:681`、`:694`：HY2生成及精确客户端/版本门槛；`.runtime/xboard-bundled/app/Support/AbstractProtocol.php:205`：`supportsFeature()`。
- `.runtime/xboard-bundled/app/Protocols/ClashMeta.php:589`；`Stash.php:432`；`General.php:324`；`Shadowrocket.php:349`；`Surge.php:234`；`Loon.php:340`：各订阅生成方法。

本节未更改业务源码、提交仓库或部署面板；未以订阅字段存在替代真实网络功能验证。

## 环境、证据与复现边界

- 后端正式版本 `v2026.10.25.1`，源码 `2b3000781d95ae43255471688e9bb8d4220f9551`；Node正式版本 `v1.13-orphan.5`，发布 tag `7b14a1c96b9e65ce5b0d7870b71d5770359dacd4`。
- 正式 Linux amd64 Node SHA-256：`0f3aeda43036081c668826a87cf4c35d227ac8a0a4488ccebab2a4c74db1b78f`。
- 测试客户端 SHA-256：`115aaf17049a259241fe7c0118f351f39f7d71fba2eff372b63dd06a5d24b86e`。固定 sing-quic `v0.6.0`；sing-box 使用既有模块锁及维护构建 modfile，没有下载或更新原维护者源码。
- [安全测量数据](verification/hysteria2-port-hopping-20261025.json)：12组真实流量、端口时间、零 payload 抓包摘要、后端生成器及边界输入结果、实验源码 SHA-256。订阅样例中的固定合成密码也已移除；真实随机合成身份、证书私钥与运行日志不在此文件。
- [实验程序快照](../tests/e2e/hy2/README.md)：真实 Laravel fixture、私有合成配置导出器、Go客户端、echo/仅报文头观察器和 VM 编排。路径和隔离边界专用于本轮实验，不是生产安装器。
- Go客户端在既有离线环境构建：`.runtime/python/python.exe .runtime/rate-route-fix/run_go.py --linux build -tags with_quic,with_utls,with_wireguard,with_acme,with_clash_api -trimpath -ldflags '-s -w' -o D:/xb-node-endpoint/.runtime/hy2-20261025/hy2-client-linux-amd64 D:/xb-node-endpoint/.runtime/hy2-20261025/client_main.go`。
- 前两次实验脚本因空命名空间 JSON 输出及 nft 单行块语法停止，修正实验脚本后完整执行12组；没有把脚本失败当成协议缺陷。失败材料保存在私有测试目录中，未读其运行日志。
- 完成后本轮进程全部退出、两个网络命名空间已删除，宿主 nft 规则规范化摘要保持不变，既有面板 `127.0.0.1:17001` 返回 HTTP 200。未替换已有 Node 服务，未读取 Root 私有运行日志。

**未验证/延期**：公网链路与跨公网 NAT、IPv6、商业客户端实际导入/运行、默认30秒间隔、混淆、长期满载和 QUIC 吞吐限速。此次证明的是 Hysteria2 跳跃的实际可用性、数据完整性及必要转发条件；先前 TCP 限速结论不能直接推广至 Hysteria2。商业客户端兼容性应在对应配置生成规则完善后补测。

后续若要形成一键部署，需要单独实现有范围校验、已有规则冲突检查、归属标记与回滚的节点 UDP 映射；并修复保存端的端口/间隔校验及客户端配置生成缺口。本轮只记录问题，没有自动执行这些产品改动。
