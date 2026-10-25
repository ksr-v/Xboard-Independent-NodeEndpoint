# v6 / 全插件2026.10.25.2发行验证

本次配套版本：Node `v1.13-orphan.6`、全插件后端 `v2026.10.25.2`、NodeEndpoint `1.0.9`、独立迁移工具 `1.0.8`。Root入口仍为 `root-installer-v1.0.9 / tools1.0.6`。二维码插件固定1.2.0审计副本，SmartExpiry1.2.0和FlowScope2.4.0不变。

三个仓库的正式Release已发布并设为latest；33项公开下载资产逐SHA核对通过，Node核心清单再次通过安装器pin及ELF架构校验。没有覆盖旧tag或资产，没有执行公网Root更新器。发布后的实际URL、tag、摘要及CI状态快照见[发布记录](verification/node-menu-publication-20261025.json)。CI在记录时仍进行中，不能表述为全部通过。

## 已验证的功能与资产

- 自动UDP转发候选源码47/47真实检查：两内核QUIC/TLS及持续跳跃TCP/UDP字节校验、真实面板/NodeWorker配置更新、规则撤销、权限和冲突拒绝、退出清理。后端12项新增检查、原13项限速/路由回归通过。见[自动转发报告](HYSTERIA2_AUTO_FORWARDING_REPORT.md)。
- 菜单候选源码13/13真实PTY/systemd/Node/SQLite面板检查通过；五个相关Go包生产tags Linux `-race`通过，Windows四包通过。正式发行没有改变这些Go业务源码。见[菜单报告](NODE_MANAGEMENT_TEST_REPORT.md)。
- 正式v6资产17/17检查：13项真实PTY/systemd/SQLite面板管理和4个正式程序版本smoke，amd64原生、arm64 QEMU。在线更新实际执行同版正式v6安装器和程序，下载运输为受控Release，非公网Root更新执行。绑定、健康端口和CA保留，篡改被拒，失败恢复及权限通过。完整结果见[正式VM证据](verification/node-v6-release-20261025.json)。
- v6脚本检查：原安装器26项中25通过、1项外部分发镜像路径缺失跳过；新增管理菜单7项、资产验证5项通过。组件检查：NodeEndpoint36项、Root复制命令9项通过。全插件后端14项插件集成检查通过，包括真实旧1.0.8升级和操作员文件保留。
- 正式安装器固定四二进制SHA；Release核心`SHA256SUMS`精确列出四ELF及安装器，附加文档/证据由GitHub资产摘要和外层发行记录核对。后端归档含锁定四插件源码和保留数据升级器。

Node二进制源码为 `34c524e50977ec0bbc60ce46f4a30ad0af76a0d1`，Go构建使用生产tags、`-trimpath -buildvcs=false`及固定版本/构建时间；依赖、来源和License保留。正式tag随后只增加固定pin、发行文档和CI菜单测试，不改变已验收Go业务源码。

正式测试服务和容器已移除，测试端口释放，宿主测试NAT表为空，原面板HTTP200；未读取Root私有日志或既有终端记录。按[升级教程](UPGRADE.zh-CN.md)先后端再Node，首次固定版本`upgrade`后可用`node`选择1更新；已有配置、身份和CA保留。新机器使用面板复制安装命令，无需卸载其他插件或重复配置Root入口。

## 尚未验证

公网正式Root更新执行、原生ARM64代理业务、生产迁移连续性、跨公网NAT、实际IPv6流量、长期压力、新Node Docker镜像运行，以及新完整后端包的全套原生/Docker升级重演。当前发布结合既有部署器、组件/插件升级检查和专项功能联测，不把未执行的完整部署表述为通过。部署时先在备份副本和一台节点演练。

源码/资产摘要、清理状态及最终发布结果由同次Release的 `VERIFICATION.json` 与仓库发行证据提供。本地通过不等于远端Actions通过，应查看相应提交的实际状态。未访问或替换生产服务器。
