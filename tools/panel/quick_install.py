#!/usr/bin/env python3
"""Root SSH wizard using the independently pinned, already tested tools release."""
import argparse
import fnmatch
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

TOOLS_URL = 'https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/download/root-installer-v1.0.8/node-endpoint-tools-1.0.5.zip'
TOOLS_SHA256 = '7bb619440db545b6785e41d92707aa687ad43cb9529d4f8981f86dc4e1d3eb2f'
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
        require({'tools/panel/manager.py', 'tools/panel/nginx.py', 'tools/panel/pki.py',
                 'tools/panel/panel_ca.py', 'tools/panel/sync_panel_ca.php',
                 'tools/panel/firewall.py', 'tools/panel/panel_passphrase.py',
                 'tools/panel/panel_passphrase.php'} <= set(result), '缺少管理器、防火墙、CA 导入或口令托管工具')
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


def reject_example_host(value):
    """Guard new candidates, while leaving legacy PKI/credential reads usable."""
    from pki import SafetyError, validate_host
    try:
        host, kind = validate_host(value)
    except SafetyError as error:
        raise ValueError(str(error)) from None
    if kind == 'IP':
        address = ipaddress.ip_address(host)
        examples = ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')
        require(not any(address in ipaddress.ip_network(network) for network in examples),
                '这是文档示例 IP，不能作为实际通讯地址；已有入口请用 --repair-address 显式修复')
    return host


class NoIPRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def detect_public_ip(opener=None):
    """Suggest only a bounded HTTPS public IPv4 response; never guess on failure."""
    source = 'https://api.ipify.org'
    try:
        if opener is None:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            opener = urllib.request.build_opener(NoIPRedirect(), urllib.request.HTTPSHandler(context=context))
        with opener.open(urllib.request.Request(source, headers={'User-Agent': 'NodeEndpoint-IP-Detection/1.0'}), timeout=5) as response:
            require(response.geturl() == source, 'IP 检测拒绝重定向')
            data = response.read(129)
        require(len(data) <= 128, 'IP 检测响应过长')
        address = ipaddress.ip_address(data.decode('ascii').strip())
        require(address.version == 4 and address.is_global and not address.is_multicast,
                '未检测到公网 IPv4')
        return reject_example_host(str(address))
    except (OSError, ValueError, UnicodeError):
        return ''


def select_communication_host(current=None, explicit=None):
    if explicit is not None:
        return reject_example_host(explicit)
    if current is not None:
        return reject_example_host(current)
    print('请选择节点通讯地址的获取方式：\n1. 自动检测本机公网 IPv4（默认）\n2. 手动输入 IP / 域名')
    while True:
        try:
            choice = input('请选择 [1]（CANCEL 取消）：').strip().upper() or '1'
        except (EOFError, KeyboardInterrupt):
            raise ValueError('已取消通讯地址选择，未继续安装入口') from None
        if choice == 'CANCEL':
            raise ValueError('已取消通讯地址选择，未继续安装入口')
        if choice in ('1', '2'):
            break
        print('请输入 1 或 2；输入 CANCEL 取消。')
    if choice == '2':
        return reject_example_host(ask('请输入实际通讯 IP / 域名（不含协议）'))
    suggested = detect_public_ip()
    print('检测服务器出口公网 IPv4 作为候选；NAT/多 IP 服务器请核对节点实际可访问的地址。'
          if suggested else '未能检测公网 IPv4，请输入本机实际通讯 IP 或域名。')
    return reject_example_host(ask('通讯 IP/域名（不含协议；回车接受候选，可手工覆盖）', suggested))


