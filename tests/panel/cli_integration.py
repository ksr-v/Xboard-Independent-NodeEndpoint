#!/usr/bin/env python3
"""Real Root CLI + native Laravel + isolated PHP-FPM smoke on a test VM.

Run as Root on an explicitly dedicated Linux test machine. Requires existing
nginx, PHP-FPM, PHP, OpenSSL, ss and tests/integration/vendor dependencies.
Uses real audited ServerRoute classes and Laravel's actual route:list command.
Never changes system service configuration. Retains a private evidence directory
after stopping only fixture processes identified by config, executable and start.
"""
import argparse
import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linux_integration import existing_proxy_pids, free_port, process_info


BOOTSTRAP = r'''<?php
declare(strict_types=1);
$project = __PROJECT__;
$fixture = dirname(__DIR__);
require $project . '/tests/integration/vendor/autoload.php';
spl_autoload_register(function (string $class) use ($project): void {
    if (str_starts_with($class, 'App\\')) {
        $file = $project . '/.audit/Xboard/app/' . str_replace('\\', '/', substr($class, 4)) . '.php';
        if (is_file($file)) require_once $file;
    }
});
// Synthetic settings storage only; routes, middleware, exception handling and
// the route:list implementation are the real audited native source classes.
function admin_setting($key, $default = null) {
    return ['server_token' => 'cli-fixture-node-token', 'app_url' => 'https://original.example'][$key] ?? $default;
}
final class CliFixtureKernel extends Illuminate\Foundation\Http\Kernel {
    protected $bootstrappers = [];
    protected $middleware = [App\Http\Middleware\TrustProxies::class];
    protected $middlewareGroups = ['api' => [App\Http\Middleware\ForceJson::class], 'web' => []];
    protected $middlewareAliases = ['server' => App\Http\Middleware\Server::class,
        'server.v2' => App\Http\Middleware\ServerV2::class];
}
final class CliFixtureConsoleKernel extends Illuminate\Foundation\Console\Kernel {
    protected $bootstrappers = [];
    protected $commands = [Illuminate\Foundation\Console\RouteListCommand::class];
}
$app = new Illuminate\Foundation\Application($fixture);
$app->useStoragePath($fixture . '/storage');
$app->usePublicPath($fixture . '/public');
$app->instance('config', new Illuminate\Config\Repository([
    'app' => ['name' => 'NodeEndpoint real CLI fixture', 'env' => 'testing', 'debug' => true,
        'timezone' => 'UTC', 'url' => 'https://original.example',
        'key' => 'base64:' . base64_encode(str_repeat('f', 32)), 'cipher' => 'AES-256-CBC'],
    'cache' => ['default' => 'array', 'stores' => ['array' => ['driver' => 'array']]],
    'logging' => ['default' => 'single', 'channels' => ['single' => ['driver' => 'single',
        'path' => $fixture . '/storage/logs/laravel.log', 'level' => 'debug']]],
    'database' => ['default' => 'sqlite', 'connections' => ['sqlite' => ['driver' => 'sqlite',
        'database' => ':memory:', 'prefix' => '']]],
    'view' => ['paths' => [$fixture . '/resources/views'], 'compiled' => $fixture . '/storage/framework/views'],
    'filesystems' => ['default' => 'local', 'disks' => ['local' => ['driver' => 'local', 'root' => $fixture . '/storage/app']]],
]));
Illuminate\Support\Facades\Facade::setFacadeApplication($app);
$app->instance('request', Illuminate\Http\Request::create('https://original.example/'));
$app->singleton(Illuminate\Contracts\Debug\ExceptionHandler::class, App\Exceptions\Handler::class);
$app->singleton(Illuminate\Contracts\Http\Kernel::class, CliFixtureKernel::class);
$app->singleton(Illuminate\Contracts\Console\Kernel::class, CliFixtureConsoleKernel::class);
foreach ([Illuminate\Filesystem\FilesystemServiceProvider::class,
    Illuminate\Database\DatabaseServiceProvider::class, Illuminate\Cache\CacheServiceProvider::class,
    Illuminate\View\ViewServiceProvider::class, Illuminate\Translation\TranslationServiceProvider::class,
    Illuminate\Validation\ValidationServiceProvider::class,
    Illuminate\Foundation\Providers\FoundationServiceProvider::class] as $provider) $app->register($provider);
$app->make(Illuminate\Contracts\Http\Kernel::class);
$app->boot();
$router = $app->make('router');
$router->group(['prefix' => 'api/v1', 'middleware' => 'api'], function ($router): void {
    (new App\Http\Routes\V1\ServerRoute)->map($router);
});
$router->group(['prefix' => 'api/v2', 'middleware' => 'api'], function ($router): void {
    (new App\Http\Routes\V2\ServerRoute)->map($router);
});
$router->get('/', fn () => response('original-native-website', 200));
return $app;
'''
ARTISAN = r'''<?php
$app = require __DIR__ . '/bootstrap/app.php';
$kernel = $app->make(Illuminate\Contracts\Console\Kernel::class);
$input = new Symfony\Component\Console\Input\ArgvInput;
$status = $kernel->handle($input, new Symfony\Component\Console\Output\ConsoleOutput);
$kernel->terminate($input, $status);
exit($status);
'''
INDEX = r'''<?php
$app = require dirname(__DIR__) . '/bootstrap/app.php';
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
$request = Illuminate\Http\Request::capture();
$response = $kernel->handle($request);
$response->send();
$kernel->terminate($request, $response);
'''


