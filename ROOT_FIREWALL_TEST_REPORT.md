# Root 防火墙一体化验证

验证日期：2026-10-17。发布入口为 `root-installer-v1.0.4`，固定工具包为 `node-endpoint-tools-1.0.3.zip`。面板插件和 Node 安装器保持既有版本。

## 实现范围

标准 PHP-FPM Root 向导在独立入口安装成功后，从已提交且归属可信的安装记录取得端口，配置本机防火墙，再尝试同机公共 CA 导入。任一后续步骤失败均单独报告，以非零退出；不重建已安装的入口或 PKI。`--configure-firewall` 单独处理已有入口端口，`--sync-ca` 单独导入 CA，`--detect-only` 保持部署检测只读，`--skip-firewall` 供自管规则使用。

firewalld 对默认和活动 zone 分别核对、补齐和复查 runtime / permanent 端口规则，不整体 reload。UFW 保存入站 TCP 允许规则；未启用时仅保存并明确报告。双管理器冲突、未知策略或无法确认的既有规则优先级返回需要人工处理。操作失败包含部分应用标记，不把本机配置结果当作公网可达证据。

已有入口的补规则操作校验 Root 状态目录及文件、独占 0600 管理锁、状态 marker、安装计划与配置归属、配置摘要和 listen 端口。它不进入入口修改、应用 PHP 或证书生成流程。

## 自动测试

Windows 和授权的 Ubuntu 测试虚拟机分别通过 **98 项**面板工具测试：

```bash
python3 -m unittest discover -s tests/panel -p 'test_*.py'
```

其中新增防火墙模块的 **29 项**测试使用命令替身，覆盖默认 / 活动 zone、运行时 / 永久规则、幂等、双管理器冲突、UFW 活动 / 未启用、规则优先级、权限、超时及部分失败；这些场景不会操作运行测试的主机防火墙。引导测试同时覆盖工具包防火墙成员缺失拒绝、无 TTY 的专用补规则分支、模式互斥、失败退出，以及防火墙失败仍尝试 CA 导入。

## 真实 UFW 隔离验证

授权 Ubuntu 虚拟机运行：

```bash
sudo python3 tests/panel/firewall_linux_integration.py
```

测试使用独立 mount / network namespace，将 UFW 配置副本和独立 `/run` 绑定到隔离环境，拒绝连接共享 firewalld daemon。真实结果通过：

- TCP 8443 的 IPv4 / IPv6 入站允许规则生效；重复执行保持每个协议族一条规则。
- UFW reload 后规则保留；原 SSH 22、自有测试端口及默认策略原样保留。
- `configure_firewall` 从可信状态使用自定义端口 18443，修改安装记录中的端口而未同步 listen 时拒绝操作。
- 单独补防火墙前后，PKI 和 Nginx 配置的字节、权限、属主及修改时间一致。
- 隔离环境外的宿主 UFW 配置及 IPv4 / IPv6 规则前后相同；比较排除正常流量计数及生成时间戳。

成功后隔离测试资源已清理。测试不开放宿主生产端口，不改动生产面板。

## 验证边界

本轮真实防火墙验证为 UFW；firewalld 分支使用命令替身验证。未重新执行整套空机 Xboard 安装、完整 Root 交互安装、NodeWorker / 代理业务或 ARM64 联测，既有 CA 导入及节点功能结果见历史报告。本机工具无法配置或证明云安全组、服务商防火墙及公网回程连通性。历史 UUID / Observer 限制继续保留。

独立安装器、工具 ZIP 和本轮结果 JSON 随 Root Release 发布；工具 ZIP 包含更新后的操作文档，排除会对自身 ZIP 形成循环摘要的独立引导程序。
