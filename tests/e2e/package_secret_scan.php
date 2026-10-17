<?php
declare(strict_types=1);

// Root-private test helper. Capture exact owned test credentials before cleanup,
// then scan three explicitly pinned rebuilt ZIPs without extracting their files.
// Retired Sanctum plaintext is not retained by token renewal: scan native token
// secret candidates by their privately journalled digest as well as exact values.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
ini_set('display_errors', '0'); umask(0077); $level = ob_get_level(); ob_start();
function scanRequire(bool $ok): void { if (!$ok) throw new RuntimeException(); }
function scanPrivate(string $path): void {
    scanRequire(is_file($path) && !is_link($path));
    if (PHP_OS_FAMILY !== 'Windows') scanRequire(fileowner($path) === posix_geteuid() && (fileperms($path) & 0077) === 0);
}
function scanRead(string $path): array {
    scanPrivate($path); $value = json_decode((string) file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
    scanRequire(is_array($value)); return $value;
}
function scanWrite(string $path, array $value): void {
    scanRequire(!is_link($path)); if (file_exists($path)) scanPrivate($path);
    $temporary = $path . '.tmp-' . bin2hex(random_bytes(8)); $handle = fopen($temporary, 'x'); scanRequire((bool) $handle);
    try {
        scanRequire(PHP_OS_FAMILY === 'Windows' || chmod($temporary, 0600));
        $encoded = json_encode($value, JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT) . "\n";
        scanRequire(fwrite($handle, $encoded) === strlen($encoded)); fflush($handle);
    } finally { fclose($handle); }
    scanRequire(rename($temporary, $path));
}
try {
    $action = $argv[1] ?? ''; scanRequire(in_array($action, ['capture', 'scan'], true)); $options = [];
    foreach (array_slice($argv, 2) as $argument) {
        scanRequire((bool) preg_match('/^--(stage|run|app|confirm|manifest-file)=(.+)$/D', $argument, $match) && !isset($options[$match[1]]));
        $options[$match[1]] = $match[2];
    }
    $stage = realpath($options['stage'] ?? ''); $run = $options['run'] ?? '';
    scanRequire((bool) $stage && !is_link($options['stage']) && (bool) preg_match('/^[a-z0-9][a-z0-9-]{5,27}$/D', $run));
    if (PHP_OS_FAMILY !== 'Windows') scanRequire(posix_geteuid() === 0 && fileowner($stage) === 0 && (fileperms($stage) & 0077) === 0);
    scanPrivate($stage . '/.node-endpoint-e2e-owner');
    $archive = $stage . '/' . $run . '-secret-scan-values.json';
    $lockPath = $stage . '/' . $run . '-secret-scan.lock'; scanRequire(!is_link($lockPath));
    if (file_exists($lockPath)) scanPrivate($lockPath);
    $lock = fopen($lockPath, 'c'); scanRequire($lock && flock($lock, LOCK_EX | LOCK_NB));
    if (PHP_OS_FAMILY !== 'Windows') scanRequire(chmod($lockPath, 0600));
    if ($action === 'capture') {
        scanRequire(($options['confirm'] ?? '') === 'CAPTURE_OWNED_TEST_SECRET_SCAN');
        $root = realpath($options['app'] ?? ''); scanRequire((bool) $root && is_file($root . '/vendor/autoload.php'));
        require $root . '/vendor/autoload.php'; $app = require $root . '/bootstrap/app.php';
        $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap();
        $owned = scanRead($stage . '/' . $run . '-state.json');
        $credentials = scanRead($stage . '/' . $run . '-credentials.json');
        $plugin = scanRead($stage . '/' . $run . '-plugin-state.json');
        $tokens = scanRead($stage . '/' . $run . '-plugin-tokens.json');
        $auth = scanRead($stage . '/' . $run . '-auth.json');
        scanRequire(($owned['schema'] ?? null) === 1 && ($owned['run'] ?? null) === $run && ($owned['app_path'] ?? null) === $root && ($owned['status'] ?? null) === 'created');
        scanRequire(($credentials['schema'] ?? null) === 1 && ($credentials['run_id'] ?? null) === $run && ($credentials['user']['id'] ?? null) === (int) $owned['ids']['user'] && ($credentials['machine']['id'] ?? null) === (int) $owned['ids']['machine']);
        scanRequire(($plugin['schema'] ?? null) === 1 && ($plugin['run'] ?? null) === $run && ($plugin['app_path'] ?? null) === $root && ($plugin['status'] ?? null) === 'installed' && ($tokens['run'] ?? null) === $run);
        $user = \App\Models\User::findOrFail((int) $owned['ids']['user']);
        $machine = \App\Models\ServerMachine::findOrFail((int) $owned['ids']['machine']);
        scanRequire($user->email === $run . '@node-endpoint-e2e.invalid' && $user->remarks === 'NE-E2E-' . $run && !$user->is_admin && !$user->is_staff && $user->uuid === $credentials['user']['uuid']);
        scanRequire($machine->name === 'NE-E2E-' . $run && $machine->token === $credentials['machine']['token']);
        scanRequire(($auth['run'] ?? null) === $run && ($auth['app_path'] ?? null) === $root && ($auth['status'] ?? null) === 'applied' &&
            ($auth['applied']['value'] ?? null) === ($credentials['node_token'] ?? null) && admin_setting('server_token') === $credentials['node_token']);
        $values = [$credentials['user']['uuid'], $credentials['machine']['token'], $credentials['node_token']];
        $digests = []; $prefixes = []; scanRequire(isset($tokens['tokens']['admin'], $tokens['tokens']['ordinary']) && count($tokens['tokens']) === 2);
        foreach ($tokens['tokens'] as $role => $token) {
            scanRequire(in_array($role, ['admin', 'ordinary'], true) && $token['name'] === 'NE-E2E-' . $run . '-plugin-' . $role && $token['morph'] === $user->getMorphClass());
            $owner = \App\Models\User::findOrFail((int) $token['user_id']);
            scanRequire((bool) $owner->is_admin === ($role === 'admin') && ($role !== 'ordinary' || (int) $owner->id === (int) $user->id));
            $parts = explode('|', $token['plain'], 2); scanRequire(count($parts) === 2 && (int) $parts[0] === $token['id'] && strlen($parts[1]) >= 48);
            $record = \Illuminate\Support\Facades\DB::table('personal_access_tokens')->where('id', $token['id'])->first();
            scanRequire($record && $record->name === $token['name'] && (int) $record->tokenable_id === (int) $token['user_id'] && $record->tokenable_type === $token['morph'] && hash_equals($record->token, hash('sha256', $parts[1])));
            $prefix = substr($parts[1], 0, -48); $entropy = substr($parts[1], -48, 40); $crc = substr($parts[1], -8);
            scanRequire((bool) preg_match('/^[A-Za-z0-9_-]{0,32}$/D', $prefix) && (bool) preg_match('/^[A-Za-z0-9]{40}$/D', $entropy) && hash_equals(hash('crc32b', $entropy), $crc) && $prefix === config('sanctum.token_prefix', ''));
            $values[] = $token['plain']; $values[] = $parts[1]; $digests[] = $record->token; $prefixes[] = $prefix;
        }
        foreach ($tokens['retired'] ?? [] as $token) {
            scanRequire($token['morph'] === $user->getMorphClass() && (bool) preg_match('/^[a-f0-9]{64}$/D', $token['token_sha256']) &&
                in_array($token['name'], ['NE-E2E-' . $run . '-plugin-admin', 'NE-E2E-' . $run . '-plugin-ordinary'], true));
            $role = str_ends_with($token['name'], '-admin') ? 'admin' : 'ordinary';
            scanRequire((int) $token['user_id'] === (int) $tokens['tokens'][$role]['user_id']);
            $record = \Illuminate\Support\Facades\DB::table('personal_access_tokens')->where('id', $token['id'])->first();
            if ($record) scanRequire($record->name === $token['name'] && (int) $record->tokenable_id === (int) $token['user_id'] && $record->tokenable_type === $token['morph'] && hash_equals($record->token, $token['token_sha256']));
            $digests[] = $token['token_sha256'];
        }
        if (file_exists($archive)) {
            $previous = scanRead($archive); scanRequire(($previous['schema'] ?? null) === 1 && ($previous['run'] ?? null) === $run && ($previous['app_path'] ?? null) === $root);
            $values = array_merge($previous['exact_values'], $values); $digests = array_merge($previous['sanctum_secret_digests'], $digests); $prefixes = array_merge($previous['sanctum_prefixes'], $prefixes);
        }
        scanRequire(!in_array('', $values, true));
        $saved = ['schema' => 1, 'run' => $run, 'app_path' => $root, 'exact_values' => array_values(array_unique($values)),
            'sanctum_secret_digests' => array_values(array_unique($digests)), 'sanctum_prefixes' => array_values(array_unique($prefixes))];
        scanWrite($archive, $saved);
        $result = ['status' => 'CAPTURED', 'owned_exact_value_count' => count($saved['exact_values']), 'owned_sanctum_secret_digest_count' => count($saved['sanctum_secret_digests']), 'database_mutation' => false, 'private_values_retained_for_post_cleanup_scan' => true];
    } else {
        $saved = scanRead($archive); scanRequire(($saved['schema'] ?? null) === 1 && ($saved['run'] ?? null) === $run && count($saved['exact_values']) >= 3 && count($saved['sanctum_secret_digests']) >= 2);
        $name = $options['manifest-file'] ?? ''; scanRequire(basename($name) === $name && (bool) preg_match('/^[A-Za-z0-9._-]+\.json$/D', $name));
        $manifest = scanRead($stage . '/' . $name); scanRequire(($manifest['schema'] ?? null) === 1 && ($manifest['run'] ?? null) === $run && count($manifest['packages'] ?? []) === 3);
        $digests = array_fill_keys($saved['sanctum_secret_digests'], true); $packages = []; $names = [];
        foreach ($manifest['packages'] as $package) {
            $name = $package['file']; scanRequire(basename($name) === $name && (bool) preg_match('/^[A-Za-z0-9._-]+\.zip$/D', $name) && !isset($names[$name])); $names[$name] = true;
            $path = $stage . '/' . $name; scanPrivate($path);
            scanRequire((bool) preg_match('/^[a-f0-9]{64}$/D', $package['sha256']) && hash_equals($package['sha256'], hash_file('sha256', $path)));
            $zip = new ZipArchive(); scanRequire($zip->open($path) === true && $zip->numFiles > 0 && $zip->numFiles < 20000); $total = 0; $members = 0; $leak = false;
            try {
                for ($index = 0; $index < $zip->numFiles; $index++) {
                    $info = $zip->statIndex($index); scanRequire(is_array($info) && $info['size'] <= 16000000); $total += $info['size']; scanRequire($total <= 128000000);
                    if (str_ends_with($info['name'], '/')) continue;
                    $bytes = $zip->getFromIndex($index); scanRequire(is_string($bytes)); $members++;
                    foreach ($saved['exact_values'] as $value) if (str_contains($bytes, $value)) $leak = true;
                    foreach ($saved['sanctum_prefixes'] as $prefix) {
                        preg_match_all('~' . preg_quote($prefix, '~') . '(?=([A-Za-z0-9]{40}[a-f0-9]{8}))~', $bytes, $found);
                        foreach ($found[1] as $candidate) if (hash_equals(hash('crc32b', substr($candidate, 0, 40)), substr($candidate, 40)) && isset($digests[hash('sha256', $prefix . $candidate)])) $leak = true;
                    }
                }
            } finally { $zip->close(); }
            $packages[] = ['file' => $name, 'members_scanned' => $members, 'expected_sha256_matches' => true, 'owned_test_secret_values_absent' => !$leak];
        }
        $passed = !in_array(false, array_column($packages, 'owned_test_secret_values_absent'), true);
        $result = ['status' => $passed ? 'PASS' : 'FAIL', 'package_count' => 3, 'owned_exact_value_count' => count($saved['exact_values']),
            'owned_sanctum_secret_digest_count' => count($digests), 'values_absent' => $passed, 'packages' => $packages,
            'scope' => 'Exact captured test credentials plus native Sanctum secret candidates matched against current/retired private digests; this is not a generic secret-detection claim'];
    }
    while (ob_get_level() > $level) ob_end_clean(); echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
    if (($result['status'] ?? '') === 'FAIL') exit(1);
} catch (Throwable $error) {
    while (ob_get_level() > $level) ob_end_clean();
    echo json_encode(['status' => 'ERROR', 'reason' => 'PRIVATE_PACKAGE_SECRET_SCAN_FAILED']) . "\n"; exit(1);
}
