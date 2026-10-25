# Node 管理菜单实验（2026-10-25）

这是专用测试虚拟机上的审计夹具，不是用户安装入口。最终结果见 [测试报告](../../../docs/NODE_MANAGEMENT_TEST_REPORT.md) 和 [脱敏 JSON](../../../docs/verification/node-management-20261025.json)。不包含实际凭据、证书私钥、原始终端输出、运行日志或二进制。

## 文件与前提

- `vm_menu_fixture_final.py`：本轮最终使用的13项 PTY／systemd／真实 Node／后端测试。固定 `/opt/xboard-node-menu-20261025-r3`，拒绝已有路径和单元；测试单元唯一命名。
- `linux_source_verify_final.py`：固定源码归档摘要，在离线模块缓存中执行五包生产 tags `go test -race`。
- `final_guard.py`：只读核对三轮本次服务、端口、容器和原面板健康。
- `export_source.py`：Windows工作区生成源码快照的原始辅助脚本；不会上传秘密或访问远端。

VM 使用 Linux amd64、Root／sudo、systemd、Python3、nftables、Docker。依赖前次 [HY2自动转发实验](../hy2_auto/README.md) 已归属标记的测试面板及离线Go模块缓存；不能对未经核对的生产目录运行。各夹具读取自己的 owner JSON 后才操作。

该面板隔离于原面板：`codex-xboard-hy2-auto-panel-20261025`，SQLite，loopback17006，测试machine与用户身份只在Root私有目录中。开始前用同目录 `panel_fixture.py start` 启动，重载 `panel_seed.php`，执行 `seed disable`，将HY2跳跃设为单监听42443，保证本轮不会创建宿主转发。

## 测试资产

上传目录 `/tmp/codex-node-menu-upload-20261025` 必须为测试用户私有目录。放入以下文件，并使用证据JSON中的最终 `runtime.candidate` 内容写入 `manifest.json`：

```text
xboard-node-linux-amd64
xbctl-linux-amd64
updated-xboard-node-linux-amd64
updated-xbctl-linux-amd64
install.sh
vm_menu_fixture_final.py
```

二进制来自独立 Node 源码，Node使用既有生产 tags；候选版本字符串 `node-menu-candidate`，更新样本字符串 `v9.99.0`。测试更新器只在夹具内适配为这两个固定摘要，不向GitHub创建Release或tag，不修改公开版本的二进制pin。不要将夹具适配后的安装器部署到用户节点。

```bash
sudo python3 -I -B /tmp/codex-node-menu-upload-20261025/vm_menu_fixture_final.py prepare
sudo python3 -I -B /tmp/codex-node-menu-upload-20261025/vm_menu_fixture_final.py run
sudo python3 -I -B /tmp/codex-node-menu-upload-20261025/vm_menu_fixture_final.py cleanup
```

`run` 启动真实PTY输入隐藏Token，原始输出只写Root私有目录；公开结果只有预设布尔值和公共文件摘要。不要读取或提交私有终端记录。`journalctl` 被合成哨兵替代，`curl` 只为本次固定Release提供本地资产，apt-get不执行系统安装。通过真实Node进程、真实backend机器API及WS检查绑定；不是模拟后端响应。

升级样本由完整安装器执行 `perform_upgrade`，真实替换Node／xbctl和菜单；更改路径只限自有测试目录、单元和命令路径。测试通过后停止并移除单元，再清理本轮面板容器，保留私有测试材料。`final_guard.py` 审计三次实际测试的固定路径；新环境复现时需相应提供归属明确的前两轮记录，不能伪造owner绕过检查。

源码检查另需 `node-source.tar.gz`、`source-metadata.json`，包含 `source_archive_sha256` 和 `source_files`；使用既有 `/opt/xboard-rate-route-linux-fix-20261024` 离线缓存及Go1.26.5。核对快照后再运行 `linux_source_verify_final.py`。导出的175文件快照含当时文档，测试后仅补充文档；受测Go与Shell业务源码保持一致。

公网正式更新、ARM64运行和生产迁移未在本轮执行；结果不代表这些环境已验收。

## 正式v6资产复核

后续正式字节另以 `formal_vm_release_fixture.py` 和 `formal_final_guard.py` 复核，固定独立 `/opt/xboard-node-menu-release-20261025`。上传时将前者命名为 `vm_release_fixture.py`，清单使用[正式证据](../../../docs/verification/node-v6-release-20261025.json)的 `runtime.candidate`；程序须取同次v6 Release并逐SHA核对，updated两个amd64文件为同一正式程序，QEMU需匹配清单摘要。只适配自有路径，保留正式版本和四二进制pin。

先执行 `smoke` 核对四程序amd64原生／arm64 QEMU版本，再依次 `prepare`、`run`、`cleanup` 和最终只读guard。正式17项全部通过，真实管理13项及版本4项；保留绑定、健康端口和CA的升级实际执行。下载运输为受控Release；未执行公网Root更新。正式夹具仍使用合成日志，不读取Root私有日志和既有PTY，测试后容器／单元移除、端口释放，原面板HTTP200。
