# Node 对接与安全迁移工具

版本：NodeEndpoint 1.0.0。入口：`tools/node/node_endpoint.py`，Python 3.9+ 标准库；CA 验证另需 `openssl`。工具不运行上游安装器、不自动升级已有二进制，不改变面板绑定、流量历史或 FlowScope 采集器。

## 当前发行锁（2026-10-25）

NodeEndpoint 1.0.8 随附的独立 tools 1.0.7 将 Node 下载版本更新为 **v1.13-orphan.5**。以包内 `tools/node/release-lock.json` 的 source commit 与逐资产 SHA-256 为当前依据，`audited-SHA256SUMS` 同步记录发行清单。工具的保守迁移实现与 CLI 版本 1.0.0 保持，未改成自动替换已有二进制。

发行源码 commit 以当前 `release-lock.json` 的 `source_commit` 为准；二进制构建于 `6e1d5c0cf176f106438bfdc40e81ff85ead2971e`，随后发行提交补齐安装器固定资产摘要、域名 / 私有 CA 通用下载校验和文档，不改变 Go 业务代码。该构建来源另记为 `binary_source_commit`，不把安装器自身的摘要误当成二进制编译来源。

已有节点升级到本次限速 / 路由修复版本使用[配套更新教程](UPGRADE.zh-CN.md)中的明确 `upgrade`，不使用 `prepare / install-new` 或面板新装命令。下方 v1.13-orphan.4 的审计摘要与联测结果是历史证据，不能视为 v1.13-orphan.5 的实机验收。

Root 面板入口继续固定 root-installer-v1.0.9 / tools 1.0.6，它不负责 Node 二进制升级。不要把两个工具包版本差异当成需要重建面板 CA 的信号。

## 历史源码及发布审计（v1.13-orphan.4）

