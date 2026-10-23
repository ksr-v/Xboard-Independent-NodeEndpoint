<?php
// Fixed non-root CLI bridge. Only endpoint settings cross this private pipe.
declare(strict_types=1);
ini_set('display_errors', '0'); ini_set('log_errors', '0');
ob_start();
try {
    if (PHP_SAPI !== 'cli' || !function_exists('posix_geteuid') || posix_geteuid() === 0 || count($argv) !== 2) {
        throw new RuntimeException();
    }
    $raw = stream_get_contents(STDIN, 8193);
    if (!is_string($raw) || strlen($raw) > 8192) { throw new RuntimeException(); }
    $input = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
    if (!is_array($input) || !in_array($input['action'] ?? null, ['read', 'save'], true)) { throw new RuntimeException(); }
    $fields = $input['action'] === 'read' ? ['action'] : ['action', 'host', 'port', 'ws_mode', 'expected'];
    if (array_diff(array_keys($input), $fields)) { throw new RuntimeException(); }
    $path = realpath($argv[1]);
    if (!$path || !is_file($path . '/vendor/autoload.php')) { throw new RuntimeException(); }
    require $path . '/vendor/autoload.php';
    $app = require $path . '/bootstrap/app.php';
    $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    if (!\Plugin\NodeEndpoint\Services\Settings::pluginEnabled()) { throw new RuntimeException(); }
    // Lock and read in the same transaction as the write. MySQL/PostgreSQL
    // writers wait for this row; SQLite refuses a stale snapshot write rather
    // than replacing a concurrent administrator save.
    $output = \Illuminate\Support\Facades\DB::transaction(function () use ($input): array {
        $keys = array_keys(\Plugin\NodeEndpoint\Services\Endpoint::DEFAULTS);
        $row = \Illuminate\Support\Facades\DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
        $stored = $row ? json_decode($row->settings, true, 32, JSON_THROW_ON_ERROR) : [];
        if (!is_array($stored)) { throw new RuntimeException(); }
        $existing = array_replace(\Plugin\NodeEndpoint\Services\Endpoint::DEFAULTS, $stored);
        $current = array_replace(\Plugin\NodeEndpoint\Services\Endpoint::DEFAULTS,
            array_intersect_key($existing, array_flip($keys)));
        $status = 'read';
        if ($input['action'] === 'save') {
            if (!is_string($input['host'] ?? null) || !is_int($input['port'] ?? null)
                || !in_array($input['ws_mode'] ?? null, ['rewrite', 'rest'], true)) { throw new RuntimeException(); }
            $next = array_replace(\Plugin\NodeEndpoint\Services\Endpoint::DEFAULTS,
                \Plugin\NodeEndpoint\Services\Endpoint::validate(array_replace($current, [
                'enabled' => true, 'host' => $input['host'], 'port' => $input['port'],
                'https' => true, 'http_acknowledged' => false, 'ws_mode' => $input['ws_mode'],
            ]), (string) (admin_setting('app_url') ?: config('app.url'))));
            if ($next === $current) {
                $status = 'unchanged';
            } else {
                if (!is_array($input['expected'] ?? null)
                    || array_intersect_key($input['expected'], array_flip($keys)) !== $current) { throw new RuntimeException(); }
                $merged = array_replace($existing, $next, ['last_probe' => null]);
                if ($row) {
                    \Plugin\NodeEndpoint\Services\Settings::write($merged);
                } else {
                    // A concurrent first save must fail this unique insert; an
                    // updateOrInsert here could overwrite the new administrator row.
                    \Illuminate\Support\Facades\DB::table('node_endpoint_settings')->insert([
                        'id' => 1, 'settings' => json_encode($merged, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES),
                        'updated_at' => now(), 'created_at' => now(),
                    ]);
                }
                $current = $next; $status = 'saved';
            }
        }
        return ['status' => $status, 'settings' => $current, 'uid' => posix_geteuid()];
    });
    while (ob_get_level()) { ob_end_clean(); }
    echo json_encode($output, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
} catch (\Throwable) {
    while (ob_get_level()) { ob_end_clean(); }
    echo json_encode(['error' => 'endpoint_settings_unavailable']) . "\n";
    exit(1);
}
