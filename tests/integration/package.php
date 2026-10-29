<?php
/** Native ZIP upload/install/enable smoke in a fresh, retained SQLite fixture. */
declare(strict_types=1);

$projectRoot = dirname(__DIR__, 2);
$packagePath = realpath($argv[1] ?? $projectRoot . '/dist/v1.1.0/NodeEndpoint-1.1.0.zip');
if (!$packagePath || !is_file($packagePath)) throw new RuntimeException('Build the package before running this smoke test.');
// Always isolate this run; never reuse the UI server or the 25-test fixture.
$packageFixture = __DIR__ . '/work/package-' . gmdate('Ymd-His') . '-' . bin2hex(random_bytes(3));
putenv('NE_FIXTURE_PATH=' . $packageFixture);
require __DIR__ . '/bootstrap.php';

use App\Services\Plugin\PluginManager;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\{Cache, Schema};

$checks = [];
function packageCheck(string $label, callable $check): void
{
    global $checks;
    $check();
    $checks[] = $label;
    echo 'PASS ' . $label . PHP_EOL;
}
function treeHashes(string $directory): array
{
    $hashes = [];
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($directory, FilesystemIterator::SKIP_DOTS)) as $item) {
        if ($item->isLink()) throw new RuntimeException('Unexpected symlink in package fixture.');
        if ($item->isFile()) $hashes[str_replace('\\', '/', substr($item->getPathname(), strlen($directory) + 1))] = hash_file('sha256', $item->getPathname());
    }
    ksort($hashes);
    return $hashes;
}

$packageFiles = [];
packageCheck('ZIP has one NodeEndpoint root, safe paths and version 1.1.0', function () use ($packagePath, &$packageFiles): void {
    $zip = new ZipArchive();
    eq($zip->open($packagePath), true, 'Cannot open distribution ZIP.');
    try {
        ok($zip->numFiles > 0, 'ZIP is empty.');
        for ($index = 0; $index < $zip->numFiles; ++$index) {
            $name = $zip->getNameIndex($index);
            ok(is_string($name) && str_starts_with($name, 'NodeEndpoint/') && !str_contains($name, '\\')
                && !str_contains($name, "\0") && !str_contains($name, ':')
                && !in_array('..', explode('/', $name), true) && !in_array('.', explode('/', $name), true), 'Unsafe or extra ZIP root.');
            if (str_ends_with($name, '/')) continue;
            $relative = substr($name, strlen('NodeEndpoint/'));
            ok(!array_key_exists($relative, $packageFiles), 'Duplicate ZIP entry.');
            $data = $zip->getFromIndex($index);
            ok(is_string($data), 'Unreadable ZIP entry.');
            $packageFiles[$relative] = hash('sha256', $data);
        }
        foreach (['config.json', 'Plugin.php', 'routes/api.php', 'routes/web.php', 'resources/views/index.html',
            'resources/assets/app.js', 'resources/assets/style.css', 'tools/panel/manager.py', 'tools/node/node_endpoint.py'] as $required) {
            ok(isset($packageFiles[$required]), 'Required file missing: ' . $required);
        }
        $config = json_decode($zip->getFromName('NodeEndpoint/config.json'), true, 512, JSON_THROW_ON_ERROR);
        eq($config['code'], 'node_endpoint');
        eq($config['version'], '1.1.0');
    } finally { $zip->close(); }
});

// bootstrap copies source packages for other tests; move only this fresh fixture's
// unused template aside so the native uploader must populate the package itself.
ok($fresh && str_starts_with(str_replace('\\', '/', realpath($fixture)), str_replace('\\', '/', realpath(__DIR__ . '/work')) . '/package-'), 'Fixture boundary verification failed.');
ok(rename($fixture . '/plugins/NodeEndpoint', $fixture . '/source-template-NodeEndpoint'), 'Cannot isolate the unused source template.');
$manager = app(PluginManager::class);
foreach (['smart_expiry', 'qrcodeextend', 'flowscope'] as $code) {
    eq($manager->install($code), true);
    eq($manager->enable($code), true);
}
$adminDirectory = public_path('assets/admin');
$adminBefore = treeHashes($adminDirectory);
$otherPluginsBefore = App\Models\Plugin::orderBy('code')->get()->toArray();

