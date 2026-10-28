# NodeEndpoint 1.0.10 联动验证

日期：2026-10-28。基线为独立仓库 `230361a25277a0b273a78ed4c5e8e306bc8649a5`；插件五文件更新提交为 `a600680392dc97738594f5c07f1d5cb032b433f6`。插件源码共25文件，已验证工作目录原始字节与该提交的 Git blob 一致，完整 SHA-256 清单供配套面板 bundle 锁定。

本次修改范围为插件版本元数据、域名／IP 新机器安装器和固定 Node v7 版本；Node 工具只更新正式资产锁，CLI 保守迁移实现保持原版。Root 引导、`quick_install.py` 和网页 Root 命令分别与基线逐字节比较相同，继续固定 `root-installer-v1.0.9 / tools 1.0.6`。

已执行以下命令生成检查，Windows PHP 8.2 和开发 VM 的 PHP 8.3.6 各4项通过：固定 v7 Release 安装器与对应参数、合成特殊字符 Token 转义、IP 命令 CA 和双指纹保留、无效机器身份拒绝。检查不会运行 shell 命令或安装器；全插件19个 PHP 文件语法检查通过。

```bash
php tests/integration/release_commands.php
```

Node 工具回归与发布包验证命令：

```bash
python3 tests/node/test_node_endpoint.py
python3 tools/build/build.py --plugin-only --with-tools
python3 tools/build/verify.py --plugin-only --with-tools
python3 tools/build/verify_linkage.py
```

工具包构建只读取原始 `node-endpoint-tools-1.0.0.zip`，必须先匹配历史固定 SHA-256 `42f2893938c294646c44e351a5c251cfea9f6a30156d67c345bc476307ea89e2`。新版原生插件和补充工具分别输出到 `dist/v1.0.10/`，不覆盖旧版包。发布包检查包含归档 CRC、路径安全、私钥／运行目录排除、全部成员 manifest、源文件一致性，以及两包中的 Node 发行锁和审计清单一致性。

Node 工具回归为 36/36，通过的对象为本次固定 v7 资产锁。正式 Node 的发行源码提交锁定 `b8b791ffb66954c78b0e7036667240b3c093a87e`，二进制构建源码提交为 `fd5cacf2deba18c047f4ae606bf1259220ee7592`；两提交间没有 Go、模块锁或核心补丁清单变化。`tools/node/release-lock.json` 与审计清单固定四个架构资产、安装器和 SHA256SUMS 共六个资产的摘要，已逐一与本地正式资产比较一致。该锁定与 Node Release 的实际发布验证分开记录，不把候选 `dbd6...` 二进制摘要当作正式 v7 摘要。

工具测试在隔离 VM 以普通用户运行时，6 个模拟事务测试因需要 Root 归属的 `/var/lib` 自有临时目录而跳过；随后以 `sudo -n python3 tests/node/test_node_endpoint.py` 完整重跑，36 项全部通过、0 失败、0 跳过，耗时 13.264 秒。真实 TLS 测试通过，服务事务仍为模拟，不操作真实 systemd 服务。两份新包分别有 129 和 95 个成员（含各自 manifest），CRC、安全路径、成员摘要和 Node 锁联动检查通过。插件 ZIP 的 25 个业务源文件另按原始字节逐项核对配套面板 `a600680...` 的捆绑锁，避免换行规范化掩盖安装源差异；最终包摘要见同目录 `SHA256SUMS` 与 `release.json`，详细结果记录于 `dist/verification/device-limit-release-20261028.json`。

本轮没有重跑完整 Laravel 原生插件安装／上传组件套件、浏览器、Root 部署或生产业务。1.0.9 的36组件／9 Root 命令结果属于历史证据，不作为本版重跑结果。配套面板与 Node 的设备限制业务测试和局限另见[联动说明](DEVICE_LIMIT_RELEASE_20261028.md)及其引用的面板验收记录。
