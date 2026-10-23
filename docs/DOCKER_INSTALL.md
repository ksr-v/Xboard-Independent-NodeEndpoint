# 宝塔 / aaPanel + Docker：安装与私有节点入口

本说明配套 NodeEndpoint 1.0.7、Root `root-installer-v1.0.9` / tools 1.0.6，以及独立全插件后端 `v2026.10.23.1`。原生 PHP-FPM 安装仍使用原教程；Docker 操作在**宿主机 Root SSH**中执行。

## 新建面板：一个安装入口

先准备宝塔 Nginx、Docker Engine 和 Compose 插件、Git、Python 3.10+、curl、OpenSSL、`ss` 和 `runuser`。宿主机不需要再安装 PHP 或 Redis；安装器不会安装系统软件。

宝塔新建纯静态网站，配置普通网站 HTTPS 证书，将该网站反向代理到 `http://127.0.0.1:7001`。修改原有根 `location`，不要重复添加：

```nginx
location / {
    proxy_pass http://127.0.0.1:7001;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 60s;
    proxy_buffering off;
}
```

这是反向代理，不是 Laravel 伪静态。检查并重载 Nginx；容器尚未启动时，网站暂时返回 502 属正常现象。此安装入口要求唯一对应到 `127.0.0.1:7001` 的站点。

在新的空站点目录执行，目录示例换成实际值；不要删除已有网站数据：

```bash
cd /www/wwwroot/panel.example.com
git clone --branch v2026.10.23.1 --single-branch \
  https://github.com/ksr-v/Xboard-Custom-Source.git .
bash docker-install.sh
```

按提示填写实际通讯 IP / 域名和管理员邮箱。IP 可接受自动检测到的公网 IPv4；检测失败或存在 NAT / 多 IP 时手工填写节点实际可访问的地址。核对完整计划后输入一次 `YES`。

默认使用宝塔 MySQL / MariaDB 和内置 Redis。先在宝塔准备空数据库及独立账号；安装时填写容器可访问的数据库地址、端口、库名、账号和隐藏密码。安装器准备权限、构建独立镜像，以容器内 `www` 安装 Xboard 并启用四插件，然后配置独立 HTTPS、本机防火墙、随机 CA 口令托管和公共 CA 导入。默认节点通讯端口为 8443；容器 HTTP 后端仅绑定 `127.0.0.1:7001`。

安装器最后打印 Root 私有日志目录；首次管理员密码和后台路径在该目录的 `*-application-install.out` 中。以 Root 在本机查看并妥善保存，不要公开完整日志。用网站域名登录后台，刷新 `/node-endpoint`，检查通讯地址、公共 CA 和入口验证结果。

默认命令已选择 MySQL / MariaDB。如需无外部数据库的 SQLite，可改用：

```bash
bash docker-install.sh --database sqlite
```

MySQL 地址默认 `host.docker.internal`，安装器将它映射到宿主 Docker 网关；回车接受即可，也可填写实际私网数据库地址。容器的 `127.0.0.1` 不是宿主机。宝塔数据库需要监听容器可达的宿主地址，并允许该 Docker 网段使用独立账号访问，不能只监听宿主回环。工具不会修改数据库监听、防火墙或账号，也不会清空已有数据。已有面板始终保留原数据库。

新装入口只处理全新目录和它自己已认领的同版本重试。它会生成专用 `compose.node-endpoint.yaml`，不要用其它 Compose 文件或默认项目名替代其归属配置。**已有原版 Docker 面板不要运行此新装入口。**

## 已有标准 Docker 面板：补齐私有入口

先备份数据库、原 `.env` / `APP_KEY`、`storage/app`、`.docker/.data`、`plugins` 和其它运行卷；安装或升级并启用本次 NodeEndpoint 插件。保留现有主程序、机器身份和节点配置。

受支持的标准布局是本机 Compose 的 `xboard` 服务，应用在镜像内 `/www`，以非 Root 应用用户运行，并已持久化 `.env`、`storage/app`、`.docker/.data`、`plugins`。不要求把整套源码挂入容器。Web 后端应仅发布到 `127.0.0.1`，宝塔站点应已反代到该后端并支持 WebSocket。

以宿主 Root 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh | bash -s -- --docker
```

多项目时可追加 `--app-dir /实际/Compose项目目录`。没有原生 PHP-FPM 站点时，普通 Root 命令也会尝试识别标准 Docker；显式 `--docker` 可以避免选错部署类型。

核对检测到的项目、容器、后端和通讯地址，确认后工具保存通讯设置、配置入口并导入公共 CA。修改 IP 时，先在插件页面保存并复制新版 Root 命令，或显式使用该命令的 `--host` 参数；只保存网页设置不会更改宿主监听或服务器证书。

若旧部署未挂载 `storage/app`，先停服务并**保留旧容器**，把旧容器的 `/www/storage/app/.` 导出到新的受保护宿主目录，核对公共 CA 和插件记录，再把该已填充目录加入挂载。新增空挂载会遮住原文件；导出前不要 `down`、`rm` 或删除旧容器。

## 重建容器与升级

保留数据库、`APP_KEY`、运行卷和宿主 Root 状态。容器 ID 变化后，重复上面的 `--docker` 命令，核对计划并显式确认。工具通过原凭据与原 CA 私钥的本机匹配验证后认领新容器，保留原 CA、加密口令和节点信任，不默默按容器名字换绑。

若 CA 私钥已移到离线介质，本次重新绑定需提供原加密私钥文件，可在 Root 命令后追加 `--ca-key /Root私有目录/ca.key.pem`；不要把文件挂载到容器或上传到面板。跨项目、持久化来源变化或原凭据无法验证时会停止，不能靠重装插件或生成新 CA 绕过。

升级源码或镜像需按独立后端升级步骤备份、重建并使用**原项目名和原 Compose 配置**替换容器；`docker compose pull` 不会重建本地镜像。新装脚本只支持同版本安全重试，不用于任意源码升级。单纯恢复镜像版本不会回滚数据库，恢复应使用经过核对的配套备份。

## 接入新节点与支持范围

Root 配置完成后，在原生机器管理选择“IP + 私有证书”并复制安装命令。新节点安装命令会携带公共 CA；不需要在每台节点上重复执行面板 Root 工具。已有业务节点使用保留配置的迁移流程。

私钥保存在宿主机网站目录之外的 Root 私有状态中，面板只保存公共 CA 和由原 `APP_KEY` 加密的口令。Web 进程不持有 Docker socket 或 sudo 权限。云安全组 / 服务商防火墙仍需允许实际节点通讯端口。

首版自动流程不支持 host-network、分离容器、远程 Docker、任意服务名或公开 HTTP 后端。未知布局会停止，不自动套用旧手工参数。完整面板教程见[独立后端 Docker 指南](https://github.com/ksr-v/Xboard-Custom-Source/blob/v2026.10.23.1/docs/installation/aapanel-docker.zh-CN.md)。自动流程的实际测试范围以本次 Release 验证记录为准；历史手工 Docker 验证不能代替新自动流程的验证。
