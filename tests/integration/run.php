<?php
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';
use App\Services\Plugin\PluginManager;
use Plugin\NodeEndpoint\Services\{Endpoint, Settings};
use Illuminate\Support\Facades\{Cache, DB, Schema};
use Illuminate\Foundation\Application;
$passed = 0;
function test(string $label, callable $body): void { global $passed; app(Illuminate\Cache\RateLimiter::class)->clear(sha1('1')); $body(); ++$passed; echo 'PASS ' . $label . PHP_EOL; }

test('first native install creates only its table and starts disabled', function (): void {
    $manager = app(PluginManager::class);
    eq($manager->install('node_endpoint'), true);
    ok(Schema::hasTable('node_endpoint_settings'), 'Settings migration not applied.');
    eq((bool) App\Models\Plugin::where('code', 'node_endpoint')->value('is_enabled'), false);
    eq(DB::table('production_evidence')->count(), 1);
});
test('node native handshake keeps original URL before plugin activation', function (): void {
    Cache::put(App\WebSocket\NodeWorker::HEARTBEAT_CACHE_KEY, time(), 30);
    $r = requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(jsonResponse($r)['websocket']['ws_url'], 'wss://old.panel.example/ws');
});
test('repeated native enable and per-request boot do not duplicate api middleware', function (): void {
    app(PluginManager::class)->enable('node_endpoint');
    app(PluginManager::class)->enable('node_endpoint');
    requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken']);
    $count = count(array_filter(app('router')->getMiddlewareGroups()['api'], fn ($v) => $v === Plugin\NodeEndpoint\Middleware\ScopedHandshake::class));
    eq($count, 1);
    eq(count(array_filter(app('router')->getMiddlewareGroups()['api'], fn ($v) => $v === Plugin\NodeEndpoint\Middleware\NativeMachineInstall::class)), 1);
});
test('real Sanctum anonymous and ordinary-user tokens cannot use Admin API', function (): void {
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings')->getStatusCode(), 403);
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['userToken'])->getStatusCode(), 403);
    $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(jsonResponse($r)['data']['version'], '1.1.2');
});
test('native generic config save cannot bypass the isolated endpoint settings API', function (): void {
    (new App\Services\Plugin\PluginConfigService())->updateConfig('node_endpoint', ['management_url' => '/attempted-change', 'enabled' => true, 'host' => 'evil.example', 'https' => false, 'http_acknowledged' => true]);
    eq(Settings::read()['enabled'], false);
    eq(Settings::read()['host'], '');
    eq(Settings::read()['https'], true);
});
test('independent UI shell and extensionless assets serve native state-gated responses', function (): void {
    $r = requestFixture('https://old.panel.example/node-endpoint');
    eq($r->getStatusCode(), 200);
    ok(str_contains($r->headers->get('Content-Security-Policy', ''), "frame-ancestors 'none'"), 'Shell CSP absent.');
    foreach (['app', 'style'] as $asset) eq(requestFixture('https://old.panel.example/node-endpoint/assets/' . $asset)->getStatusCode(), 200);
});
test('HTTP requires explicit risk acknowledgement, and invalid save is atomic', function (): void {
    $base = array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => '192.0.2.10', 'https' => false]);
    $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', $base, $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 422, $r->getContent());
    eq(Settings::read()['enabled'], false);
    $base['http_acknowledged'] = true;
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', $base, $GLOBALS['adminToken'])->getStatusCode(), 200);
    $base['https'] = true;
    $base['http_acknowledged'] = false;
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', $base, $GLOBALS['adminToken'])->getStatusCode(), 200);
    eq(Settings::read()['https'], true);
});
test('format and original-website conflicts are refused', function (): void {
    foreach (['https://example.com', '999.2.3.4', 'example.com:8443', '-bad.example', 'example.com/path'] as $host) {
        $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => $host]), $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 422, 'Bad host accepted: ' . $host);
    }
    $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => 'old.panel.example', 'port' => 443]), $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 422);
    eq(Settings::read()['host'], '192.0.2.10');
});
test('DNS absolute-name normalization and IPv6 URL generation remain consistent', function (): void {
    eq(Endpoint::host('Node.Example.'), 'node.example');
    eq(Endpoint::urls(array_replace(Endpoint::DEFAULTS, ['host' => '2001:db8::1']))['url'], 'https://[2001:db8::1]:8443');
    $before = Settings::read();
    Settings::write(array_replace($before, ['host' => '2001:db8::1']));
    $r = requestFixture('https://[2001:db8::1]:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(jsonResponse($r)['websocket']['ws_url'], 'wss://[2001:db8::1]:8443/ws');
    Settings::write($before);
    $website = $GLOBALS['testSettings']['app_url'];
    $GLOBALS['testSettings']['app_url'] = 'https://Old.Panel.Example.:443/';
    $conflict = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => 'old.panel.example', 'port' => 443]), $GLOBALS['adminToken']);
    $GLOBALS['testSettings']['app_url'] = $website;
    eq($conflict->getStatusCode(), 422, 'Absolute DNS website alias bypassed conflict check.');
});
test('request-scoped handshake rewrites independent IP and keeps old host unchanged', function (): void {
    $r = requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(jsonResponse($r)['websocket']['ws_url'], 'wss://192.0.2.10:8443/ws', json_encode(['settings' => Settings::read(), 'api' => app('router')->getMiddlewareGroups()['api'], 'request' => [request()->getHost(), request()->getPort(), request()->isSecure(), request()->route()?->getActionName()]]));
    eq(jsonResponse(requestFixture('https://old.panel.example/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']))['websocket']['ws_url'], 'wss://old.panel.example/ws');
    eq(jsonResponse(requestFixture('https://192.0.2.10:8443/api/fixture/other'))['websocket']['ws_url'], 'wss://other.example/ws');
    eq(admin_setting('server_ws_url'), 'wss://old.panel.example/ws');
    eq(admin_setting('app_url'), 'https://old.panel.example');
});
test('native ServerV2 node and machine credentials remain mandatory', function (): void {
    eq(requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'wrong'])->getStatusCode(), 422);
    $r = requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['machine_id' => $GLOBALS['machine']->id, 'token' => 'fixture-machine-token']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(jsonResponse($r)['websocket']['ws_url'], 'wss://192.0.2.10:8443/ws');
    eq(requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['machine_id' => $GLOBALS['machine']->id, 'token' => 'wrong'])->getStatusCode(), 401);
});
test('trusted forwarded origin resolves independent scheme/port, untrusted headers do not', function (): void {
    $headers = ['HTTP_X_FORWARDED_HOST' => '192.0.2.10:8443', 'HTTP_X_FORWARDED_PROTO' => 'https', 'HTTP_X_FORWARDED_PORT' => '8443'];
    $trusted = requestFixture('http://127.0.0.1/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token'], null, array_replace($headers, ['REMOTE_ADDR' => '127.0.0.1']));
    eq(jsonResponse($trusted)['websocket']['ws_url'], 'wss://192.0.2.10:8443/ws');
    $untrusted = requestFixture('http://old.panel.example/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token'], null, array_replace($headers, ['REMOTE_ADDR' => '198.51.100.20']));
    eq(jsonResponse($untrusted)['websocket']['ws_url'], 'wss://old.panel.example/ws');
});
test('WS heartbeat absent and explicit REST fallback never claim WSS success', function (): void {
    Cache::forget(App\WebSocket\NodeWorker::HEARTBEAT_CACHE_KEY);
    eq(jsonResponse(requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']))['websocket'], ['enabled' => false]);
    Cache::put(App\WebSocket\NodeWorker::HEARTBEAT_CACHE_KEY, time(), 30);
    Settings::write(array_replace(Settings::read(), ['ws_mode' => 'rest']));
    eq(jsonResponse(requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']))['websocket'], ['enabled' => false]);
    Settings::write(array_replace(Settings::read(), ['ws_mode' => 'rewrite']));
});
test('native disable makes retained routes inert and preserves Root-owned evidence', function (): void {
    app(PluginManager::class)->disable('node_endpoint');
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken'])->getStatusCode(), 404);
    eq(requestFixture('https://old.panel.example/node-endpoint')->getStatusCode(), 404);
    eq(requestFixture('https://old.panel.example/node-endpoint/assets/app')->getStatusCode(), 404);
    eq(jsonResponse(requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']))['websocket']['ws_url'], 'wss://old.panel.example/ws');
    eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')), 'root-owned: preserve');
    app(PluginManager::class)->enable('node_endpoint');
});
test('public Root status import validates schema, excludes credentials and cannot change endpoint configuration', function (): void {
    $url = 'https://old.panel.example/api/v1/node-endpoint/status-import';
    $status = ['schema_version' => 1, 'version' => '1.0.0', 'installed' => true,
        'endpoint' => ['host' => '192.0.2.10', 'port' => 8443, 'https' => true],
        'trust' => ['mode' => 'dual', 'fingerprints' => [str_repeat('a', 64), str_repeat('b', 64)]],
        'checks' => ['nginx' => 'passed'], 'generated_at' => gmdate('c'), 'extra_field' => 'must be discarded'];
    $before = Settings::read();
    $r = requestFixture($url, 'POST', ['status' => $status], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    $stored = Settings::read();
    eq($stored['root_status']['installed'], true);
    eq($stored['root_status']['trust'], $status['trust']);
    eq(isset($stored['root_status']['extra_field']), false);
    eq($stored['host'], $before['host']);
    foreach ([[], array_replace($status, ['schema_version' => 2]), array_replace($status, ['version' => str_repeat('x', 33)]),
        array_replace($status, ['token' => 'synthetic-secret']), array_replace($status, ['server' => ['key' => '-----BEGIN PRIVATE KEY-----']])] as $invalid) {
        eq(requestFixture($url, 'POST', ['status' => $invalid], $GLOBALS['adminToken'])->getStatusCode(), 422);
        eq(Settings::read(), $stored, 'Rejected status changed persisted settings.');
    }
});
function fixtureCertificate(string $commonName, string $extension = 'v3_ca', string $digest = 'sha256', int $bits = 2048, bool $ec = false): array
{
    $config = storage_path('app/fixture-openssl.cnf');
    file_put_contents($config, "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=fixture\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n[v3_leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:192.0.2.10\n");
    $keyOptions = $ec ? ['private_key_type' => OPENSSL_KEYTYPE_EC, 'private_key_bits' => 2048, 'curve_name' => 'prime256v1', 'config' => $config]
        : ['private_key_type' => OPENSSL_KEYTYPE_RSA, 'private_key_bits' => $bits, 'config' => $config];
    $key = openssl_pkey_new($keyOptions);
    if (!$key) throw new RuntimeException('OpenSSL fixture key generation failed.');
    $csr = openssl_csr_new(['commonName' => $commonName], $key, ['config' => $config, 'digest_alg' => $digest]);
    $certificate = openssl_csr_sign($csr, null, $key, 3650, ['config' => $config, 'x509_extensions' => $extension, 'digest_alg' => $digest]);
    if (!$certificate || !openssl_x509_export($certificate, $pem)) throw new RuntimeException('OpenSSL fixture certificate generation failed.');
    openssl_pkey_export($key, $private, null, ['config' => $config]);
    return ['pem' => $pem, 'private' => $private, 'fingerprint' => openssl_x509_fingerprint($pem, 'sha256')];
}
test('real OpenSSL CA import verifies fingerprint, public-only contents, root usages and modern key strength', function (): void {
    $ca = fixtureCertificate('First Fixture Root');
    $GLOBALS['fixtureCa'] = $ca;
    $url = 'https://old.panel.example/api/v1/node-endpoint/ca';
    $valid = requestFixture($url, 'POST', ['pem' => $ca['pem'], 'fingerprints' => [$ca['fingerprint']]], $GLOBALS['adminToken']);
    eq($valid->getStatusCode(), 200, $valid->getContent());
    eq(jsonResponse($valid)['data'][0]['fingerprint_sha256'], $ca['fingerprint']);
    $ec = fixtureCertificate('P-256 Fixture Root', 'v3_ca', 'sha256', 256, true);
    $ecResponse = requestFixture($url, 'POST', ['pem' => $ec['pem'], 'fingerprints' => [$ec['fingerprint']]], $GLOBALS['adminToken']);
    eq($ecResponse->getStatusCode(), 200, $ecResponse->getContent());
    eq(jsonResponse($ecResponse)['data'][0]['fingerprint_sha256'], $ec['fingerprint']);
    requestFixture($url, 'POST', ['pem' => $ca['pem'], 'fingerprints' => [$ca['fingerprint']]], $GLOBALS['adminToken']);
    $stored = file_get_contents(Settings::caPath());
    $leaf = fixtureCertificate('Fixture Server', 'v3_leaf');
    $weakKey = fixtureCertificate('Weak Fixture Root', 'v3_ca', 'sha256', 1024);
    $weakSignature = fixtureCertificate('SHA1 Fixture Root', 'v3_ca', 'sha1');
    foreach ([[$ca['pem'], str_repeat('0', 64)], [$ca['pem'] . $ca['private'], $ca['fingerprint']],
        [$leaf['pem'], $leaf['fingerprint']], [$weakKey['pem'], $weakKey['fingerprint']], [$weakSignature['pem'], $weakSignature['fingerprint']]] as [$pem, $fingerprint]) {
        $r = requestFixture($url, 'POST', ['pem' => $pem, 'fingerprints' => [$fingerprint]], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 422, $r->getContent());
        eq(file_get_contents(Settings::caPath()), $stored, 'Rejected CA import altered trust bundle.');
    }
    $metadata = jsonResponse(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken']))['data']['ca'][0];
    ok($metadata['remaining_days'] >= 3649, 'Actual certificate validity missing.');
    eq($metadata['status'], 'valid');
});
test('two independently pinned CA roots support dual trust and reject duplicate roots', function (): void {
    $first = $GLOBALS['fixtureCa'];
    $second = fixtureCertificate('Second Fixture Root');
    $GLOBALS['fixtureCa2'] = $second;
    $url = 'https://old.panel.example/api/v1/node-endpoint/ca';
    $r = requestFixture($url, 'POST', ['pem' => $first['pem'] . $second['pem'], 'fingerprints' => [$first['fingerprint'], $second['fingerprint']]], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    eq(count(jsonResponse($r)['data']), 2);
    $stored = file_get_contents(Settings::caPath());
    eq(requestFixture($url, 'POST', ['pem' => $first['pem'] . $first['pem'], 'fingerprints' => [$first['fingerprint'], $first['fingerprint']]], $GLOBALS['adminToken'])->getStatusCode(), 422);
    eq(file_get_contents(Settings::caPath()), $stored);
});
function nativeInstallOptions(array $data, string $key, App\Models\ServerMachine $machine, bool $endpointReady): array
{
    $domain = $data[$key];
    ok(str_contains($domain, 'ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.9/install.sh'), 'Domain installer is not independently maintained.');
    ok(!str_contains($domain, 'cedar2025') && !str_contains($domain, '--endpoint-ca'), 'Domain used official installer or private CA arguments.');
    foreach (['--panel'=>rtrim((string)admin_setting('app_url'),'/'), '--machine-id'=>(string)$machine->id,
        '--token'=>$machine->token, '--version'=>'v1.13-orphan.9'] as $arg=>$value) {
        ok(str_contains($domain, escapeshellarg($arg).' '.escapeshellarg($value)), 'Domain argument differs: '.$arg);
    }
    $options = $data['install_options'];
    eq($options['owner'], 'node_endpoint');
    eq($options['schema'], 1);
    eq($options['domain'], ['available' => true, 'command' => $domain]);
    eq($options['endpoint']['available'], $endpointReady);
    if ($endpointReady) {
        $command = $options['endpoint']['command'];
        ok(str_contains($command, Plugin\NodeEndpoint\Services\MachineInstallCommand::INSTALLER), 'Independent installer absent.');
        ok(!str_contains($command, 'cedar2025') && !str_contains($command, 'old.panel.example'), 'Original source or website leaked into independent target.');
        ok(str_contains($command, escapeshellarg('--machine-id') . ' ' . escapeshellarg((string) $machine->id)), 'Independent machine identity changed.');
        ok(str_contains($command, escapeshellarg('--token') . ' ' . escapeshellarg($machine->token)), 'Independent machine Token changed.');
    } else {
        eq($options['endpoint'], ['available' => false,
            'message' => '独立 IP 对接尚未就绪，请启用 HTTPS 独立入口并同步公共 CA。']);
    }
    return $options;
}

test('native machine commands require Admin and use independent installation sources for domain and IP', function (): void {
    $url = 'https://old.panel.example/api/v2/admin/server/machine/installCommand';
    eq(requestFixture($url, 'GET', ['id' => $GLOBALS['machine']->id])->getStatusCode(), 403);
    eq(requestFixture($url, 'GET', ['id' => $GLOBALS['machine']->id], $GLOBALS['userToken'])->getStatusCode(), 403);
    $count = App\Models\ServerMachine::count();
    foreach ([null, $GLOBALS['userToken']] as $token) {
        eq(requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
            ['name' => 'Unauthorized new fixture'], $token)->getStatusCode(), 403);
    }
    eq(App\Models\ServerMachine::count(), $count, 'Unauthorized creation succeeded.');
    $r = requestFixture($url, 'GET', ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    $options = nativeInstallOptions(jsonResponse($r)['data'], 'command', $GLOBALS['machine'], true);
    $command = $options['endpoint']['command'];
    ok(str_contains($command, escapeshellarg('--panel') . ' ' . escapeshellarg('https://192.0.2.10:8443')), 'Configured IP absent.');
    ok(str_contains($command, escapeshellarg('--endpoint-ca-base64') . ' ' . escapeshellarg(base64_encode(file_get_contents(Settings::caPath())))), 'Public CA not embedded.');
    ok(str_contains($command, escapeshellarg('--endpoint-ca-fingerprint') . ' ' . escapeshellarg($GLOBALS['fixtureCa']['fingerprint'] . ',' . $GLOBALS['fixtureCa2']['fingerprint'])), 'Independent pins absent.');
    ok(str_contains($r->headers->get('Cache-Control'), 'no-store'), 'Sensitive command cacheable.');
    eq(admin_setting('app_url'), 'https://old.panel.example');
});

test('native new machine creation offers both commands for one identity and updates remain native', function (): void {
    $url = 'https://old.panel.example/api/v2/admin/server/machine/save';
    $count = App\Models\ServerMachine::count();
    $r = requestFixture($url, 'POST', ['name' => 'Native new fixture', 'notes' => 'preserved'], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    $data = jsonResponse($r)['data'];
    $machine = App\Models\ServerMachine::findOrFail($data['id']);
    eq(App\Models\ServerMachine::count(), $count + 1, 'Creation changed more than one machine.');
    eq($data['token'], $machine->token);
    eq($machine->notes, 'preserved');
    nativeInstallOptions($data, 'install_command', $machine, true);
    ok(str_contains($r->headers->get('Cache-Control'), 'no-store'), 'Created sensitive commands cacheable.');
    eq(jsonResponse(requestFixture($url, 'POST', ['id' => $machine->id, 'name' => 'Renamed'], $GLOBALS['adminToken']))['data'], true);
    eq($machine->fresh()->token, $data['token'], 'Name update changed machine identity.');
});

test('missing or invalid CA disables only the IP option and never prevents native creation', function (): void {
    $pem = file_get_contents(Settings::caPath());
    try {
        foreach ([null, 'not a certificate', $pem . $GLOBALS['fixtureCa']['private']] as $contents) {
            if ($contents === null) { unlink(Settings::caPath()); }
            else { file_put_contents(Settings::caPath(), $contents); }
            $count = App\Models\ServerMachine::count();
            $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand', 'GET',
                ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
            eq($r->getStatusCode(), 200, $r->getContent());
            nativeInstallOptions(jsonResponse($r)['data'], 'command', $GLOBALS['machine'], false);
            eq(App\Models\ServerMachine::count(), $count, 'Command lookup created a machine.');
            ok(str_contains($r->headers->get('Cache-Control'), 'no-store'), 'Unavailable IP response cacheable.');
            $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
                ['name' => 'Native CA-unavailable fixture'], $GLOBALS['adminToken']);
            eq($r->getStatusCode(), 200, $r->getContent());
            $data = jsonResponse($r)['data'];
            $created = App\Models\ServerMachine::findOrFail($data['id']);
            eq($data['token'], $created->token);
            nativeInstallOptions($data, 'install_command', $created, false);
            eq(App\Models\ServerMachine::count(), $count + 1, 'Unavailable CA caused partial or duplicate creation.');
            ok(str_contains($r->headers->get('Cache-Control'), 'no-store'), 'Created fallback commands cacheable.');
        }
    } finally { file_put_contents(Settings::caPath(), $pem); }
});

test('HTTP independent entry keeps native domain commands and creation available', function (): void {
    $before = Settings::read();
    Settings::write(array_replace($before, ['https' => false, 'http_acknowledged' => true]));
    try {
        $count = App\Models\ServerMachine::count();
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand', 'GET',
            ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        nativeInstallOptions(jsonResponse($r)['data'], 'command', $GLOBALS['machine'], false);
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
            ['name' => 'Native HTTP fixture'], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        $data = jsonResponse($r)['data'];
        nativeInstallOptions($data, 'install_command', App\Models\ServerMachine::findOrFail($data['id']), false);
        eq(App\Models\ServerMachine::count(), $count + 1);
    } finally { Settings::write($before); }
});

test('disabled independent entry advertises only its unavailable IP option while domain remains usable', function (): void {
    $before = Settings::read();
    Settings::write(array_replace($before, ['enabled' => false]));
    try {
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand', 'GET',
            ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        nativeInstallOptions(jsonResponse($r)['data'], 'command', $GLOBALS['machine'], false);
        $count = App\Models\ServerMachine::count();
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
            ['name' => 'Native disabled-entry fixture'], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        $data = jsonResponse($r)['data'];
        nativeInstallOptions($data, 'install_command', App\Models\ServerMachine::findOrFail($data['id']), false);
        eq(App\Models\ServerMachine::count(), $count + 1);
    } finally { Settings::write($before); }
});

test('disabled native plugin leaves domain lookup and new machine response untouched', function (): void {
    app(PluginManager::class)->disable('node_endpoint');
    try {
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand', 'GET',
            ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        $data = jsonResponse($r)['data'];
        eq(array_keys($data), ['command']);
        ok(str_contains($data['command'], 'cedar2025/xboard-node/dev/install.sh'), 'Disabled plugin changed native source.');
        $count = App\Models\ServerMachine::count();
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
            ['name' => 'Native disabled-plugin fixture'], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        $data = jsonResponse($r)['data'];
        eq(array_keys($data), ['id', 'token', 'install_command']);
        ok(str_contains($data['install_command'], 'cedar2025/xboard-node/dev/install.sh'), 'Disabled creation changed native source.');
        eq(App\Models\ServerMachine::count(), $count + 1);
    } finally { app(PluginManager::class)->enable('node_endpoint'); }
});

test('native machine validation errors and updates remain unchanged with CA missing', function (): void {
    $pem = file_get_contents(Settings::caPath());
    unlink(Settings::caPath());
    try {
        $count = App\Models\ServerMachine::count();
        foreach ([
            ['installCommand', 'GET', [], 'id'],
            ['installCommand', 'GET', ['id' => 999999], 'id'],
            ['save', 'POST', [], 'name'],
            ['save', 'POST', ['name' => 'Invalid ID', 'id' => 999999], 'id'],
        ] as [$action, $method, $params, $field]) {
            $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/' . $action,
                $method, $params, $GLOBALS['adminToken']);
            eq($r->getStatusCode(), 422, $r->getContent());
            $data = jsonResponse($r);
            ok(isset($data['errors'][$field]), 'Original validation error absent.');
            ok(!str_contains($r->getContent(), 'install_options'), 'Validation error gained install choices.');
        }
        $r = requestFixture('https://old.panel.example/api/v2/admin/server/machine/save', 'POST',
            ['id' => $GLOBALS['machine']->id, 'name' => 'Renamed without CA', 'notes' => 'native update'], $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        eq(jsonResponse($r)['data'], true);
        eq($GLOBALS['machine']->fresh()->notes, 'native update');
        eq($GLOBALS['machine']->fresh()->token, 'fixture-machine-token');
        eq(App\Models\ServerMachine::count(), $count);
    } finally { file_put_contents(Settings::caPath(), $pem); }
});

test('both installation options use the current token after the native reset route', function (): void {
    $machine = App\Models\ServerMachine::create(['name' => 'Token rotation fixture', 'token' => 'fixture-prior-token', 'is_active' => true]);
    $url = 'https://old.panel.example/api/v2/admin/server/machine/installCommand';
    $r = requestFixture($url, 'GET', ['id' => $machine->id], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    nativeInstallOptions(jsonResponse($r)['data'], 'command', $machine, true);
    foreach ([null, $GLOBALS['userToken']] as $token) {
        eq(requestFixture('https://old.panel.example/api/v2/admin/server/machine/resetToken', 'POST',
            ['id' => $machine->id], $token)->getStatusCode(), 403);
        eq($machine->fresh()->token, 'fixture-prior-token');
    }
    $reset = requestFixture('https://old.panel.example/api/v2/admin/server/machine/resetToken', 'POST',
        ['id' => $machine->id], $GLOBALS['adminToken']);
    eq($reset->getStatusCode(), 200, $reset->getContent());
    $token = jsonResponse($reset)['data']['token'];
    ok($token !== 'fixture-prior-token', 'Native token reset did not rotate identity.');
    $machine->refresh();
    eq($machine->token, $token);
    $r = requestFixture($url, 'GET', ['id' => $machine->id], $GLOBALS['adminToken']);
    eq($r->getStatusCode(), 200, $r->getContent());
    nativeInstallOptions(jsonResponse($r)['data'], 'command', $machine, true);
    ok(!str_contains($r->getContent(), 'fixture-prior-token'), 'Stale credential remained in an installation option.');
});

test('domain installation preserves the website URL and uses request origin only as its native fallback', function (): void {
    $before=$GLOBALS['testSettings']['app_url'];
    try {
        foreach (['https://old.panel.example:9443///'=>'https://old.panel.example:9443',
            ''=>'https://fallback.panel.example:9443'] as $website=>$expected) {
            $GLOBALS['testSettings']['app_url']=$website;
            $r=requestFixture('https://fallback.panel.example:9443/api/v2/admin/server/machine/installCommand',
                'GET',['id'=>$GLOBALS['machine']->id],$GLOBALS['adminToken']);
            eq($r->getStatusCode(),200,$r->getContent());
            $data=jsonResponse($r)['data'];
            eq($data['command'],$data['install_options']['domain']['command']);
            ok(str_contains($data['command'],escapeshellarg('--panel').' '.escapeshellarg($expected)),'Wrong domain/fallback origin.');
            ok(!str_contains($data['command'],'cedar2025'),'Fallback used the official installer.');
            eq(admin_setting('app_url'),$website,'Command generation changed global website setting.');
        }
    } finally {$GLOBALS['testSettings']['app_url']=$before;}
});

test('independent domain and IP commands preserve shell-sensitive machine credentials', function (): void {
    $machine=$GLOBALS['machine']->fresh();
    $previous=$machine->token;
    try {
        $machine->token='fixture quote \' space ; $(fixture) & end';
        $machine->save();
        $r=requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand','GET',
            ['id'=>$machine->id],$GLOBALS['adminToken']);
        eq($r->getStatusCode(),200,$r->getContent());
        $options=nativeInstallOptions(jsonResponse($r)['data'],'command',$machine,true);
        ok($options['domain']['command']!==$options['endpoint']['command'],'Distinct targets lost.');
    } finally {$machine->token=$previous;$machine->save();}
});

test('native plugin upgrade from 1.0.2 to 1.1.2 preserves settings, CA, machine credentials and enabled state', function (): void {
    $before = Settings::read();
    $ca = file_get_contents(Settings::caPath());
    $token = $GLOBALS['machine']->fresh()->token;
    App\Models\Plugin::where('code', 'node_endpoint')->update(['version' => '1.0.2']);
    eq(app(PluginManager::class)->update('node_endpoint'), true);
    eq(App\Models\Plugin::where('code', 'node_endpoint')->value('version'), '1.1.2');
    eq((bool) App\Models\Plugin::where('code', 'node_endpoint')->value('is_enabled'), true);
    eq(Settings::read(), $before);
    eq(file_get_contents(Settings::caPath()), $ca);
    eq($GLOBALS['machine']->fresh()->token, $token);
});

test('command generation supports complete mode/kernel/architecture matrix without Token argv', function (): void {
    foreach (['node', 'machine'] as $mode) foreach (['singbox', 'xray'] as $kernelType) foreach (['amd64', 'arm64'] as $architecture) {
        $params = ['mode' => $mode, 'kernel' => $kernelType, 'architecture' => $architecture, 'instance' => 'fixture-instance', 'identity_id' => 7, 'node_type' => 'shadowsocks'];
        $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST', $params, $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200, $r->getContent());
        $data = jsonResponse($r)['data'];
        ok(str_contains($data['prepare'], "'--ca-fingerprint' '" . $GLOBALS['fixtureCa']['fingerprint'] . ',' . $GLOBALS['fixtureCa2']['fingerprint'] . "'"), 'Dual CA trust not fully pinned.');
        ok(str_contains($data['prepare'], "'--mode' '{$mode}'"), 'Mode absent.');
        ok(str_contains($data['prepare'], "'--architecture' '{$architecture}'"), 'Architecture absent.');
        ok(!str_contains($data['prepare'], 'fixture-node-token') && !str_contains($data['prepare'], '--token'), 'Token exposed in generated command.');
        ok(str_contains($data['install'], 'install-new'), 'Migration/new install boundary lost.');
    }
    $params = ['mode' => 'machine', 'kernel' => 'xray', 'architecture' => 'amd64', 'instance' => 'bad;touch hacked', 'identity_id' => 7];
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST', $params, $GLOBALS['adminToken'])->getStatusCode(), 422);
});
test('command API rejects unsupported Xray protocols and accepts sing-box counterparts', function (): void {
    foreach (['tuic', 'anytls'] as $protocol) {
        $params = ['mode' => 'node', 'kernel' => 'xray', 'architecture' => 'amd64', 'instance' => 'protocol-fixture', 'identity_id' => 7, 'node_type' => $protocol];
        eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST', $params, $GLOBALS['adminToken'])->getStatusCode(), 422);
        $params['kernel'] = 'singbox';
        eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST', $params, $GLOBALS['adminToken'])->getStatusCode(), 200);
    }
    foreach (['xray', 'singbox'] as $kernelType) {
        $params = ['mode' => 'node', 'kernel' => $kernelType, 'architecture' => 'amd64', 'instance' => 'protocol-fixture', 'identity_id' => 7, 'node_type' => 'hysteria2'];
        $r = requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST', $params, $GLOBALS['adminToken']);
        eq($r->getStatusCode(), 200);
        ok(str_contains(jsonResponse($r)['data']['prepare'], "'--node-type' 'hysteria'"), 'Hysteria authentication alias was not normalized.');
    }
});
test('native API throttle rejects the thirty-first authenticated request', function (): void {
    for ($i = 0; $i < 30; $i++) eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken'])->getStatusCode(), 200);
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken'])->getStatusCode(), 429);
});
test('explicit local purge refuses enabled plugin and preserves all imported trust', function (): void {
    $before = Settings::read();
    $trust = file_get_contents(Settings::caPath());
    $r = runFixturePurge('PURGE_NODE_ENDPOINT');
    eq($r->getExitCode(), 2, $r->getErrorOutput());
    eq(Settings::read(), $before);
    eq(file_get_contents(Settings::caPath()), $trust);
});
test('actual three-plugin lifecycle coexistence leaves Admin resources byte intact', function (): void {
    foreach (['smart_expiry', 'qrcodeextend', 'flowscope'] as $code) { app(PluginManager::class)->install($code); app(PluginManager::class)->enable($code); }
    $bundle = public_path('assets/admin/assets/index-CEIYH7i8.js');
    $before = hash_file('sha256', $bundle);
    $pluginsBefore = App\Models\Plugin::where('code', '!=', 'node_endpoint')->get()->toArray();
    app(PluginManager::class)->disable('node_endpoint');
    app(PluginManager::class)->enable('node_endpoint');
    requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken']);
    eq(hash_file('sha256', $bundle), $before);
    eq(App\Models\Plugin::where('code', '!=', 'node_endpoint')->get()->toArray(), $pluginsBefore);
    $html = requestFixture('https://old.panel.example/fixture-admin')->getContent();
    ok(str_contains($html, 'data-flowscope-loader'), 'FlowScope composer no longer active.');
    ok(str_contains($html, 'assets/index-CEIYH7i8.js'), 'Original Admin entry removed.');
});
test('native uninstall removes plugin state and consistently retains restore settings/business/Root/other plugins', function (): void {
    $bundleBefore = hash_file('sha256', public_path('assets/admin/assets/index-CEIYH7i8.js'));
    $pluginsBefore = App\Models\Plugin::where('code', '!=', 'node_endpoint')->get()->toArray();
    $evidence = DB::table('production_evidence')->get()->toJson();
    app(PluginManager::class)->uninstall('node_endpoint');
    eq(Schema::hasTable('node_endpoint_settings'), true);
    eq(Settings::read()['host'], '192.0.2.10');
    eq(App\Models\Plugin::where('code', 'node_endpoint')->exists(), false);
    eq(DB::table('production_evidence')->get()->toJson(), $evidence);
    eq(App\Models\Plugin::where('code', '!=', 'node_endpoint')->get()->toArray(), $pluginsBefore);
    eq(hash_file('sha256', public_path('assets/admin/assets/index-CEIYH7i8.js')), $bundleBefore);
    eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')), 'root-owned: preserve');
    eq(App\Models\ServerMachine::find($GLOBALS['machine']->id)->token, 'fixture-machine-token');
    eq(requestFixture('https://old.panel.example/node-endpoint')->getStatusCode(), 404);
});
function runFixturePurge(string $confirmation): Symfony\Component\Process\Process
{
    global $fixture, $project;
    if (!is_dir($fixture . '/vendor')) mkdir($fixture . '/vendor', 0700);
    file_put_contents($fixture . '/artisan', "<?php // isolated application marker\n");
    file_put_contents($fixture . '/vendor/autoload.php', '<?php require ' . var_export(__DIR__ . '/vendor/autoload.php', true) . ';');
    file_put_contents($fixture . '/bootstrap/app.php', '<?php require ' . var_export(__DIR__ . '/bootstrap.php', true) . '; return $app;');
    $process = new Symfony\Component\Process\Process([PHP_BINARY, '-c', php_ini_loaded_file(), $project . '/tools/plugin/purge.php', '--app=' . $fixture, '--confirm=' . $confirmation], $project, ['NE_FIXTURE_PATH' => $fixture]);
    $process->run();
    return $process;
}
test('explicit local purge requires exact confirmation and removes only owned recovery data', function (): void {
    $before = App\Models\Plugin::get()->toArray();
    $evidence = DB::table('production_evidence')->get()->toJson();
    $rejected = runFixturePurge('wrong');
    eq($rejected->getExitCode(), 2, $rejected->getErrorOutput());
    ok(Schema::hasTable('node_endpoint_settings'), 'Rejected purge removed settings.');
    $purged = runFixturePurge('PURGE_NODE_ENDPOINT');
    eq($purged->getExitCode(), 0, $purged->getErrorOutput());
    eq(Schema::hasTable('node_endpoint_settings'), false);
    eq(is_file(Settings::caPath()), false);
    eq(DB::table('migrations')->where('migration', '2026_10_09_000001_create_node_endpoint_settings')->exists(), false);
    eq(App\Models\Plugin::get()->toArray(), $before);
    eq(DB::table('production_evidence')->get()->toJson(), $evidence);
    eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')), 'root-owned: preserve');
});
test('reinstall after explicit purge and reverse foreign-plugin order preserve all current Admin changes', function (): void {
    app(PluginManager::class)->install('node_endpoint');
    app(PluginManager::class)->enable('node_endpoint');
    foreach (['smart_expiry', 'qrcodeextend'] as $code) app(PluginManager::class)->disable($code);
    foreach (['qrcodeextend', 'smart_expiry'] as $code) app(PluginManager::class)->enable($code);
    $bundle = public_path('assets/admin/assets/index-CEIYH7i8.js');
    $before = hash_file('sha256', $bundle);
    app(PluginManager::class)->disable('node_endpoint');
    app(PluginManager::class)->enable('node_endpoint');
    eq(hash_file('sha256', $bundle), $before);
    $html = requestFixture('https://old.panel.example/fixture-admin')->getContent();
    ok(str_contains($html, 'data-flowscope-loader'), 'FlowScope integration disappeared in reverse ordering.');
    eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')), 'root-owned: preserve');
});
echo json_encode(['result' => 'PASS', 'tests' => $passed, 'fixture' => $fixture,
    'framework' => Application::VERSION, 'scope' => 'Native PHP/Laravel/Sanctum components with SQLite; no Linux/Nginx/Go/real WebSocket deployment']) . PHP_EOL;
