# NodeEndpoint 1.1.0 验证记录

日期：2026-10-29。Root 引导 `root-installer-v1.0.10`，固定 tools `1.0.6`，受限助手 `1.0.0`。所有凭据和 Root 私有材料保留在隔离测试目录，不随报告发布。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| Linux Python 面板工具 | 239/239，无跳过 | 原有工具全套，含助手 33 项、bootstrap 4 项；Root 系统副作用受隔离约束 |
| PHP Web 桥接 | 27 组通过 | 真实 Laravel、Sanctum、Admin、SQLite；Root 传输模拟 |
| Chrome 前端 | 27 组通过 | 11 组登录/Root、9 组命令、7 组 CA；HTTPS 与接口响应模拟 |
| 原生插件组件 | 36 项通过 | PHP 8.2.33、Laravel 12.69.3、Sanctum、隔离 SQLite |
| SSH 命令原生接口 | 9 项通过 | 已保存设置、地址规范化、注入拒绝和管理员限制 |
| 原生 ZIP 上传、安装、启用 | 6 项通过 | 逐个验证上传字节，保留其他插件、后台资源与状态 |
| 真实网页 Root 动作 | 45/45 | PHP-FPM → Unix socket → Root → Nginx/PKI/防火墙；真实 HTTPS API |

## 原生初始化与真实动作

在独立 Linux 测试站运行原始 `init.sh`，以非 Root `www` 用户完成 Composer、数据库和五插件安装启用，再由 Root 初始化入口、安装并启用助手、同步公共 CA 和处理本机端口。测试使用独立 MariaDB 数据库、独立 Redis 实例和复制出的 Nginx 二进制/独立 master；挂载命名空间将 Root 状态隔离到专用目录。

网站测试地址为 `https://web-manager.test:18444`，仅绑定 loopback；通讯入口测试端口 `18443` 绑定 IPv4/IPv6 全地址，运行在隔离 VM 内。本轮不代表公网或云安全组可达。

网页动作将通讯地址从 `127.0.0.1` 改为 `127.0.0.2`，验证新 SAN、旧 SAN 拒绝、CA 指纹保持不变；刷新路由保持叶证书，续签更换叶证书；重复执行返回同一任务，不重复签发。四类任务均完成入口、CA 同步和端口规则各阶段。负例覆盖管理员任务隔离、普通用户/无凭据拒绝、HTTPS 与精确同源、设置变更、完整确认、端口白名单，以及路径/命令/私钥导出请求拒绝。公共证据见 [native-actions.json](verification/web-manager-native-actions.json)。

真实测试曾复现跨进程数据库锁等待：网页持有设置行锁，Root 的固定 PHP 读取工具等待同一行，使计划接口在约 10.48 秒后返回 504。最终实现以短事务读取/复核设置，Root RPC 在数据库事务之外执行；Root 仍在变更前检查计划与状态摘要。修复后真实流程通过，并加入“每个 Root RPC 的事务层级必须为零”和计划期间设置变更的回归。

## 发行与边界

插件 ZIP 从 Git 原始 LF 字节通过项目构建器生成，逐文件核对 28 个原生插件源文件；ZIP 内成员清单、CRC、摘要、路径及私钥扫描均须通过。发行附件 `source-verification.json` 给出实际提交和 ZIP 摘要。

本轮未重跑 Docker/Octane 助手部署、真实 Node WSS、外部代理内核业务、ARM64、公网 NAT/IPv6 路径或长期压力测试。前端模拟检查不代替上述真实 API 动作，真实 API 动作也不代表已在生产服务器部署。旧 CA 私钥/轮换与任意系统命令继续由 SSH 管理。
