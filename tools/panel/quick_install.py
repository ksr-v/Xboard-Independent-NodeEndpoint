#!/usr/bin/env python3
"""Root SSH wizard using the independently pinned, already tested tools release."""
import argparse
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

TOOLS_URL = 'https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/download/root-installer-v1.0.2/node-endpoint-tools-1.0.1.zip'
TOOLS_SHA256 = 'e7de5e40074051e4b90ab9dc516c231e8023746fae8c3283374e521c7bdd01ad'
CACHE = Path('/opt/nodeendpoint-quick') / TOOLS_SHA256


def require(value, reason):
    if not value:
        raise ValueError(reason)


def secure_path(path):
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        if item.exists() or item.is_symlink():
            info = item.lstat()
            require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                    '路径必须由 Root 所有、不可被组/其他用户写入且无符号链接：' + str(item))
    return path


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == 'https', '拒绝非 HTTPS 下载重定向')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(HTTPSRedirect(), urllib.request.HTTPSHandler(context=context))
    with opener.open(urllib.request.Request(TOOLS_URL, headers={'User-Agent': 'NodeEndpoint-Root-Wizard/1.0'}), timeout=45) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    require(len(data) <= 16 * 1024 * 1024, '工具包超出下载大小限制')
    return data


def verified_members(data, expected=TOOLS_SHA256):
    require(hashlib.sha256(data).hexdigest() == expected, '工具包 SHA-256 不匹配，未执行任何工具')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and 0 < len(names) < 2000, '重复或异常 ZIP 成员')
        result = {}
        total = 0
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            require(bool(path.parts) and not path.is_absolute() and '..' not in path.parts and not any(c in entry.filename for c in ('\\', ':'))
                    and entry.filename == path.as_posix() and not entry.is_dir(), '不安全 ZIP 路径')
            require(stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG), '拒绝 ZIP 链接或特殊文件')
            total += entry.file_size
            require(entry.file_size <= 16 * 1024 * 1024 and total <= 64 * 1024 * 1024, 'ZIP 解压大小异常')
            result[entry.filename] = archive.read(entry)
        manifest = {}
        for line in result['MANIFEST.sha256'].decode('utf-8').splitlines():
            digest, name = line.split('  ', 1)
            require(re.fullmatch('[a-f0-9]{64}', digest) and name not in manifest, '清单重复或摘要无效')
            manifest[name] = digest
        require(set(manifest) == set(result) - {'MANIFEST.sha256'}, '工具包清单不完整')
        require(all(hashlib.sha256(result[name]).hexdigest() == digest for name, digest in manifest.items()), '成员摘要不匹配')
        require({'tools/panel/manager.py', 'tools/panel/nginx.py', 'tools/panel/pki.py'} <= set(result), '缺少管理器')
        return result


def install_cache(members, target=CACHE):
    target = secure_path(target)
    marker = '.nodeendpoint-verified-package.json'
    if target.exists():
        secure_path(target / marker)
        require(target.is_dir() and (target / marker).is_file(), '缓存目录已有其他内容，拒绝覆盖')
        require(json.loads((target / marker).read_text()) == {'sha256': TOOLS_SHA256}, '缓存归属不匹配')
        observed = set()
        for path in target.rglob('*'):
            secure_path(path)
            if path.is_file():
                name = path.relative_to(target).as_posix()
                if name != marker:
                    require(name in members and path.read_bytes() == members[name], '缓存已被修改，拒绝执行')
                    observed.add(name)
        require(observed == set(members), '缓存成员缺失')
        return target
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_path(target.parent)
    temporary = Path(tempfile.mkdtemp(prefix='.verified-', dir=target.parent))
    try:
        for name, data in members.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open('xb') as handle:
                handle.write(data)
            path.chmod(0o600)
        (temporary / marker).write_text(json.dumps({'sha256': TOOLS_SHA256}), encoding='utf-8')
        (temporary / marker).chmod(0o600)
        require(not target.exists(), '缓存目录同时被创建，拒绝覆盖')
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def discover(environment, manager):
    """Use the same expanded Nginx AST and literal backends as the manager."""
    candidates = []
    for filename, text in environment.files.items():
        for site in manager.expanded(manager.parse(text, filename), environment.files, environment.prefix):
            if site.words != ['server']:
                continue
            roots = manager.values(site.children, 'root')
            if len(roots) != 1 or len(roots[0]) != 1 or '$' in roots[0][0]:
                continue
            public = Path(roots[0][0])
            app = public.parent
            if public.name != 'public' or not (app / 'artisan').is_file() or not (public / 'index.php').is_file():
                continue
            ws_locations = [x for x in manager.walk(site.children) if x.words[0] == 'location' and any(w in ('/ws', '/ws/') for w in x.words)]
            ws_nodes = [x for location in ws_locations for x in manager.walk(location.children)]
            backends = {x.words[1] for x in manager.walk(site.children) if x.words[0] == 'fastcgi_pass' and len(x.words) == 2 and x not in ws_nodes}
            if len(backends) != 1:
                continue
            ws = {x.words[1] for x in ws_nodes if x.words[0] == 'proxy_pass' and len(x.words) == 2 and re.fullmatch(r'http://127\.0\.0\.1:[0-9]+', x.words[1])}
            names = [name for group in manager.values(site.children, 'server_name') for name in group if not any(c in name for c in ('*', '$', '~')) and name != '_']
            directories = {str(PurePosixPath(pattern).parent) for pattern in environment.includes
                           if not any(c in str(PurePosixPath(pattern).parent) for c in ('*', '?', '['))
                           and fnmatch.fnmatchcase(str(PurePosixPath(pattern).parent / 'node-endpoint.conf'), pattern)}
            preferred = str(PurePosixPath(filename).parent)
            for name in names:
                candidates.append({'site_config': filename, 'site_name': name, 'app': str(app), 'backend': next(iter(backends)),
                                   'include_dir': preferred if preferred in directories else (next(iter(directories)) if len(directories) == 1 else ''),
                                   'ws_backend': next(iter(ws)) if len(ws) == 1 else ''})
    unique = {json.dumps(x, sort_keys=True): x for x in candidates}
    return list(unique.values())


