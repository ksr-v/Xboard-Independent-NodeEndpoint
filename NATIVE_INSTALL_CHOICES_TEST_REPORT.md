# NodeEndpoint 1.0.2 安装方式双选验收

日期：2026-10-17。改动范围为原生机器管理的命令选择与动态页面适配，Node 安装器、Go 二进制、Root 入口和 PKI 均沿用已发布版本。

## 结果

- Windows PHP 8.2 / Ubuntu VM PHP 8.3、Laravel 12.69.3：原生组件 34 项分别通过。默认命令完整保留原版域名方式，附加独立 IP/CA 选项；验证管理员权限、单双 CA、无/坏 CA、HTTP、禁用入口、创建唯一性、原生校验错误、更新、Token 重置及 1.0.1 → 1.0.2 升级保留数据。
- 两平台动态 Admin 页面集成各 6 项通过：资源与状态门控、composer 去重、禁用恢复、独立页面不被注入，以及 FlowScope 两种 composer 顺序共存。原 Admin bundle 和 Root 归属证据保持。
- 实际审计 Admin bundle + Chrome：14 项浏览器检查通过。命令 GET、新建机器 POST、插件状态与配置来自隔离 Laravel/Sanctum SQLite fixture；仅用户展示信息、插件列表、机器列表/节点列表/历史图表数据使用合成 mock。

## 浏览器覆盖

1. 原生详情显示两个选项，默认原版域名；原有角标及底部两个复制按钮一并隐藏。
2. 域名命令与原生响应一致，IP 命令与附加选项逐字一致；切换不自动写入剪贴板，切回不会复制旧选项。
3. 打开的卡片超过非活动缓存期限仍保持；重复打开原生缓存详情和切换机器不会丢选项或复用错误身份。
4. 真实 XHR 与 fetch 响应均只观察、不修改；新建机器后的 Token 弹窗也能选择，Token 区块及其复制按钮保留。
5. 移动端 390px 视口无卡片横向溢出；响应式抽屉重建后保留选择。
6. 独立入口未就绪时，仅禁用 IP 选项并说明原因，域名仍可复制。
7. 未授权响应及不触发 hashchange/storage 的 SPA sign-in 导航清理内存命令；新的授权响应可恢复选择。
8. 插件禁用后清理卡片和隐藏标记，恢复原生复制控件；页面无浏览器运行错误。

## 实现与维护边界

通过原生 Admin view composer 动态加载插件资源，不修改 Xboard 核心、Admin 模板文件或编译 bundle。仅对有 owner/schema 标记、原生命令完全匹配的已授权响应适配已审计的命令容器。UI 不读取认证 storage 或 React Fiber，也不替换浏览器 clipboard 实现。命令仅在当前页面内存，最多 20 条；非活动缓存 10 分钟，以兼容原生 React Query 的缓存重开。普通 SPA 导航保留缓存，退出、认证拒绝、页面离开或插件禁用清理。

通讯地址、CA 或 Token 改变后应刷新管理页面，清除原生查询缓存。非匹配的 Admin DOM 保留原生显示，不保证任意第三方主题/不同构建的双选适配。浏览器本轮未启用 FlowScope 的 JS 运行时；其 PHP 动态 wrapper 两个顺序、资源字节及既有三插件生命周期单独验证。

本轮不执行真实生产部署或节点安装，不重复声明代理转发、流量计量、WSS/CA 轮换已测。既有真实两 VM IP HTTPS/WSS/安装器证据见 NATIVE_MACHINE_TEST_REPORT.md；固定 Go 的 UUID/Observer 限制见 FULL_E2E_TEST_REPORT.md，继续保留。发布 ZIP 的摘要及原生上传验包结果随 Release 的 JSON 提供。
