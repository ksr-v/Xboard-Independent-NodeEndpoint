# NodeEndpoint 1.0.0 开发路线与交付边界

## A：源码审计

已只读核对六个指定仓库、记录 exact commits，确认原生插件生命周期、Sanctum、Admin 资源和共享锁、Node 安装器、多绑定及 handshake/WS 协议。Independent 最新 overlay PHP 源不在提供的资料仓库中，保留明确兼容验收限制。

## B–C：插件与权限设计、后端和页面

已实现原生插件包、自有设置表、地址/端口/HTTPS 验证、HTTP 明文确认、独立管理页、原生管理员 API、CA 导入/导出、公共状态快照、无凭据连通性 probe、IP 检测和命令生成。采用每请求作用域 middleware 处理新入口 handshake；不改核心 Controller、Admin bundle 或全局设置。

## D：私有 PKI 与 Root 工具

已实现双 10 年证书、正确扩展、显式时间与 CA 上限、证书元数据、离线 CA、主动/应急重签及双 CA 轮换。入口工具检测实际 Nginx/include/监听/应用路由/后端、建立独立监听、备份、配置验证、reload、回滚和单独卸载。系统和部署识别不明确时拒绝执行。

## E：Node 对接和迁移

已实现独立 Node 固定版本/摘要、node/machine、sing-box/Xray、两种 CPU 架构的新实例准备，独立指纹验证和 REST/WSS 预检，已审计 systemd 部署的严格多实例迁移及回滚。不会自动更改现有 Node，不把地址变更与二进制升级绑定。

V1 对 Docker、自定义 unit、环境覆盖、复杂 YAML、无法证明稳定数据目录的多实例采取安全拒绝。这些环境需要后续有真实 fixture 的适配器，不能简单回退到上游重装。

## F–G：测试与打包

实际结果列于 TEST_REPORT 和 VM_TEST_REPORT，源码审计、Laravel 组件、真实 OpenSSL、TLS / HTTP / WSS fixture 与真实 Linux 进程分别报告。专用 Ubuntu VM 已执行真实 Nginx、UID/mode/flock、Root CLI / 独立 PHP-FPM 及原生组件测试；真实 AMD64 Go Node 在合成面板上完成新装、双内核、WSS、换证、标准多实例迁移与 CLI 回滚的 11 项检查，协议组合修复后 Node / PHP 回归为 36 / 26 项。打包生成唯一原生插件目录 ZIP、独立工具包、源码包、版本与摘要，最终包另经原生 upload/install/enable 复测。生产部署验收门槛由 PRODUCTION_DEPLOYMENT 定义。

后续完整两 VM 原版 Xboard / NodeWorker / Go 联测与最终清理已完成（已测冷启动认证及 Admin HTTP 属性更新通过，上游全量及单用户增量 UUID 限制另列），见 [FULL_E2E_TEST_REPORT.md](FULL_E2E_TEST_REPORT.md)。实际宝塔 Nginx/PHP-FPM 环境发现的多 master 与 quoted brace 解析问题已修复；Root Windows/Linux 回归各 36 项。

## 仍需实机验收

继续验收实际 Independent overlay，以及 Octane / Docker / 其他宝塔与 OpenResty 布局、ARM64、公网 / IPv6 和原域名实际 DNS 故障下的长期运行。已有专用 Linux VM 结果不能替代这些目标环境测试。完成目标部署与业务验收后再分批生产切换，保存可追溯的服务器版本、配置摘要和结果。当前未向任何远程仓库或生产服务器推送/部署。