packageCheck('unmodified native PluginManager.upload imports exact ZIP bytes', function () use ($manager, $packagePath, $packageFiles): void {
    eq($manager->upload(new UploadedFile($packagePath, basename($packagePath), 'application/zip', null, true)), true);
    eq(App\Models\Plugin::where('code', 'node_endpoint')->exists(), false, 'Upload unexpectedly installed the plugin.');
    $installedFiles = treeHashes(base_path('plugins/NodeEndpoint'));
    $expected = $packageFiles;
    ksort($expected);
    eq($installedFiles, $expected, 'Native upload changed or omitted ZIP contents.');
});

packageCheck('native installation applies own schema and enables version 1.1.0', function () use ($manager): void {
    eq($manager->install('node_endpoint'), true);
    ok(Schema::hasTable('node_endpoint_settings'), 'Own migration missing.');
    eq((bool) App\Models\Plugin::where('code', 'node_endpoint')->value('is_enabled'), false);
    eq($manager->enable('node_endpoint'), true);
    $row = App\Models\Plugin::where('code', 'node_endpoint')->firstOrFail();
    eq($row->version, '1.1.0');
    eq((bool) $row->is_enabled, true);
});

packageCheck('uploaded page/assets and native authorized settings API respond', function () use ($adminToken, $packageFiles): void {
    $page = requestFixture('https://old.panel.example/node-endpoint');
    eq($page->getStatusCode(), 200);
    eq(hash_file('sha256', $page->getFile()->getPathname()), $packageFiles['resources/views/index.html']);
    foreach (['app' => 'app.js', 'style' => 'style.css'] as $name => $file) {
        $asset = requestFixture('https://old.panel.example/node-endpoint/assets/' . $name);
        eq($asset->getStatusCode(), 200);
        eq(hash_file('sha256', $asset->getFile()->getPathname()), $packageFiles['resources/assets/' . $file]);
    }
    eq(requestFixture('https://old.panel.example/api/v1/node-endpoint/settings')->getStatusCode(), 403);
    $response = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $adminToken);
    eq($response->getStatusCode(), 200, $response->getContent());
    eq(jsonResponse($response)['data']['version'], '1.1.0');
});

packageCheck('uploaded settings save and native scoped handshake work', function () use ($adminToken): void {
    $saved = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'POST', [
        'enabled' => true, 'host' => '192.0.2.10', 'port' => 8443, 'https' => true,
        'http_acknowledged' => false, 'ws_mode' => 'rewrite',
    ], $adminToken);
    eq($saved->getStatusCode(), 200, $saved->getContent());
    eq(jsonResponse($saved)['data']['urls']['url'], 'https://192.0.2.10:8443');
    Cache::put(App\WebSocket\NodeWorker::HEARTBEAT_CACHE_KEY, time(), 30);
    $handshake = requestFixture('https://192.0.2.10:8443/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']);
    eq($handshake->getStatusCode(), 200, $handshake->getContent());
    eq(jsonResponse($handshake)['websocket']['ws_url'], 'wss://192.0.2.10:8443/ws');
    $original = requestFixture('https://old.panel.example/api/v2/server/handshake', 'POST', ['token' => 'fixture-node-token']);
    eq(jsonResponse($original)['websocket']['ws_url'], 'wss://old.panel.example/ws');
});

packageCheck('three enabled plugins, complete Admin tree and Root evidence remain intact', function () use ($adminDirectory, $adminBefore, $otherPluginsBefore): void {
    eq(treeHashes($adminDirectory), $adminBefore, 'NodeEndpoint changed Admin resources.');
    eq(App\Models\Plugin::where('code', '!=', 'node_endpoint')->orderBy('code')->get()->toArray(), $otherPluginsBefore);
    eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')), 'root-owned: preserve');
    $admin = requestFixture('https://old.panel.example/fixture-admin');
    eq($admin->getStatusCode(), 200, $admin->getContent());
    ok(str_contains($admin->getContent(), 'data-flowscope-loader'), 'FlowScope dynamic Admin integration missing.');
});

echo json_encode(['result' => 'PASS', 'checks' => count($checks), 'package' => $packagePath,
    'files_verified' => count($packageFiles), 'fixture' => $fixture,
    'scope' => 'Actual native ZIP upload/install/enable and component HTTP requests; no browser, Linux/Nginx, Go TLS or production deployment'], JSON_UNESCAPED_SLASHES) . PHP_EOL;
