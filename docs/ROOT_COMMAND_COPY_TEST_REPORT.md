# Root 命令一键复制

日期：2026-10-18。插件版本：NodeEndpoint 1.0.5。继续使用已发布的 root-installer-v1.0.6 / tools 1.0.4；本次没有修改 Root 工具或 CA 存储。

保存节点通讯 IP 后，页面显示“SSH 配置入口”、已保存目标、完整 Root 命令和一键复制按钮。命令固定下载入口，预填规范化后的已保存主机地址；管理员仍需在 SSH 中选择站点 / PHP、核对端口和 WS 模式并确认操作。标准向导支持首次安装及已有入口的 modify，因此适用于原版面板和独立后端，不依赖独立后端专用的地址修复命令。

Web 只生成文本，不执行 Shell。无效、未启用、HTTP、80/443 或文档示例 IP 不生成命令，并解释原因。表单有未保存修改时隐藏旧可执行命令并禁用复制，失败的保存不生成新命令；保存 POST 或刷新 GET 期间继续编辑也保留新输入并要求再次保存。剪贴板不可用时保留可手工复制的命令。

## 已验证

| 范围 | 结果 |
| --- | --- |
| Root 命令真实 PHP / Admin API | 9 项通过，涵盖已保存值、规范化、权限、注入、示例网段、无效保存和不可用状态 |
| Root 命令真实 Chrome 行为 | 9 项通过，实际剪贴板复制、编辑与失败失效、POST / GET 并发编辑、手工复制回退、手机布局、401 清理 |
| CA 口令页面回归 | Chrome 7 项通过，显示 / 复制、清空和授权行为保持 |
| 原生插件回归 | 36 项通过，包含原生生命周期、升级保留、通讯接口及其他插件共存 |

Chrome 使用实际插件 HTML / JS / CSS，HTTPS 来源和 API 使用模拟响应；权限、持久化和生成命令由独立 Laravel / SQLite / Sanctum 集成检查验证。测试凭据均为合成数据，未接触生产。

本次没有重复执行未修改的 Root 工具 Linux 换证 / 回滚联测，其固定版本验证见 [公网 IP 修复记录](PUBLIC_IP_TEST_REPORT.md)。没有执行生产命令、迁移现有节点、完整 Docker / Octane、MySQL / PostgreSQL 或客户端代理流量验证。

## 复现

```bash
php tests/integration/root_command.php
php tests/integration/run.php
node tests/ui/root-command.cjs
node tests/ui/ca-passphrase.cjs
```

需要项目已准备的隔离 PHP 依赖、扩展、playwright-core 和 Chrome；应用测试不指向生产数据库。