def select(items, label):
    require(items, label + '未发现候选值')
    if len(items) == 1:
        return items[0]
    print(label + '存在多个候选，请选择：')
    for i, item in enumerate(items, 1):
        print(str(i) + '. ' + str(item))
    while True:
        answer = input('编号：').strip()
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]


def ask(label, default=''):
    result = input(label + (' [' + str(default) + ']' if default else '') + '：').strip() or str(default)
    require(result, label + '不能为空')
    return result


def php_candidates():
    paths = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            executable = (process / 'exe').resolve(strict=True)
            if executable.name == 'php-fpm':
                candidate = executable.parent.parent / 'bin/php'
            elif re.fullmatch(r'php-fpm[0-9.]+', executable.name):
                candidate = Path('/usr/bin') / executable.name.replace('php-fpm', 'php', 1)
            else:
                continue
            if candidate.is_file() and os.access(candidate, os.X_OK):
                paths.add(str(candidate))
        except OSError:
            continue
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-file', help='Offline ZIP; the same built-in SHA256 pin still applies')
    parser.add_argument('--nginx')
    parser.add_argument('--detect-only', action='store_true', help='Prepare verified tool cache and inspect; do not install endpoint')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, '请在面板服务器以 Root 运行')
    require(sys.version_info >= (3, 10), '需要 Python 3.10+')
    import pwd
    os.umask(0o077)
    require(args.detect_only or sys.stdin.isatty(), '安装需要 SSH 交互终端，不要将脚本通过管道执行')
    for name in ('openssl', 'ss', 'runuser'):
        require(shutil.which(name), '缺少依赖：' + name + '，请先通过本机包管理器安装')
    print('校验固定 tools v1.0.1（Nginx 解析修复）；不更新 Xboard、Node 或系统软件。')
    data = Path(args.package_file).read_bytes() if args.package_file else download()
    directory = install_cache(verified_members(data))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(directory / 'tools/panel'))
    import manager
    selected_nginx = args.nginx
    if not selected_nginx and Path('/www/server/nginx/sbin/nginx').is_file():
        selected_nginx = '/www/server/nginx/sbin/nginx'
    environment = manager.Environment(selected_nginx)
    choices = discover(environment, manager)
    if args.detect_only:
        print(json.dumps({'verified_tools_sha256': TOOLS_SHA256, 'fpm_sites': choices, 'endpoint_installation_performed': False}, ensure_ascii=False, indent=2))
        return
    if not choices:
        print('未发现可自动填写的原生 PHP-FPM 站点。Docker/Octane 或特殊布局请在现有菜单核对真实路径和后端；不会按 FPM 猜测安装。')
        return manager.menu(manager.parser().parse_args((['--nginx', selected_nginx] if selected_nginx else []) + ['menu']))
    site = select(choices, 'Xboard 原站点')
    print('检测结果：' + json.dumps(site, ensure_ascii=False))
    uid = (Path(site['app']) / 'storage').stat().st_uid
    user = pwd.getpwuid(uid).pw_name if uid != 0 else ''
    if not user:
        user = ask('Xboard 非 Root 运行用户')
    require(pwd.getpwnam(user).pw_uid != 0, '应用不能以 Root 运行')
    php = select(php_candidates(), 'PHP CLI（按实际 FPM 版本选择）')
    host = ask('通讯 IP/域名（与已启用插件页面保存值一致）')
    port = ask('通讯端口', '8443')
    include_dir = site['include_dir'] or ask('检测已列出的 HTTP include 目录')
    argv = (['--nginx', selected_nginx] if selected_nginx else []) + ['install', '--host', host, '--port', port,
            '--site-config', site['site_config'], '--site-name', site['site_name'], '--include-dir', include_dir,
            '--app', site['app'], '--app-user', user, '--php', php, '--deployment', 'fpm', '--backend', site['backend']]
    if site['ws_backend'] and input('使用检测到的 WS 后端 ' + site['ws_backend'] + '？[Y/n]：').strip().lower() not in ('n', 'no'):
        argv += ['--enable-ws', '--ws-backend', site['ws_backend']]
    else:
        print('未启用 WS；请将插件页新入口模式设为 REST fallback。')
    state = Path('/var/lib/node-endpoint/installation.json')
    if state.exists():
        require(input('独立入口已存在。修改前请备份；输入 MODIFY 继续：') == 'MODIFY', '取消修改')
        argv[argv.index('install')] = 'modify'
    print('接下来由已校验管理器核对站点/路由/监听并展示计划，要求输入 INSTALL 地址:端口 和隐藏 CA 口令。')
    manager.execute(manager.parser().parse_args(argv))
    print('入口安装完成。防火墙/云安全组请按需要放行该端口；将公共 CA 和独立指纹导入插件页并分发给 Node。')
    print('公共 CA：/var/lib/node-endpoint/pki/trust-bundle.pem；状态：/var/lib/node-endpoint/public-status.json')
    print('已有 Node 尚未自动迁移，请先验证新测试身份，再按维护窗口逐个迁移。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, EOFError, KeyboardInterrupt) as error:
        print('停止：' + str(error), file=sys.stderr)
        sys.exit(1)
