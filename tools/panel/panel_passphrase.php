<?php
// Non-root application bridge. Secrets enter only the captured stdout pipe;
// neither application exceptions nor arbitrary bootstrap output are forwarded.
declare(strict_types=1);
ini_set('display_errors', '0'); ini_set('log_errors', '0');
$bufferLevel = ob_get_level(); ob_start();
try {
    if (PHP_SAPI !== 'cli' || (function_exists('posix_geteuid') && posix_geteuid() === 0)
        || count($argv) !== 2) { throw new RuntimeException(); }
    $raw = stream_get_contents(STDIN, 4097);
    if (!is_string($raw) || strlen($raw) > 4096) { throw new RuntimeException(); }
    $input = json_decode($raw, true, 8, JSON_THROW_ON_ERROR);
    if (!is_array($input) || !is_string($input['action'] ?? null) || !is_string($input['credential_id'] ?? null)
        || !preg_match('/^[a-f0-9]{32}$/D', $input['credential_id'])) { throw new RuntimeException(); }
    $action = $input['action'];
    $fields = match ($action) {
        'prepare' => ['action', 'credential_id', 'host'],
        'get' => ['action', 'credential_id', 'host'],
        'bind' => ['action', 'credential_id', 'host', 'fingerprint'],
        default => throw new RuntimeException(),
    };
    if (array_diff(array_keys($input), $fields)
        || ($action !== 'get' && !is_string($input['host'] ?? null))
        || (array_key_exists('host', $input) && !is_string($input['host']))
        || ($action === 'bind' && (!is_string($input['fingerprint'] ?? null)
            || !preg_match('/^[a-f0-9]{64}$/D', $input['fingerprint'])))) { throw new RuntimeException(); }
    $appPath = realpath($argv[1]);
    if (!$appPath || !is_file($appPath . '/vendor/autoload.php') || !is_file($appPath . '/bootstrap/app.php')) { throw new RuntimeException(); }
    require $appPath . '/vendor/autoload.php';
    $app = require $appPath . '/bootstrap/app.php';
    $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    if (!\Plugin\NodeEndpoint\Services\Settings::pluginEnabled()) { throw new RuntimeException(); }
    $vault = \Plugin\NodeEndpoint\Services\CaPassphrases::class;
    $id = $input['credential_id'];
    if ($action === 'prepare') {
        $output = ['credential_id' => $id, 'passphrase' => $vault::prepare($id, $input['host'])];
    } elseif ($action === 'get') {
        if (isset($input['host']) && !$vault::matchesHost($id, $input['host'])) { throw new RuntimeException(); }
        $output = ['credential_id' => $id, 'passphrase' => $vault::get($id)];
    } else {
        $vault::bind($id, $input['host'], $input['fingerprint']);
        $output = ['status' => 'stored', 'credential_id' => $id, 'fingerprint' => $input['fingerprint']];
    }
    while (ob_get_level() > $bufferLevel) { ob_end_clean(); }
    echo json_encode($output, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
} catch (\Throwable) {
    while (ob_get_level() > $bufferLevel) { ob_end_clean(); }
    echo json_encode(['error' => 'ca_credential_unavailable']) . "\n";
    exit(1);
}
