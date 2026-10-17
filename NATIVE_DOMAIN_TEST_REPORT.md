# NodeEndpoint 1.0.3 独立域名安装命令验收

日期：2026-10-17。本次将默认域名命令也改用 ksr-v 独立部署源；原面板地址、机器身份和 IP 私有 CA 方式保留。不修改 Xboard 核心、Admin bundle、Root PKI、节点安装器或 Go 二进制。

## 检查结果

- Windows PHP 8.2 / Ubuntu VM PHP 8.3、Laravel 12.69.3 原生组件各 36 项通过。新增域名来源、固定版本、无私有 CA 参数、app_url 尾斜线 / 空值回退 / 非默认端口、特殊字符 Token 转义检查；既有权限、缺少 CA、HTTP / 独立入口禁用、机器创建、Token 重置、验证错误和生命周期回归保留。
- 两平台的动态 Admin 视图及 FlowScope 两种 composer 顺序各 6 项通过，原 Admin bundle 字节保持。
- 实际审计 Admin bundle + Chrome 的 14 项通过。默认域名选项显示“域名对接（独立部署）”，复制独立脚本且保留网站地址；新建机器弹窗同时验证域名和 IP 两种命令。缓存重开、机器切换、手机布局、认证 / 退出清理和插件禁用恢复保留。
- 升级检查覆盖 1.0.2 → 1.0.3，保留设置、公共 CA、机器 Token 和启用状态。最终原生 ZIP 上传安装及成员清单结果见随 Release 的 JSON。

## 命令差异

两种方式均使用固定安装脚本：

    https://raw.githubusercontent.com/ksr-v/Xboard-Independent/nodeendpoint-native-v1.0.0/node-installer/install.sh

并明确传入 version v1.13-orphan.4。域名使用原 app_url，未配置时按宿主行为回退请求 origin；不传入 endpoint-ca 参数，默认使用系统 / 公有 CA 信任。IP 使用插件配置的地址，并嵌入私有 CA 的公共证书与指纹。两者共享原机器 ID 和当前 Token，默认 command/install_command 与 domain 选项一致。

独立入口或 CA 未就绪不影响域名方式。只有禁用 NodeEndpoint 插件本身时，才恢复宿主原生命令行为。域名无 CA 新装仍下载独立仓库的固定 Node/xbctl Release；现有安装器仅在私有 CA 分支启用额外二进制 SHA 校验，本次不扩大该声明。

## 范围

API 使用隔离 Laravel/Sanctum SQLite fixture。浏览器仅用户展示、插件 / 机器 / 节点列表及历史图表数据为合成 mock，命令与创建请求走真实原生控制器；截图和合成凭据不发布。测试服务器仅监听 loopback，测试结束停止。

本轮验证命令生成与复制，不重跑真实节点安装、代理、计量、WSS 或 CA 轮换，不操作生产。既有 Node 安装 / IP HTTPS / WSS 证据见 NATIVE_MACHINE_TEST_REPORT.md，Go UUID / Observer 历史限制见 FULL_E2E_TEST_REPORT.md。非审计主题 / Admin 构建适配边界及缓存维护见 NATIVE_INSTALL_CHOICES_TEST_REPORT.md。
