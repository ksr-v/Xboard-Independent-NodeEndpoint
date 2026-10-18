# CA 口令托管验证记录

日期：2026-10-18。版本：NodeEndpoint 1.0.4 / tools 1.0.4 / root-installer-v1.0.5。

已验证：Windows Root 工具单元检查 106 项通过（使用真实 OpenSSL）；原生 Laravel / Sanctum / SQLite 插件回归 36 项通过；独立口令回归 16 项通过；Chrome 浏览器行为 7 项通过。

口令回归使用实际 Laravel Crypt 和 SQLite，覆盖随机口令、密文落库、幂等准备、CA 绑定冲突、原 APP_KEY、篡改拒绝、HTTPS 管理员权限、当前信任指纹、独立限流、no-store、插件禁用保留，以及普通设置、命令和日志不含口令。Root 状态恢复检查使用隔离的协议 fixture，验证先保存 ID、准备失败重试、绑定失败恢复、双 CA 分离、旧 CA 不生成替代口令和失败不输出凭据。

浏览器检查使用真实插件 HTML / JS / CSS，HTTPS 页面与 API 由测试 mock 提供；验证按需查看、复制、隐藏、60 秒清空、页面离开清空、鉴权失效清空和移动布局。该测试不等于生产 TLS、生产登录或服务商网络验证。

真实 Linux Root / PHP 非 Root 管道、完整新装签发、加密口令取回及最终发行包验收将在隔离测试虚拟机执行，结果在发布前补齐。未访问生产面板。

数据库必须和原 `.env` / APP_KEY 一起备份。CA 私钥仍需单独加密导出、核对并离线保存。禁用、卸载及普通恢复数据清理保留凭据表；删除前需单独核对恢复材料。

Docker / Octane 全量部署、MySQL / PostgreSQL 完整安装、生产灾备及客户端流量验收未在本轮运行。