def panel_endpoint_command(plan, check=False):
    """Run the custom backend command as its recorded application UID, never Root."""
    import manager
    import pwd
    owner = pwd.getpwnam(plan.get('app_user') or '')
    require(owner.pw_uid != 0 and owner.pw_gid != 0, '必须使用非 Root 应用用户保存面板设置')
    app = Path(plan['app']).resolve(strict=True)
    require((app / 'app/Console/Commands/ConfigureEndpoint.php').is_file(),
            '--repair-address 需要独立 Xboard 后端；原版面板请按手动修改流程处理')
    php = manager.trusted_file(plan.get('php') or '', True)
    runuser = manager.trusted_file(shutil.which('runuser') or '/usr/sbin/runuser', True)
    arguments = ['list', '--raw'] if check else ['xboard:configure-endpoint', '--host=' + reject_example_host(plan['host']),
        '--port=' + str(plan['port']), '--ws-mode=' + ('rewrite' if plan.get('ws_backend') else 'rest')]
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'HOME': owner.pw_dir, 'USER': owner.pw_name, 'LOGNAME': owner.pw_name}
    try:
        result = subprocess.run([str(runuser), '-u', owner.pw_name, '--', str(php),
                                 '-d', 'display_errors=0', '-d', 'log_errors=0', 'artisan', *arguments],
                                cwd=app, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('应用用户命令未完成；未输出应用日志。若入口已提交，可重跑 --repair-address 完成面板设置同步') from None
    require(result.returncode == 0, '应用用户命令未完成；未输出应用日志。若入口已提交，可重跑 --repair-address 完成面板设置同步')
    if check:
        require(any(line.split()[:1] == ['xboard:configure-endpoint']
                    for line in result.stdout.decode('utf-8', errors='replace').splitlines()),
                '独立后端缺少 xboard:configure-endpoint，未修改入口')


def repair_address(explicit=None, skip_firewall=False, nginx=None):
    """Explicitly renew the server SAN under the existing CA, then save the panel."""
    import manager
    from panel_ca import payload_from_state
    plan, payload = payload_from_state('/var/lib/node-endpoint')
    from pki import read_json
    metadata = read_json(manager.trusted_file('/var/lib/node-endpoint/pki/state.json'))
    require(metadata.get('active_ca') and metadata.get('host') == plan['host'] and not metadata.get('rotation'),
            '地址修复要求现有 CA、证书地址与入口一致，且没有待完成的 CA 轮换；未创建新 CA')
    panel_endpoint_command(plan, check=True)
    try:
        current = reject_example_host(plan['host'])
    except ValueError:
        current = None
    host = select_communication_host(current=current, explicit=explicit)
    require(not (Path('/var/lib/node-endpoint/pki/state.json').is_symlink()), '拒绝 PKI 状态符号链接')
    print('修复通讯地址：' + plan['host'] + ' -> ' + host + ':' + str(plan['port'])
          + '；沿用原 CA 和口令，仅在地址变化时重签服务器证书。')
    argv = (['--nginx', nginx] if nginx else []) + ['modify', '--host', host, '--port', str(plan['port']),
        '--site-config', plan['source_site'], '--site-name', plan['site_name'], '--include-dir', plan['include_dir'],
        '--app', plan['app'], '--app-user', plan['app_user'], '--php', plan['php'],
        '--deployment', plan['deployment'], '--backend', plan['backend']]
    if plan.get('ws_backend'):
        argv += ['--enable-ws', '--ws-backend', plan['ws_backend']]
    manager.execute(manager.parser().parse_args(argv))
    committed, after = payload_from_state('/var/lib/node-endpoint')
    require(committed['host'] == host and committed['port'] == plan['port'], '入口已被其他操作修改，未写入面板设置')
    require(after['pem'] == payload['pem'] and after['fingerprints'] == payload['fingerprints'],
            'CA 信任发生变化，未自动保存面板设置，请核对并发维护操作')
    panel_endpoint_command(committed)
    print('面板通讯设置已同步；正在核对防火墙并同步原 CA 公共证书。')
    finish_install(skip_firewall)


def refresh_entry(nginx=None):
    """Re-audit an owned HTTPS entry without changing its address or CA trust."""
    import manager
    from panel_ca import payload_from_state
    plan, before = payload_from_state('/var/lib/node-endpoint')
    required = ('host', 'source_site', 'site_name', 'include_dir', 'app', 'app_user', 'php', 'deployment', 'backend')
    require(all(isinstance(plan.get(key), str) and plan[key] for key in required),
            'Root 安装记录缺少完整站点参数，未猜测或重建入口')
    require(type(plan.get('port')) is int and plan.get('https') is True and plan['deployment'] in ('fpm', 'octane'),
            '刷新仅支持已有的同机 HTTPS FPM/Octane 独立入口')
    argv = (['--nginx', nginx] if nginx else []) + ['modify', '--host', plan['host'], '--port', str(plan['port']),
        '--site-config', plan['source_site'], '--site-name', plan['site_name'], '--include-dir', plan['include_dir'],
        '--app', plan['app'], '--app-user', plan['app_user'], '--php', plan['php'],
        '--deployment', plan['deployment'], '--backend', plan['backend']]
    if plan.get('ws_backend'):
        argv += ['--enable-ws', '--ws-backend', plan['ws_backend']]
    print('刷新已安装入口的路由白名单；保留当前通讯地址、端口、CA 和面板设置。请核对完整计划并输入 INSTALL 地址:端口确认。')
    manager.execute(manager.parser().parse_args(argv))
    committed, after = payload_from_state('/var/lib/node-endpoint')
    require(all(committed.get(key) == plan.get(key) for key in ('host', 'port', 'https')),
            '刷新后的入口地址或端口发生变化，请核对并发维护操作')
    require(after['pem'] == before['pem'] and after['fingerprints'] == before['fingerprints'],
            '刷新后的 CA 信任发生变化，请核对并发维护操作')
    print('入口路由刷新完成，通讯地址和 CA 已核对保持一致。')
    print('若节点已检测到公网 IPv4，旧的 404 退避最多 10 分钟后会重试上报，随后可刷新服务器管理页面。')


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


def configure_firewall(state='/var/lib/node-endpoint'):
    """Open only the committed, owned entry's port while holding its manager lock."""
    import manager
    from firewall import ensure_port
    manager.linux_root()
    import fcntl
    state = manager.safe_state(state)
    for name in (manager.STATE_MARKER, 'installation.json', 'manager.lock'):
        path = state / name
        require(not path.is_symlink(), 'Root 状态文件不能为符号链接：' + name)
        require(manager.trusted_file(path).stat().st_nlink == 1, 'Root 状态文件不能为硬链接：' + name)
    marker = json.loads(manager.trusted_file(state / manager.STATE_MARKER).read_text())
    require(isinstance(marker, dict) and marker.get('tool') == 'NodeEndpoint'
            and marker.get('schema_version') == 1 and marker.get('path') == str(state)
            and isinstance(marker.get('ownership'), str)
            and re.fullmatch('[a-f0-9]{32}', marker['ownership']), 'Root 安装记录归属不一致')
    descriptor = os.open(manager.trusted_file(state / 'manager.lock'), os.O_RDWR | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        require(details.st_uid == 0 and details.st_nlink == 1 and stat.S_IMODE(details.st_mode) == 0o600,
                'Root 管理锁必须为 Root 所有的独立 0600 文件')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('其他 Root 管理操作正在执行') from error
        installed = json.loads(manager.trusted_file(state / 'installation.json').read_text())
        require(isinstance(installed, dict) and isinstance(installed.get('plan'), dict), '缺少已安装入口计划')
        require(isinstance(installed.get('config_path'), str)
                and isinstance(installed.get('ownership'), str)
                and re.fullmatch('[a-f0-9]{32}', installed['ownership']), '入口安装记录无效')
        manager.trusted_file(installed['config_path'])
        manager.Manager(state, None).assert_owned()
        plan = installed['plan']
        require(plan.get('ownership') == installed.get('ownership')
                and plan.get('config_path') == installed.get('config_path'), '入口计划与配置归属不一致')
        port = plan.get('port')
        require(type(port) is int and 1 <= port <= 65535 and port not in (80, 443), '已安装入口端口无效')
        config = manager.parse(Path(installed['config_path']).read_text(), installed['config_path'])
        listeners = [entry.words for entry in manager.walk(config) if entry.words[0] == 'listen']
        require(bool(listeners) and all(len(words) >= 2 and words[1] in
                ('0.0.0.0:' + str(port), '[::]:' + str(port)) for words in listeners),
                '入口监听与安装记录端口不一致')
        return ensure_port(port)
    finally:
        os.close(descriptor)


def finish_install(skip_firewall=False):
    """Keep successful entry/PKI intact and report each independent follow-up."""
    failures = []
    if skip_firewall:
        print('已按 --skip-firewall 保留现有本机防火墙规则。')
    else:
        try:
            result = configure_firewall()
            print('本机防火墙：' + json.dumps(result, ensure_ascii=False))
            if result['status'] not in ('ensured', 'saved_inactive'):
                failures.append('firewall')
        except (RuntimeError, OSError, ValueError) as error:
            print('入口已安装，本机防火墙处理未完成：' + str(error), file=sys.stderr)
            failures.append('firewall')
        if 'firewall' in failures:
            print('核对后使用同一短命令加 bash -s -- --configure-firewall 重试；也可在宝塔安全页手动放行已配置的 TCP 端口。', file=sys.stderr)
    from panel_ca import sync_panel_ca
    try:
        result = sync_panel_ca()
        print('CA 公共证书已自动导入面板，请刷新插件页面：' + json.dumps(result, ensure_ascii=False))
    except (RuntimeError, OSError, ValueError) as error:
        print('入口已安装，CA 自动导入未完成：' + str(error), file=sys.stderr)
        print('修正原因后使用同一短命令加 bash -s -- --sync-ca 单独重试；不会重新生成证书。', file=sys.stderr)
        failures.append('ca')
    print('云安全组/服务商防火墙仍需在服务商控制台放行；请从节点服务器测试实际连通性。')
    print('公共 CA：/var/lib/node-endpoint/pki/trust-bundle.pem；状态：/var/lib/node-endpoint/public-status.json')
    print('已有 Node 尚未自动迁移；新增机器可在原生机器管理中选择 IP + 私有证书并复制安装命令。')
    if failures:
        raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-file', help='Offline ZIP; the same built-in SHA256 pin still applies')
    parser.add_argument('--nginx')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--detect-only', action='store_true', help='Prepare verified tool cache and inspect; do not install endpoint or change firewall')
    actions.add_argument('--sync-ca', action='store_true', help='Import public CA from existing Root state into this panel; do not reinstall endpoint')
    actions.add_argument('--configure-firewall', action='store_true', help='Allow the installed entry TCP port persistently; do not reinstall or regenerate certificates')
    actions.add_argument('--repair-address', action='store_true', help='Repair an installed custom backend endpoint using the existing CA; renew server SAN and save panel settings')
    actions.add_argument('--refresh-entry', action='store_true', help='Re-audit the owned HTTPS entry route allowlist, retaining its address, CA and panel settings')
    parser.add_argument('--host', help='Actual bare IP or DNS host; omit to choose IPv4 detection or manual input on first installation/repair')
    parser.add_argument('--skip-firewall', action='store_true', help='Keep local firewall unchanged during entry installation')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, '请在面板服务器以 Root 运行')
    require(sys.version_info >= (3, 10), '需要 Python 3.10+')
    import pwd
    os.umask(0o077)
    require(not (args.skip_firewall and (args.detect_only or args.sync_ca or args.configure_firewall or args.refresh_entry)),
            '--skip-firewall 仅用于入口安装或地址修复')
    require(not (args.host and (args.detect_only or args.sync_ca or args.configure_firewall or args.refresh_entry)), '--host 仅用于安装或地址修复')
    require(args.detect_only or args.sync_ca or args.configure_firewall or sys.stdin.isatty(), '安装需要 SSH 交互终端，不要将脚本通过管道执行')
    for name in ('openssl', 'ss', 'runuser'):
        require(shutil.which(name), '缺少依赖：' + name + '，请先通过本机包管理器安装')
    print('校验固定 tools v1.0.5（随机 CA 口令加密托管 + 本机 TCP 端口放行 + CA 自动导入）；不更新 Xboard、Node 或系统软件。')
    data = Path(args.package_file).read_bytes() if args.package_file else download()
    directory = install_cache(verified_members(data))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(directory / 'tools/panel'))
    import manager
    if args.configure_firewall:
        result = configure_firewall()
        print('本机防火墙：' + json.dumps(result, ensure_ascii=False))
        print('本命令只处理已安装入口的本机规则；云安全组/服务商防火墙请另行核对。')
        if result['status'] not in ('ensured', 'saved_inactive'):
            raise SystemExit(2)
        return
    if args.sync_ca:
        from panel_ca import sync_panel_ca
        result = sync_panel_ca()
        print('CA 公共证书已自动导入面板，请刷新插件页面：' + json.dumps(result, ensure_ascii=False))
        return
    selected_nginx = args.nginx
    if not selected_nginx and Path('/www/server/nginx/sbin/nginx').is_file():
        selected_nginx = '/www/server/nginx/sbin/nginx'
    if args.refresh_entry:
        return refresh_entry(selected_nginx)
    if args.repair_address:
        return repair_address(args.host, args.skip_firewall, selected_nginx)
    environment = manager.Environment(selected_nginx)
    choices = discover(environment, manager)
    if args.detect_only:
        print(json.dumps({'verified_tools_sha256': TOOLS_SHA256, 'fpm_sites': choices, 'endpoint_installation_performed': False}, ensure_ascii=False, indent=2))
        return
    if not choices:
        print('未发现可自动填写的原生 PHP-FPM 站点。Docker/Octane 或特殊布局请在现有菜单核对真实路径和后端；不会按 FPM 猜测安装。')
        print('手工管理菜单不自动配置防火墙或导入 CA。完成入口安装后，分别使用 --configure-firewall 和 --sync-ca。')
        return manager.menu(manager.parser().parse_args((['--nginx', selected_nginx] if selected_nginx else []) + ['menu']))
    site = select(choices, 'Xboard 原站点')
    print('检测结果：' + json.dumps(site, ensure_ascii=False))
    uid = (Path(site['app']) / 'storage').stat().st_uid
    user = pwd.getpwuid(uid).pw_name if uid != 0 else ''
    if not user:
        user = ask('Xboard 非 Root 运行用户')
    require(pwd.getpwnam(user).pw_uid != 0, '应用不能以 Root 运行')
    php = select(php_candidates(), 'PHP CLI（按实际 FPM 版本选择）')
    state = Path('/var/lib/node-endpoint/installation.json')
    installed = None
    if state.exists():
        from panel_ca import payload_from_state
        installed, _ = payload_from_state(state.parent)
    host = select_communication_host(current=installed['host'] if installed else None, explicit=args.host)
    port = ask('通讯端口', str(installed['port']) if installed else '8443')
    include_dir = site['include_dir'] or ask('检测已列出的 HTTP include 目录')
    argv = (['--nginx', selected_nginx] if selected_nginx else []) + ['install', '--host', host, '--port', port,
            '--site-config', site['site_config'], '--site-name', site['site_name'], '--include-dir', include_dir,
            '--app', site['app'], '--app-user', user, '--php', php, '--deployment', 'fpm', '--backend', site['backend']]
    if site['ws_backend'] and input('使用检测到的 WS 后端 ' + site['ws_backend'] + '？[Y/n]：').strip().lower() not in ('n', 'no'):
        argv += ['--enable-ws', '--ws-backend', site['ws_backend']]
    else:
        print('未启用 WS；请将插件页新入口模式设为 REST fallback。')
    if state.exists():
        require(input('独立入口已存在。修改前请备份；输入 MODIFY 继续：') == 'MODIFY', '取消修改')
        argv[argv.index('install')] = 'modify'
    print('接下来核对站点/路由/监听并展示计划，请完整确认 INSTALL 地址:端口。新版插件的新 CA 口令自动随机生成并加密保存在面板；原有未托管 CA 仍需原口令。')
    if not args.skip_firewall:
        print('入口成功后将为该 TCP 端口添加允许所有来源的本机防火墙规则并持久保存；自管规则可用 --skip-firewall。')
    manager.execute(manager.parser().parse_args(argv))
    print('入口安装完成。正在处理本机防火墙并通过非 Root 应用用户自动导入 CA 公共证书。')
    finish_install(args.skip_firewall)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, EOFError, KeyboardInterrupt) as error:
        print('停止：' + str(error), file=sys.stderr)
        sys.exit(1)
