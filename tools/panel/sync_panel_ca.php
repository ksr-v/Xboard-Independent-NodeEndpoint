<?php
// Invoked as the existing application user. stdin contains public CA data only.
declare(strict_types=1);
ini_set('display_errors', '0');
ini_set('log_errors', '0');
ob_start();
$failure = 'application_failed';
try {
    if (PHP_SAPI !== 'cli' || (function_exists('posix_geteuid') && posix_geteuid() === 0)) {
        throw new RuntimeException('Non-root CLI required');
    }
    $raw = stream_get_contents(STDIN, 65537);
    if (strlen($raw) > 65536 || str_contains($raw, 'PRIVATE KEY')) {
        throw new RuntimeException('Public data only');
    }
    $payload = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
    if (!is_array($payload) || array_diff(array_keys($payload), ['pem', 'fingerprints', 'endpoint'])
        || !is_string($payload['pem'] ?? null) || !is_array($payload['fingerprints'] ?? null)
        || !is_array($payload['endpoint'] ?? null)) {
        throw new RuntimeException('Invalid payload');
    }
    $appPath = $argv[1] ?? '';
    require $appPath . '/vendor/autoload.php';
    $app = require $appPath . '/bootstrap/app.php';
    $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    $failure = 'plugin_disabled';
    if (!\Plugin\NodeEndpoint\Services\Settings::pluginEnabled()) {
        throw new RuntimeException('Plugin must be enabled');
    }
    $settings = \Plugin\NodeEndpoint\Services\Settings::read();
    $expected = $payload['endpoint'];
    $failure = 'endpoint_mismatch';
    if (($settings['host'] ?? null) !== ($expected['host'] ?? null)
        || ($settings['port'] ?? null) !== ($expected['port'] ?? null)
        || ($settings['https'] ?? null) !== true || ($expected['https'] ?? null) !== true) {
        throw new RuntimeException('Endpoint differs');
    }
    $failure = 'ca_invalid';
    $verified = \Plugin\NodeEndpoint\Services\Certificates::import($payload['pem'], $payload['fingerprints']);
    $failure = 'unsafe_storage';
    $target = \Plugin\NodeEndpoint\Services\Settings::caPath();
    $directory = dirname($target);
    $storage = storage_path();
    // All filesystem changes run without Root; refuse redirected/foreign targets.
    if ($target !== storage_path('app/node_endpoint/trust-bundle.pem')) {
        throw new RuntimeException('Unexpected storage path');
    }
    foreach ([$storage, $storage . '/app', $directory, $target] as $path) {
        if (is_link($path)) { throw new RuntimeException('Symlink storage refused'); }
    }
    if (!is_dir($directory) && !mkdir($directory, 0700, true)) {
        throw new RuntimeException('Cannot create plugin CA storage');
    }
    $uid = function_exists('posix_geteuid') ? posix_geteuid() : null;
    if (($uid !== null && fileowner($directory) !== $uid) || (fileperms($directory) & 0022)) {
        throw new RuntimeException('CA directory ownership or writes differ');
    }
    if (file_exists($target) && (!is_file($target) || ($uid !== null && fileowner($target) !== $uid)
        || (lstat($target)['nlink'] ?? 0) !== 1)) {
        throw new RuntimeException('Foreign CA destination refused');
    }
    $status = 'unchanged';
    if (!is_file($target) || file_get_contents($target) !== $verified['pem']) {
        $temporary = tempnam($directory, '.ca-sync-');
        if (!$temporary) { throw new RuntimeException('Cannot stage CA'); }
        try {
            if (!chmod($temporary, 0600)
                || file_put_contents($temporary, $verified['pem'], LOCK_EX) !== strlen($verified['pem'])
                || !rename($temporary, $target)) {
                throw new RuntimeException('Cannot store CA');
            }
        } finally {
            if (is_file($temporary)) { unlink($temporary); }
        }
        $status = 'imported';
    } elseif (!chmod($target, 0600)) {
        throw new RuntimeException('Cannot protect CA');
    }
    clearstatcache(true, $target);
    if ((fileperms($target) & 0777) !== 0600
        || hash_file('sha256', $target) !== hash('sha256', $verified['pem'])) {
        throw new RuntimeException('CA write verification failed');
    }
    $output = ['status' => $status, 'certificates' => count($verified['certificates']),
               'fingerprints' => array_column($verified['certificates'], 'fingerprint_sha256'), 'uid' => $uid];
    while (ob_get_level()) { ob_end_clean(); }
    echo json_encode($output, JSON_THROW_ON_ERROR) . "\n";
} catch (\Throwable $error) {
    while (ob_get_level()) { ob_end_clean(); }
    echo json_encode(['error' => $failure]) . "\n";
    exit(1);
}