def ensure(condition, text):
    if not condition:
        raise RuntimeError(text)


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(path, 0o600)


class Fixture:
    def __init__(self, app_user):
        self.project = Path(__file__).resolve().parents[2]
        ensure(sys.platform == 'linux' and os.geteuid() == 0, 'Requires dedicated Linux Root test machine')
        ensure(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Requires Linux pidfd support')
        self.user = pwd.getpwnam(app_user)
        ensure(self.user.pw_uid != 0, 'Application user must be non-root')
        self.nginx = shutil.which('nginx')
        self.php = shutil.which('php')
        self.fpm = shutil.which('php-fpm8.3') or shutil.which('php-fpm')
        ensure(all((self.nginx, self.php, self.fpm, shutil.which('openssl'), shutil.which('ss'))), 'Dependencies missing')
        ensure((self.project / 'tests/integration/vendor/autoload.php').is_file(), 'Native Laravel dependencies missing')
        ensure((self.project / '.audit/Xboard/app/Http/Routes/V2/ServerRoute.php').is_file(), 'Audited native source missing')
        ensure(not existing_proxy_pids(), 'Refusing any existing Nginx/OpenResty process')
        self.root = Path(tempfile.mkdtemp(prefix='node-endpoint-cli-integration-', dir='/var/lib')).resolve()
        os.chmod(self.root, 0o755)  # non-root PHP workers may traverse; private evidence remains 0600.
        item = self.root.stat()
        self.root_identity = (item.st_dev, item.st_ino)
        self.marker = {'tool': 'NodeEndpoint real CLI integration', 'token': secrets.token_hex(16), 'path': str(self.root)}
        json_write(self.root / '.fixture-owner.json', self.marker)
        self.owned = {}
        self.handles = []
        self.results = []
        self.count = 0
        self.binary = {}
        for kind, name in [('nginx', self.nginx), ('fpm', self.fpm)]:
            item = Path(name).resolve().stat()
            self.binary[kind] = (item.st_dev, item.st_ino)
        self.main = self.root / 'nginx.conf'
        self.fpm_config = self.root / 'php-fpm.conf'
        self.services = self.service_snapshot()

    def workspace_owned(self):
        ensure(self.root.parent == Path('/var/lib') and self.root.name.startswith('node-endpoint-cli-integration-')
               and not self.root.is_symlink() and self.root.resolve() == self.root, 'Fixture path differs')
        item = self.root.stat()
        ensure((item.st_dev, item.st_ino) == self.root_identity and item.st_uid == 0
               and (item.st_mode & 0o777) == 0o755, 'Fixture directory identity/permissions differ')
        marker = self.root / '.fixture-owner.json'
        ensure(not marker.is_symlink() and marker.stat().st_uid == 0 and marker.stat().st_mode & 0o077 == 0
               and json.loads(marker.read_text()) == self.marker, 'Fixture ownership marker differs')

    def service_snapshot(self):
        result = {}
        for service in ('php8.3-fpm.service', 'docker.service', 'flowscope-collector.service'):
            proc = subprocess.run(['systemctl', 'show', service, '-p', 'MainPID', '-p', 'ActiveState'],
                                  capture_output=True, text=True, timeout=15)
            ensure(proc.returncode == 0, 'Cannot verify existing service ' + service)
            fields = dict(x.split('=', 1) for x in proc.stdout.splitlines() if '=' in x)
            identity = process_info(int(fields.get('MainPID', '0'))) if fields.get('MainPID', '0') != '0' else None
            result[service] = {'state': fields.get('ActiveState'), 'pid': fields.get('MainPID'),
                               'start': identity.get('start') if identity else None}
        return result

    def run(self, args, label, stdin='', timeout=90):
        self.count += 1
        proc = subprocess.run([str(x) for x in args], input=stdin, text=True, capture_output=True, timeout=timeout)
        # No secrets are command arguments or logged stdin. The fixture password
        # is synthetic and getpass's non-TTY fallback reads it only from stdin.
        (self.root / f'{self.count:02d}-{label}.stdout').write_text(proc.stdout)
        (self.root / f'{self.count:02d}-{label}.stderr').write_text(proc.stderr)
        os.chmod(self.root / f'{self.count:02d}-{label}.stdout', 0o600)
        os.chmod(self.root / f'{self.count:02d}-{label}.stderr', 0o600)
        ensure(proc.returncode == 0, f'{label} failed with exit {proc.returncode}; inspect fixture evidence')
        return proc.stdout

    def master_kind(self, info):
        if not info or info['state'] == 'Z':
            return None
        command = info.get('command', '')
        if info.get('exe') == self.binary['nginx'] and re.match(r'^nginx: master process ', command):
            words = shlex.split(command.split('master process ', 1)[1])
            try:
                if Path(words[words.index('-c') + 1]).resolve() == self.main and Path(words[words.index('-p') + 1]).resolve() == self.root:
                    return 'nginx'
            except (ValueError, IndexError):
                pass
        if info.get('exe') == self.binary['fpm']:
            if command == f'php-fpm: master process ({self.fpm_config})':
                return 'fpm'
            words = shlex.split(command)
            try:
                if '--nodaemonize' in words and Path(words[words.index('--fpm-config') + 1]).resolve() == self.fpm_config:
                    return 'fpm'
            except (ValueError, IndexError):
                pass
        return None

    def capture(self):
        infos = {}
        for folder in Path('/proc').iterdir():
            if folder.name.isdigit():
                info = process_info(folder.name)
                if info and info['state'] != 'Z' and info.get('exe') in self.binary.values():
                    infos[info['pid']] = info
        own = {pid for pid, info in infos.items() if self.master_kind(info)}
        while True:
            children = {pid for pid, info in infos.items() if info['parent'] in own
                        and info.get('exe') == infos[info['parent']].get('exe')}
            if children.issubset(own):
                break
            own.update(children)
        self.owned.update({pid: infos[pid] for pid in own})

    def alive(self, expected):
        current = process_info(expected['pid'])
        if not current or current['state'] == 'Z' or current['start'] != expected['start']:
            return False
        ensure(current.get('exe') == expected['exe'], 'Owned process changed executable; retaining evidence')
        return True

    def send(self, expected, sig):
        if not self.alive(expected):
            return
        try:
            fd = os.pidfd_open(expected['pid'])
        except ProcessLookupError:
            return
        try:
            if self.alive(expected):
                signal.pidfd_send_signal(fd, sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)

    def wait_exit(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.capture()
            if not any(self.alive(info) for info in self.owned.values()):
                return True
            time.sleep(0.1)
        return not any(self.alive(info) for info in self.owned.values())

    def cleanup(self):
        self.workspace_owned()
        self.capture()
        for info in list(self.owned.values()):
            if self.master_kind(info):
                self.send(info, signal.SIGQUIT)
        if not self.wait_exit(6):
            for info in list(self.owned.values()):
                self.send(info, signal.SIGTERM)
            if not self.wait_exit(5):
                for info in list(self.owned.values()):
                    self.send(info, signal.SIGKILL)
                ensure(self.wait_exit(3), 'Fixture processes did not stop; recovery evidence retained')
        for handle in self.handles:
            if isinstance(handle, subprocess.Popen):
                handle.wait(timeout=3)
            else:
                handle.close()
        ensure(self.service_snapshot() == self.services, 'Existing PHP-FPM/Docker/flowscope service identity changed')
        ensure(not existing_proxy_pids(), 'Unexpected Nginx/OpenResty remains; fixture evidence retained')
        json_write(self.root / 'CLEANUP.json', {'fixture_processes_stopped': True, 'services_unchanged': True,
                                              'pids': sorted(self.owned), 'evidence_retained': str(self.root)})

    def setup(self):
        self.app = self.root / 'app'
        for suffix in ('bootstrap/cache', 'storage/app', 'storage/logs', 'storage/framework/views', 'resources/views', 'public'):
            (self.app / suffix).mkdir(parents=True, exist_ok=True, mode=0o755)
        # JSON string literals are valid PHP literals for this canonical path;
        # prohibit interpolation characters to keep the generated bootstrap exact.
        ensure(not any(x in str(self.project) for x in ('$', '\\', '"', '\n', '\r')), 'Unsupported fixture project path')
        (self.app / 'bootstrap/app.php').write_text(BOOTSTRAP.replace('__PROJECT__', json.dumps(str(self.project))))
        (self.app / 'artisan').write_text(ARTISAN)
        (self.app / 'public/index.php').write_text(INDEX)
        for folder, dirs, files in os.walk(self.app):
            os.chown(folder, self.user.pw_uid, self.user.pw_gid)
            os.chmod(folder, 0o755)
            for name in files:
                os.chown(Path(folder) / name, self.user.pw_uid, self.user.pw_gid)
                os.chmod(Path(folder) / name, 0o644)
        # This subprocess is Laravel itself running under the actual app user.
        routes = self.run(['runuser', '-u', self.user.pw_name, '--', self.php, self.app / 'artisan', 'route:list', '--json'], 'native-routes')
        rows = json.loads(routes)
        ensure(any(x['uri'] == 'api/v2/server/handshake' and 'ServerController@handshake' in x['action'] for x in rows), 'Native handshake route missing')
        ports = set()
        while len(ports) < 4:
            ports.add(free_port())
        self.fpm_port, self.original_port, self.endpoint_port, self.modified_port = sorted(ports)
        self.fpm_config.write_text(f'''[global]
pid = {self.root}/php-fpm.pid
error_log = {self.root}/php-fpm.log
daemonize = no
[node_endpoint_cli_fixture]
user = {self.user.pw_name}
group = {grp.getgrgid(self.user.pw_gid).gr_name}
listen = 127.0.0.1:{self.fpm_port}
listen.allowed_clients = 127.0.0.1
pm = static
pm.max_children = 1
clear_env = yes
catch_workers_output = yes
''')
        os.chmod(self.fpm_config, 0o600)
        self.run([self.fpm, '--test', '--fpm-config', self.fpm_config], 'fpm-config-test')
        log = (self.root / 'fpm-process.log').open('wb')
        os.chmod(log.name, 0o600)
        self.handles.append(log)
        process = subprocess.Popen([self.fpm, '--nodaemonize', '--fpm-config', str(self.fpm_config)], stdout=log, stderr=log)
        self.handles.append(process)
        for _ in range(50):
            self.capture()
            if any(self.master_kind(info) == 'fpm' for info in self.owned.values()):
                try:
                    with socket.create_connection(('127.0.0.1', self.fpm_port), timeout=1):
                        break
                except OSError:
                    pass
            time.sleep(0.1)
        else:
            raise RuntimeError('Fixture FPM failed to start; see retained evidence')
        self.sites = self.root / 'sites'
        self.sites.mkdir(mode=0o755)
        self.site = self.sites / 'original.conf'
        self.site.write_text(f'''server {{
    listen 127.0.0.1:{self.original_port}; server_name original.example;
    root {self.app}/public; index index.php;
    location / {{ try_files $uri $uri/ /index.php?$query_string; }}
    location ~ \\.php$ {{
        include /etc/nginx/fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass 127.0.0.1:{self.fpm_port};
    }}
}}
''')
        os.chmod(self.site, 0o644)
        self.original_site = self.site.read_bytes()
        self.main.write_text(f'pid {self.root}/nginx.pid; error_log {self.root}/nginx-error.log; events {{}} '
                             f'http {{ access_log off; include {self.sites}/*.conf; }}\n')
        os.chmod(self.main, 0o644)
        self.original_main = self.main.read_bytes()
        self.run([self.nginx, '-p', str(self.root) + '/', '-c', self.main, '-t'], 'nginx-config-test')
        self.run([self.nginx, '-p', str(self.root) + '/', '-c', self.main], 'nginx-start')
        for _ in range(40):
            self.capture()
            if any(self.master_kind(info) == 'nginx' for info in self.owned.values()):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Fixture Nginx failed to start; see retained evidence')

    def cli(self, action, port=None, stdin=''):
        args = [sys.executable, self.project / 'tools/panel/manager.py', '--state', self.root / 'state',
                '--nginx', self.nginx, action]
        if action in ('install', 'modify'):
            args += ['--host', '127.0.0.1', '--port', str(port), '--include-dir', self.sites,
                     '--site-config', self.site, '--site-name', 'original.example', '--app', self.app,
                     '--deployment', 'fpm', '--backend', f'127.0.0.1:{self.fpm_port}', '--app-user', self.user.pw_name,
                     '--php', self.php, '--confirm', f'INSTALL 127.0.0.1:{port}']
        if action == 'uninstall':
            args += ['--confirm', 'UNINSTALL NODE ENDPOINT ALL NODES MIGRATED']
        output = self.run(args, action, stdin)
        decoder, last = json.JSONDecoder(), None
        offset = 0
        while offset < len(output):
            start = output.find('{', offset)
            if start < 0:
                break
            try:
                last, consumed = decoder.raw_decode(output[start:])
                offset = start + consumed
            except ValueError:
                offset = start + 1
        ensure(isinstance(last, dict), 'CLI did not return JSON object')
        return last

    def http(self, port, path, tls=False):
        if tls:
            status = json.loads((self.root / 'state/pki/state.json').read_text())
            ca = self.root / 'state/pki/ca' / status['active_ca'] / 'ca.pem'
            context = ssl.create_default_context(cafile=str(ca))
            connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=8)
        else:
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
        try:
            connection.request('GET', path, headers={'Host': f'127.0.0.1:{port}', 'Accept': 'application/json'})
            response = connection.getresponse()
            return response.status, response.read(65536), response.getheader('Location')
        finally:
            connection.close()

    def verify_original(self):
        ensure(self.site.read_bytes() == self.original_site and self.main.read_bytes() == self.original_main,
               'Original main/site configuration changed')
        code, body, _ = self.http(self.original_port, '/')
        ensure(code == 200 and body == b'original-native-website', 'Original native website failed')

    def verified_native_api(self, port):
        code, body, redirect = self.http(port, '/api/v2/server/handshake', True)
        payload = json.loads(body)
        ensure(code == 422 and not redirect and 'token' in payload.get('errors', {}), 'Expected real native missing-token 422')
        code, _, _ = self.http(port, '/admin', True)
        ensure(code == 404, 'Independent entry exposes unexpected admin path')

    def check(self, name):
        self.verify_original()
        self.results.append({'check': name, 'result': 'PASS'})

    def smoke(self):
        self.setup()
        self.verify_original()
        self.check('native_route_list_and_original_fpm_website')
        password = 'cli-fixture-only-long-passphrase'
        installed = self.cli('install', self.endpoint_port, password + '\n' + password + '\n')
        ensure(installed['changed'] is True and installed['status']['installed'], 'CLI install failed')
        self.verified_native_api(self.endpoint_port)
        self.check('cli_install_verified_https_native_missing_token_422')
        status = self.cli('status')
        ensure(status['installed'] and status['checks'].get('tls') == 'verified' and status['checks'].get('nginx') == 'loaded', 'CLI status did not verify live TLS')
        self.check('cli_status_live_tls')
        pointer = (self.root / 'state/last-backup').read_bytes()
        again = self.cli('install', self.endpoint_port)
        ensure(again['changed'] is False and (self.root / 'state/last-backup').read_bytes() == pointer, 'CLI install was not idempotent')
        self.check('cli_install_idempotence')
        modified = self.cli('modify', self.modified_port)
        ensure(modified['changed'] is True and modified['status']['endpoint']['port'] == self.modified_port, 'CLI modify failed')
        self.verified_native_api(self.modified_port)
        self.check('cli_modify_verified_https')
        rolled = self.cli('rollback', stdin='ROLLBACK NODE ENDPOINT\n')
        ensure(rolled['status']['endpoint']['port'] == self.endpoint_port, 'CLI rollback did not restore prior endpoint')
        self.verified_native_api(self.endpoint_port)
        self.check('cli_rollback_verified_https')
        removed = self.cli('uninstall')
        ensure(removed['changed'] is True and not removed['status']['installed']
               and not (self.sites / 'node-endpoint.conf').exists(), 'CLI uninstall failed')
        ensure(not self.cli('status')['installed'], 'CLI status still reports installed after uninstall')
        self.check('cli_uninstall_preserves_original_website')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-user', default='codex')
    args = parser.parse_args()
    fixture, error = None, None
    try:
        fixture = Fixture(args.app_user)
        print('Evidence directory: ' + str(fixture.root), flush=True)
        fixture.smoke()
    except Exception as exc:
        error = exc
    finally:
        if fixture is not None:
            try:
                fixture.cleanup()
            except Exception as exc:
                error = error or exc
                try:
                    fixture.workspace_owned()
                    json_write(fixture.root / 'CLEANUP_REQUIRED.json', {'error_type': type(exc).__name__, 'message': str(exc)})
                except Exception:
                    pass
            try:
                fixture.workspace_owned()
                json_write(fixture.root / 'RESULT.json', {'result': 'FAIL' if error else 'PASS',
                           'checks': fixture.results, 'error': str(error) if error else None,
                           'services_before': fixture.services, 'evidence': str(fixture.root),
                           'scope': 'real Root CLI + isolated native Laravel component + PHP-FPM; no production deployment'})
            except Exception:
                pass
    if error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    print(f'PASS: {len(fixture.results)} real CLI/FPM checks; evidence retained at {fixture.root}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