2026-10-09 只读审计 [独立 Node](https://github.com/ksr-v/Xboard-Node--Custom-Source/tree/8dbee14fb2f32a4f9324929fc24828cea6e29b38)，commit `8dbee14fb2f32a4f9324929fc24828cea6e29b38`，发布 [v1.13-orphan.4](https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/tag/v1.13-orphan.4)。该 GitHub Release 当前 `immutable=false`，因此版本名本身不足以防止附件被替换。本工具将 GitHub API 获取的附件 digest 固定在 `release-lock.json`；实际下载 `SHA256SUMS` 后核对一致，源码 `install.sh` 的实际 SHA-256 也一致。

| 文件 | 固定 SHA-256 |
| --- | --- |
| install.sh | `7a4d33d097b78d71faf5c3741b068e6c8b2ebdb30e85c40a433dde4e653b7453` |
| xboard-node-linux-amd64 | `2b37ea5fdfeb721d08b53330134b7a2e072447c10913620da13ceba600d7b772` |
| xboard-node-linux-arm64 | `004b5801338cfb0fd1f56f6b4c39f4ff57468cd012cdab1454abdc7bb31e1327` |
| xbctl-linux-amd64 | `2ea33645f2f43bf23cdd966989ba01840bdc79a6ad562d522457799bebbd3fb9` |
| xbctl-linux-arm64 | `16c652558433016adececea6f4fb3d3bfe0692943307cada51742ac3708274fa` |

上游 `install.sh` 默认固定 v1.13-orphan.4，但仍允许 `latest`，下载后仅做运行版本检查，没有自行执行 SHA-256 校验。安装动作会替换全部本地旧对接并归档实例目录；`--token` 会传入 `xbctl` 进程参数。因此生产迁移不能用重新安装命令。本工具只下载固定版本 Node，校验内置 SHA-256 及 ELF 架构后才允许安装；不下载或执行上游 shell、不调用 `xbctl config init`。

实际配置是 block YAML：`panel.url`、`panel.node_id`、`panel.token_env` 或 `machine.machine_id`、`machine.token_env`；内核字段 `kernel.type` 为 `singbox` 或 `xray`。支持 `instances` 多实例和 `nodes` 多节点绑定。`ApiHost`、`PanelURL` JSON 风格及环境变量部署不属于本次已审计的配置格式。

## 首次 CA 信任

从管理员已验证的管理端/SSH 渠道取得 **证书 DER 的 SHA-256 指纹**，将公有根证书通过独立文件传到 Node。证书下载入口给出的自报指纹不构成独立校验；不得把指纹和未知来源证书一起照单全收。私钥永不传到 Node。

```bash
python3 tools/node/node_endpoint.py ca-import \
  --ca-file /root/node-endpoint-ca.pem \
  --ca-fingerprint '<独立渠道确认的64位SHA-256>' \
  --output /root/verified-node-endpoint-ca.pem
```

该步骤核对完整 PEM、全部根指纹、CA Basic Constraints/Key Usage、根自签名（`openssl verify -check_ss_sig`）、链和有效期；只创建受控文件，不改变系统信任。支持 RSA 至少 2048 位或标准 P-256/P-384/P-521，签名为 SHA-256 或更强；弱密钥、错误自签名或其他算法会拒绝。已存在输出拒绝覆盖。两根过渡 bundle 必须提供两个独立确认的指纹，以逗号分隔；少根、多根、重复根或少指纹均拒绝。单根接口保持兼容。

```bash
python3 tools/node/node_endpoint.py ca-import \
  --ca-file /root/old-new-ca-bundle.pem \
  --ca-fingerprint '<旧根SHA-256>,<新根SHA-256>' \
  --output /root/verified-dual-ca.pem
```

Go REST 和 gorilla WSS 使用系统根信任。本工具为自己的服务生成系统公有 CA bundle 加已验证私有根，通过服务内 `SSL_CERT_FILE` 使用；不修改整个系统信任库。自动迁移已有服务时，仅支持可验证的标准 Root/systemd unit；已有其他 `Environment`、`SSL_CERT_FILE`、容器或自定义信任配置会安全拒绝。新增 CA 合并到工具拥有的 bundle 中保留旧根，可以先加新根，再换面板证书。V1 不自动删除旧根：完成所有节点迁移后，维护窗口中人工核对该服务 bundle，按根指纹移除旧根并检查 REST/WSS；不要直接修改系统全局 CA store。

## 新建 Node

先确认目标 node/machine identity 尚未运行，并且没有另一处部署使用相同 identity，再在 SSH 会话中运行 `prepare`，交互隐藏输入 Token。命令本身没有 Token。`--confirm-new-identity UNUSED` 是这个事实的显式声明，工具无法远程自动证明 identity 独占。

```bash
python3 tools/node/node_endpoint.py prepare \
  --output-dir /etc/node-endpoint-node/node-3 \
  --panel-url https://192.0.2.10:8443 \
  --mode node --node-id 3 --node-type vmess \
  --kernel xray --architecture amd64 --confirm-new-identity UNUSED \
  --ca-file /root/verified-node-endpoint-ca.pem \
  --ca-fingerprint '<独立渠道确认的SHA-256>' --download
```

机器模式将身份参数换成 `--mode machine --machine-id 12`；内核可选 `singbox`；ARM64 使用 `--architecture arm64`。IPv6 URL 必须使用方括号，如 `https://[2001:db8::10]:8443`。示例地址为文档地址，需要替换为真实独立入口。

`prepare` 先完成真实认证 REST 和单独 WSS 预检，再创建新目录：`config.yml`、`credentials.env`、CA/trust bundle、随机唯一 service 草稿、`prepared.json` 和可选 hash-verified Node binary。**WSS 认证有业务副作用：官方 NodeWorker 会注册 NodeRegistry 并清理设备状态；不能对正在运行的生产 identity 执行该探测。** 敏感文件权限 0600、目录 0700。目标目录已存在时拒绝覆盖。没有 `--download` 可以审阅配置，但不能执行自动安装。

```bash
sudo python3 tools/node/node_endpoint.py install-new \
  --prepared-dir /etc/node-endpoint-node/node-3 --confirm-install NEW
```

`install-new` 只接受声明 identity 未运行且成功准备的新目录，复核文件及 binary hash、架构、service 不存在，持有准备目录的独立 Root-owned `flock`。启动前执行 WSS 认证；再写入 `/etc/systemd/system/node-endpoint-<随机ID>.service`，启动后只检查 REST/service health，避免探测连接抢占刚启动 Node 的业务 WSS。需要开机启动可显式加 `--enable`。不修改已有 `xboard-node.service`、小写 `node` 菜单或 `xbctl`。

新装失败会逐项尝试停止、disable、移除此次 owned unit 和 daemon-reload；停服失败时保留 unit 供人工恢复，不声称已经完整回滚。清理不完整会在私有准备目录写 `INSTALL_RECOVERY_REQUIRED`，禁止重试安装，需人工核查服务状态后恢复。准备失败有 `PREPARATION_FAILED` 时不会被安装；其中 CA 文件仅为未接入任何服务的私有 staging，保留供核查。

新 unit 支持 sing-box/Xray 配置生成。已审计 Node 的 geodata 业务代码另有上游 `latest` 数据文件 URL；本工具不修改 Node 业务代码、不把地理数据的来源/完整性描述为 binary pin 已涵盖的保护。若生产需要严格零浮动资源，请先独立提供该 Node 版本需要的数据文件并审计运行时行为。

## Token 输入

默认使用终端 `getpass`；非交互可以传 `--token-file /root/protected-token.txt`，文件须当前用户拥有、0600 或更严格；或使用 `--token-stdin` 从已保护的管道读一行。不要在 shell 命令中 `echo 实际Token`，也不要将长期 Token 放入 `--token` 参数。凭据写到独立 0600 `EnvironmentFile`，未写入 panel URL、脚本参数或工具输出。

**上游协议限制：** Node config/user 的 GET API 和 WS handshake 本身通过 URL query 传 Token。本工具的真实预检遵守这个既有协议，并且不打印 query/response body。部署端必须关闭节点 API 和 `/ws` 的含 query 访问日志。上游 `internal/panel/ws.go` 在 debug 级别打印完整 WS URL；已有 Node 开启 debug 时，自动迁移拒绝继续，需要先人工改为 info/warn 并检查日志、按需轮换已暴露凭据。本插件未修改该 Go 代码，也不能保证其他第三方代理/日志系统不会记录 query。

HTTP 仅显式 `--allow-http` 时接受；Token 和通信内容可能明文传输。TLS 出错不会降级为 HTTP。

## 真实连接预检

```bash
python3 tools/node/node_endpoint.py preflight \
  --panel-url https://192.0.2.10:8443 \
  --mode machine --machine-id 12 \
  --ca-file /root/verified-node-endpoint-ca.pem \
  --ca-fingerprint '<独立渠道确认的SHA-256>'
```

默认 CLI 预检执行 `POST /api/v2/server/handshake`；传统 node 分别检查 config/users；machine 检查 machine/nodes 和每个已发现绑定的 config/users。仅接受成功的预期 JSON 结构，不跟随重定向。HTTPS 检查链、根有效期、叶有效期和目标 IP/DNS SAN；最低 TLS 1.2。默认不建立认证 WSS 连接，返回 `websocket: not tested (REST-only phase; authenticated probe deferred)`，因此不会注册探测连接或清理设备状态。

握手启用 WS 时，先核查返回同一个独立 host/port 的 `/ws`、HTTPS 对应 WSS；等值 IP 的不同 IPv6 文本表示及默认 443 端口按地址语义比较。旧域名 WSS、其他端口、query/fragment/用户名或其他路径会拒绝，防止 Token 被送回旧入口。

只有独占目标 identity 已停止、管理员理解 NodeRegistry/设备清理副作用时，才能显式添加 `--confirm-ws-probe AUTHENTICATE_STOPPED_INSTANCE`，执行真实 TLS、HTTP 101、`Sec-WebSocket-Accept`、首条 `auth.success` 及返回 `data.node_id`/`data.machine_id` 与目标 identity 的一致性检查。例如在维护窗口中，停妥该独占实例后：

```bash
python3 tools/node/node_endpoint.py preflight \
  --panel-url https://192.0.2.10:8443 \
  --mode machine --machine-id 12 \
  --ca-file /root/verified-node-endpoint-ca.pem \
  --ca-fingerprint '<独立渠道确认的SHA-256>' \
  --confirm-ws-probe AUTHENTICATE_STOPPED_INSTANCE
```

返回 `websocket: disabled` 明确表示 REST-only，不能作为 WSS 验收。默认 WSS 失败就停止；管理员可以明确加 `--allow-rest-fallback`，允许 **目标正确的** WSS 失败后继续 REST。旧/跨域 WSS 即便此参数也拒绝，必须先修复握手或使用入口限定的 REST-only handshake。工具不全局改写 `server_ws_url`。

## 迁移已有 Node

先只读核查：

```bash
sudo python3 tools/node/node_endpoint.py inspect \
  --config /etc/xboard-node/config.yml --service xboard-node.service
```

核对实例 identity、原 panel URL、全部绑定、kernel/config_dir、service 当前活动及 enable 状态。service 显示的自动迁移标记只表示已知名称/路径，真正执行时还会严格验证 unit/env/binary ownership；不能将它视为所有检查已经完成。

```bash
sudo python3 tools/node/node_endpoint.py migrate \
  --config /etc/xboard-node/config.yml \
  --credentials /etc/xboard-node/credentials.env \
  --meta /etc/xboard-node/install-meta.json \
  --service xboard-node.service \
  --instance-id '<inspect的目标ID>' \
  --from-url 'https://原面板地址:443' \
  --panel-url https://192.0.2.10:8443 \
  --ca-file /root/verified-node-endpoint-ca.pem \
  --ca-fingerprint '<独立渠道确认的SHA-256>' \
  --confirm-migrate 'MIGRATE:<同一个目标ID>'
```

迁移须标准 installer 的 Root/systemd unit、显式 block YAML、可解析的 protected credentials 和一致 install metadata。工具严格比对审计的 unit 指令及唯一允许的 owned trust drop-in，拒绝业务/日志/信任环境变量 override。修改与恢复的文件祖先须 Root-owned、没有 group/world 写权限。整个读取、预检、备份、修改和后检持有独立 Root-owned `flock`，并在修改前重新核对 config/credentials/metadata 的 SHA-256 以及 service unit/binary 的来源摘要。

工具验证目标 identity 与原 URL 精确匹配，先对选中实例的每个 node binding 只做 REST/TLS 预检，备份 config/credentials/install metadata/原工具 trust/drop-in 和活动状态；随后停止目标进程，再在事务内、修改文件之前执行认证 WSS 探测，避免抢占还在运行的旧 Node。只替换选定 `panel.url` 标量，其余 YAML 字节（包括多绑定、凭据、注释和配置）保持原样；相应 install metadata 只更新该实例的 URL、派生 ID 和关联 latest-instance 引用，保留其他字段。

此流程需要目标 identity 由该实例独占；停止本机进程并不能证明另一处部署没有使用相同 machine/node identity。管理员必须先核实，工具无法自动证明远程独占。认证 WSS 仍会触发 NodeRegistry/设备状态操作，应在维护窗口中执行；不能称为只读业务操作。

Node 的运行 ID 由 panel URL + node/machine 目标 ID 派生。变更 URL 会改变这个本地派生标签，远程 `machine_id`、Token 和 bindings 不变。多实例如果没有显式 `kernel.config_dir`，Node 会基于派生 ID 生成新数据目录，工具因此拒绝迁移：应先单独核查并显式固定各实例当前数据路径，然后重新 inventory。工具不会擅自搬移/删除数据目录，也不会重新创建远程机器。

备份在 `/etc/xboard-node/.node-endpoint-backups/<UTC时间>-<随机ID>`，目录 0700、凭据副本 0600，记录每个原始备份文件 SHA-256。HTTPS trust bundle/drop-in 只属于该服务；原网站、FlowScope、binary 和 service enable 状态不改。原服务活动才重新启动；原来未活动会保持未活动。启动后只再次检查 REST/TLS；活动服务还检查 `systemctl is-active` 及配置声明的本地 `/healthz`，不再次建立认证 WSS 探测，防止抢占 Node 的新业务连接。此前 WSS 的 PASS 是停服期间的连接能力证明，不是已启动 Go Node 当前 WSS 连接状态；客户端真实代理流量、计量和持续 WSS 仍须单独验收。

失败恢复快照及原活动状态；恢复不完整会明确给出私有备份路径。显式回滚：

```bash
sudo python3 tools/node/node_endpoint.py rollback \
  --backup '/etc/xboard-node/.node-endpoint-backups/<目标备份>' \
  --confirm-rollback ROLLBACK
```

显式回滚持有同一配置锁，先验证标准 service、固定文件 allowlist、所有原始备份 SHA-256，以及当前 unit/binary 来源摘要和当前文件 SHA-256 等于该事务实际写入值；备份损坏或后续变更时，在停止服务/恢复任何文件之前拒绝，转为人工恢复。备份不能证明旧 Token 仍有效，恢复后仍须验证旧端点和真实流量。

重复迁移可以更改同一服务下一实例或入口；已存在工具 trust/drop-in 必须保持原工具格式，否则拒绝。追加新的独立验证 CA 会保留旧信任，实现双根过渡；同一 CA 换服务器私钥无需修改 Node trust。轮换过渡期间不要删除仍被其他 bindings 使用的根。

## 支持边界

- 自动新装：Linux Root、原生 systemd、AMD64/ARM64；Python3/OpenSSL/系统 CA bundle 均已存在。不会自动安装/升级系统软件。
- 自动迁移：审计的 `/etc/xboard-node/config.yml` + credentials + metadata 和 `xboard-node.service` 标准 unit；多实例、多 node 绑定、machine 动态绑定。
- 未自动支持：Docker、Kubernetes、环境变量 `apiHost/API_HOST` 覆盖、自定义 unit/用户/RootDirectory、Windows 服务、其他 Node lineage/config 格式、subpath panel URL、任意 YAML tags/anchors/aliases/merge、多行字符串或非空 flow collections。这些会安全拒绝，保留文件。
- 未实现：自动移除旧 CA/CRL/OCSP 吊销检查、迁移 FlowScope 独立 `panel_url`、修改 Go 内核、保证既有代理流量不中断。停止/重启应安排维护窗口。
- 不提供 Node 卸载命令，以避免把迁移回滚当作删节点/删流量；新建独立 unit 需由管理员核实绑定及停止服务后人工移除，保留私有目录备份。

## 固定 Go 的已知用户更新限制

实际联测确认 `v1.13-orphan.4` 的两项 Xray 限制：全量用户更新在同 ID UUID 更换时没有移除旧 UserManager 记录，添加新认证因 duplicate/already exists 失败；原生 NodeSyncService 的增量更新确已到达传统绑定，但移除唯一旧用户后内核停止，AddUsers 和 UpdateUsers fallback 报 not running，新认证失败。两个 machine/sing-box 绑定的 mailbox 全量事件亦有观测，但这轮没有完成全部四路认证变更验收。模型 save 的 UUID Observer 触发链另有未观测事件的失败，注册/用户可用性已核对但原因未定位。工具保持固定二进制，不修改 Go 内核。遇到 UUID 更换，应安排维护窗口、核对实际用户集并受控重启 Node，再分别验证新认证成功和旧认证拒绝；本插件不自动替生产服务执行该操作。失败与成功阶段见 FULL_E2E_TEST_REPORT。raw 空用户全量列表另有源码拒绝行为，本轮未作空用户集业务验收。

## 已执行与未执行测试

运行 `python3 tests/node/test_node_endpoint.py`：Windows 与 VM 最新均 36 项 PASS（2026-10-10）。包括本地真实 TLS sockets 的 IP REST/WSS、node/machine、多机器 binding 的 REST、错误 CA/过期叶/错误 SAN 拒绝、同 CA 换服务器私钥、两根指纹全部验证及两条真实链、匹配 pin 但根自签名篡改拒绝；独立准备 node/Xray 和 machine/singbox、实际 CLI protected stdin、不打印 Token、默认 CLI 不认证 WSS、不匹配 WSS identity 拒绝、旧 WSS 禁止、WSS auth proof、损坏下载/失败 staging 拒绝；block YAML 单字段字节保留、公开 inspect 不泄露嵌套证书凭据、环境 override/identity/original URL/隐式多实例目录拒绝、等值 IPv6 WSS authority。新增实际 REST 协议/内核不兼容拒绝。基础套件中的成功保留其他实例/metadata、失败恢复原状态、并发凭据变更拒绝、损坏备份禁止恢复、新装清理停服失败证据使用 **模拟 systemd**；下述 VM Go 测试另使用真实 systemd。

另外实际下载 v1.13-orphan.4 的两个 Node release binary，SHA-256 与 ELF AMD64/ARM64 均验证通过；`SHA256SUMS` 实际下载通过自身 SHA 并逐项匹配。AMD64 在专用 Ubuntu 24.04.3 VM 实际执行，最新 Go/systemd 套件 11 项 PASS：install-new、node / Xray / VLESS、machine / sing-box / SOCKS5、持续认证 WSS、内核 loopback 代理、同 CA 换钥、双 CA 新链、标准两实例 Xray / 多bindings选中URL迁移及真实CLI rollback。其他实例URL/token/binary/meta/sentinel保持，测试资源清理和原Docker/FlowScope保持通过。测试用 prepare 无下载路径，再独立核对摘要并 stage 已上传的资产；没有将这个流程当作 prepare downloader 已执行。ARM64 尚未执行。

VM 另完成真实 Linux UID/mode/links 和独立进程 flock 14 项检查。上述历史 Go 套件使用合成 Python fixture，PHP 原生组件套件独立运行。后续完整两 VM 实际 Xboard 数据库/Redis/队列/原版 NodeWorker 与 Go 测试已完成，成功路径与上游限制分列，详见 [FULL_E2E_TEST_REPORT.md](../FULL_E2E_TEST_REPORT.md)，包括两个身份/内核、四绑定、Admin HTTP 属性更新、设备清理、CA 过渡、新装及 CLI 迁移/回滚。原始 sing-box 仍监听 `::`；仅自己的随机 fixture unit 加 `IPAddressDeny=any` / `IPAddressAllow=localhost`，实际拒绝 IPv4 非loopback连接，不改变生产默认策略或原始内核。完整证据和前几轮失败记录见 [VM_TEST_REPORT.md](../VM_TEST_REPORT.md)。

新装协议约束：Xray 拒绝 tuic / anytls；`hysteria2` 是合法 auth alias，命令规范为 `hysteria`。实际 REST wire 配置另行检查：Xray hysteria 仅接受 version 2；此 sing-box builder 对 wire `hysteria2` 无 inbound，合法 Hy2 使用 protocol=`hysteria` / version 2。prepare 检查每个 machine binding，在认证 WSS 与创建 stage 前拒绝不兼容配置；迁移不更换内核。

**NOT TESTED：** Octane/Docker/其他宝塔或 OpenResty 应用布局、原生 ARM64、真实订阅客户端、公网/IPv6、旧域名实际 DNS 故障下持续代理流量和生产流量/计量连续性；Independent `403604e` overlay 未测。真实 Linux 已验证的进程与事务以 VM_TEST_REPORT 和 FULL_E2E_TEST_REPORT 所列范围为限，不能扩展为所有协议组合或停服失败/cgroup/enable 异常均覆盖。
