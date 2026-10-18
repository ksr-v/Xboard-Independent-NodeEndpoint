<?php
/** Explicit local-only removal of NodeEndpoint recovery state. No Root listener changes. */
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
$options = getopt('', ['app:', 'confirm:']);
if (($options['confirm'] ?? '') !== 'PURGE_NODE_ENDPOINT' || !isset($options['app'])) {
    fwrite(STDERR, "Usage: php purge.php --app=/absolute/xboard --confirm=PURGE_NODE_ENDPOINT\nBack up NodeEndpoint settings first. Run as the Xboard application owner.\n");
    exit(2);
}
$path = realpath($options['app']);
if (!$path || !is_file($path . '/artisan') || !is_file($path . '/bootstrap/app.php')) {
    fwrite(STDERR, "Not a verified Laravel application root.\n"); exit(2);
}
require $path . '/vendor/autoload.php';
$app = require $path . '/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
if (App\Models\Plugin::where('code', 'node_endpoint')->where('is_enabled', true)->exists()) {
    fwrite(STDERR, "Disable or uninstall NodeEndpoint in the native plugin manager before purge.\n"); exit(2);
}
Illuminate\Support\Facades\Schema::dropIfExists('node_endpoint_settings');
Illuminate\Support\Facades\DB::table('migrations')->where('migration', '2026_10_09_000001_create_node_endpoint_settings')->delete();
$ca = storage_path('app/node_endpoint/trust-bundle.pem');
if (is_link($ca) || is_link(dirname($ca))) {
    fwrite(STDERR, "Settings removed; refused symlink CA path. Inspect manually.\n"); exit(2);
}
if (is_file($ca)) { unlink($ca); }
fwrite(STDOUT, "Only NodeEndpoint recovery settings, its migration marker and imported public CA were removed. Root listener, PKI and encrypted CA credentials were preserved. Back up the credential table together with the original APP_KEY before any separate credential cleanup.\n");
