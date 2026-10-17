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
// Serve the audited Admin build only inside this loopback fixture.
$path = rawurldecode((string) parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH));
if (str_starts_with($path, '/assets/admin/') && !in_array('..', explode('/', $path), true)) {
    $asset = realpath($fixture . '/public' . $path);
    $root = realpath($fixture . '/public/assets/admin');
    if ($asset && $root && str_starts_with($asset, $root . DIRECTORY_SEPARATOR) && is_file($asset)) {
        $type = ['js' => 'text/javascript', 'css' => 'text/css', 'json' => 'application/json',
            'woff' => 'font/woff', 'woff2' => 'font/woff2', 'svg' => 'image/svg+xml',
            'png' => 'image/png', 'ico' => 'image/x-icon'][pathinfo($asset, PATHINFO_EXTENSION)] ?? 'application/octet-stream';
        header('Content-Type: ' . $type); readfile($asset); exit;
    }
}
Illuminate\Support\Facades\Route::get('admin/{path?}', fn () => view('admin',
    ['title' => 'Native install choice fixture', 'version' => 'fixture', 'logo' => '', 'secure_path' => 'admin']))->where('path', '.*');
Illuminate\Support\Facades\Route::post('api/fixture/node-endpoint-state', function (Illuminate\Http\Request $request) {
    $manager=app(App\Services\Plugin\PluginManager::class);
    if ($request->boolean('enabled')) $manager->enable('node_endpoint');
    else $manager->disable('node_endpoint');
    return response()->json(['enabled'=>$request->boolean('enabled')]);
})->middleware(['api','admin']);
if (!App\Models\Plugin::where('code', 'node_endpoint')->exists()) {
    app(App\Services\Plugin\PluginManager::class)->install('node_endpoint');
    app(App\Services\Plugin\PluginManager::class)->enable('node_endpoint');
}
$request = Illuminate\Http\Request::capture();
$response = $kernel->handle($request);
$response->send();
$kernel->terminate($request, $response);
