<?php
/** Loopback-only UI fixture router. All tokens/data are synthetic test fixtures. */
declare(strict_types=1);
if (!in_array($_SERVER['REMOTE_ADDR'] ?? '', ['127.0.0.1', '::1'], true)) {
    http_response_code(403);
    exit;
}
if (!getenv('NE_FIXTURE_PATH')) throw new RuntimeException('Set NE_FIXTURE_PATH to an isolated fixture directory.');
require __DIR__ . '/bootstrap.php';
// Keep the reversible UI fixture aligned with edits in the development workspace.
copyTree($project . '/plugin/NodeEndpoint', $fixture . '/plugins/NodeEndpoint');
if (!App\Models\Plugin::where('code', 'node_endpoint')->exists()) {
    app(App\Services\Plugin\PluginManager::class)->install('node_endpoint');
    app(App\Services\Plugin\PluginManager::class)->enable('node_endpoint');
}
$request = Illuminate\Http\Request::capture();
$response = $kernel->handle($request);
$response->send();
$kernel->terminate($request, $response);
