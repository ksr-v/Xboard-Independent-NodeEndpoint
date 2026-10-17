<?php
declare(strict_types=1);

/* Test-only real-application controller. State and newly minted Sanctum tokens
 * stay in the root-owned stage; stdout contains only explicit checks.
 * Actions: install, renew-tokens, configure, import-ca, lifecycle, inspect, cleanup,
 *          admin-user-limit.
 * Common: --app=/path/xboard --stage=/private/lab --run=run-id
 *         --base-url=http://authorized-panel-ip
 * install: --zip-file=NodeEndpoint-1.0.0.zip --zip-sha256=HEX
 *          --confirm=NATIVE_PLUGIN_INSTALL [--app-uid=1001 --app-gid=1001]
 * configure: --endpoint-host=IP --endpoint-port=8443
 * renew-tokens: --confirm=RENEW_OWN_EPHEMERAL_TOKENS
 * import-ca: --ca-file=stage-basename.pem --fingerprints=HEX[,HEX]
 * lifecycle: --confirm=NATIVE_PLUGIN_LIFECYCLE_STOPPED
 * cleanup: --confirm=NATIVE_PLUGIN_CLEANUP
 * admin-user-limit: --phase=apply|restore --confirm=SYNTHETIC_ADMIN_USER_LIMIT_TEST
 * It refuses any pre-existing NodeEndpoint installation, recovery table,
 * migration marker, public assets or public CA. Root entry/PKI are never deleted.
 */

use App\Models\Plugin;
use App\Models\User;
use App\Services\Plugin\PluginManager;
use Illuminate\Contracts\Console\Kernel;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

final class E2EPluginException extends RuntimeException {}
function failControl(string $code): never { throw new E2EPluginException($code); }
function requireControl(bool $condition, string $code): void { if (!$condition) failControl($code); }
function privateControl(string $path): void {
    requireControl(is_file($path) && !is_link($path), 'PRIVATE_FILE_REQUIRED');
    if (PHP_OS_FAMILY !== 'Windows') requireControl((fileperms($path) & 0077) === 0 && fileowner($path) === posix_geteuid(), 'PRIVATE_OWNER_OR_MODE');
}
function readControl(string $path): array {
    privateControl($path);
    $data = json_decode((string) file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
    requireControl(is_array($data), 'INVALID_PRIVATE_JSON');
    return $data;
}
function saveControl(string $path, array $data): void {
    requireControl(!is_link($path), 'SYMLINK_REFUSED');
    if (file_exists($path)) privateControl($path);
    $tmp = $path . '.tmp-' . bin2hex(random_bytes(8));
    $handle = fopen($tmp, 'x'); requireControl((bool) $handle, 'PRIVATE_WRITE_FAILED');
    try {
        if (PHP_OS_FAMILY !== 'Windows') requireControl(chmod($tmp, 0600), 'PRIVATE_CHMOD_FAILED');
        $json = json_encode($data, JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n";
        requireControl(fwrite($handle, $json) === strlen($json), 'PRIVATE_WRITE_FAILED'); fflush($handle);
    } finally { fclose($handle); }
    requireControl(rename($tmp, $path), 'PRIVATE_RENAME_FAILED');
}
function treesControl(string $root, array $exclude = [], bool $preserveForeignLinks = false): array {
    if (!is_dir($root)) return [];
    requireControl(!is_link($root), 'TREE_SYMLINK_REFUSED');
    $result = [];
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::SELF_FIRST) as $item) {
        $relative = str_replace('\\', '/', substr($item->getPathname(), strlen($root) + 1));
        $skip = false;
        foreach ($exclude as $part) if ($relative === $part || str_starts_with($relative, $part . '/')) $skip = true;
        if ($skip) continue;
        if ($item->isLink()) {
            requireControl($preserveForeignLinks, 'TREE_SYMLINK_REFUSED');
            $result[$relative] = 'link-sha256:' . hash('sha256', (string) readlink($item->getPathname()));
            continue;
        }
        if ($item->isFile()) $result[$relative] = hash_file('sha256', $item->getPathname());
    }
    ksort($result); return $result;
}
function zipControl(string $path): array {
    $zip = new ZipArchive(); requireControl($zip->open($path) === true, 'ZIP_OPEN_FAILED');
    $files = [];
    try {
        requireControl($zip->numFiles > 0 && $zip->numFiles < 2000, 'ZIP_MEMBER_COUNT');
        for ($index = 0; $index < $zip->numFiles; $index++) {
            $name = $zip->getNameIndex($index);
            requireControl(is_string($name) && str_starts_with($name, 'NodeEndpoint/') && !str_contains($name, '\\') &&
                !str_contains($name, "\0") && !str_contains($name, ':') && !in_array('..', explode('/', $name), true) &&
                !in_array('.', explode('/', $name), true), 'ZIP_PATH_REFUSED');
            $opsys = $attrs = 0;
            if ($zip->getExternalAttributesIndex($index, $opsys, $attrs)) requireControl(($attrs >> 16 & 0170000) !== 0120000, 'ZIP_SYMLINK_REFUSED');
            if (str_ends_with($name, '/')) continue;
            $relative = substr($name, strlen('NodeEndpoint/'));
            requireControl($relative !== '' && !isset($files[$relative]), 'ZIP_DUPLICATE_MEMBER');
            $data = $zip->getFromIndex($index);
            requireControl(is_string($data) && strlen($data) <= 16000000, 'ZIP_MEMBER_TOO_LARGE');
            $files[$relative] = hash('sha256', $data);
        }
        foreach (['Plugin.php', 'config.json', 'routes/api.php', 'routes/web.php', 'resources/assets/app.js', 'resources/assets/style.css',
            'resources/views/index.html', 'tools/plugin/purge.php'] as $part) requireControl(isset($files[$part]), 'ZIP_REQUIRED_MEMBER_MISSING');
        $config = json_decode((string) $zip->getFromName('NodeEndpoint/config.json'), true, 32, JSON_THROW_ON_ERROR);
        requireControl(($config['code'] ?? null) === 'node_endpoint' && ($config['version'] ?? null) === '1.0.0', 'ZIP_CONFIG_OR_VERSION');
    } finally { $zip->close(); }
    ksort($files); return $files;
}
function dbControl(string $guard, array $synthetic): array {
    $result = [];
    $tables = ['v2_user', 'v2_server', 'v2_server_machine', 'v2_server_group', 'v2_plan', 'v2_stat_user', 'v2_stat_server', 'v2_plugins', 'v2_settings', 'migrations'];
    $exclude = [
        'v2_user' => ['id', [(int) ($synthetic['ids']['user'] ?? 0)]],
        'v2_server' => ['id', array_column($synthetic['nodes'] ?? [], 'id')],
        'v2_server_machine' => ['id', [(int) ($synthetic['ids']['machine'] ?? 0)]],
        'v2_server_group' => ['id', [(int) ($synthetic['ids']['group'] ?? 0)]],
        'v2_plan' => ['id', [(int) ($synthetic['ids']['plan'] ?? 0)]],
        'v2_stat_user' => ['user_id', [(int) ($synthetic['ids']['user'] ?? 0)]],
        'v2_stat_server' => ['server_id', array_column($synthetic['nodes'] ?? [], 'id')],
    ];
    foreach ($tables as $table) {
        if (!Schema::hasTable($table)) continue;
        $query = DB::table($table)->orderBy('id');
        if (isset($exclude[$table])) $query->whereNotIn(...$exclude[$table]);
        if ($table === 'v2_plugins') $query->where('code', '<>', 'node_endpoint');
        if ($table === 'migrations') $query->where('migration', '<>', '2026_10_09_000001_create_node_endpoint_settings');
        // Only the run's explicit empty-panel temporary token is excluded.
        if ($table === 'v2_settings' && ($synthetic['temporary_auth'] ?? false)) $query->where('name', '<>', 'server_token');
        $hash = hash_init('sha256', HASH_HMAC, $guard); $count = 0;
        foreach ($query->cursor() as $row) { $values = (array) $row; ksort($values); hash_update($hash, json_encode($values, JSON_THROW_ON_ERROR) . "\n"); $count++; }
        $result[$table] = ['count' => $count, 'hmac' => hash_final($hash)];
    }
    return $result;
}
function compareControl(array $state, string $appPath): array {
    return ['public_admin_unchanged' => treesControl($appPath . '/public', ['plugins/node_endpoint'], true) === $state['public'],
        'foreign_plugin_files_unchanged' => treesControl($appPath . '/plugins', ['NodeEndpoint'], true) === $state['foreign_plugin_files'],
        'foreign_database_rows_unchanged' => dbControl($state['guard'], $state['synthetic']) === $state['foreign_db']];
}
function ownedPermissionsControl(string $root, int $uid, int $gid): void {
    requireControl(is_dir($root) && !is_link($root), 'OWNED_DIRECTORY_REQUIRED');
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::SELF_FIRST) as $item) {
        requireControl(!$item->isLink(), 'OWNED_SYMLINK_REFUSED');
        if (PHP_OS_FAMILY !== 'Windows') {
            requireControl(chown($item->getPathname(), $uid) && chgrp($item->getPathname(), $gid) && chmod($item->getPathname(), $item->isDir() ? 0755 : 0644), 'OWNED_PERMISSION_FAILED');
        }
    }
    if (PHP_OS_FAMILY !== 'Windows') requireControl(chown($root, $uid) && chgrp($root, $gid) && chmod($root, 0755), 'OWNED_PERMISSION_FAILED');
}
function httpControl(string $base, string $path, string $method = 'GET', ?array $data = null, ?string $bearer = null): array {
    $curl = curl_init($base . $path); requireControl((bool) $curl, 'CURL_INIT_FAILED');
    $body = '';
    $headers = ['Accept: application/json'];
    if ($bearer !== null) $headers[] = 'Authorization: Bearer ' . $bearer;
    if ($data !== null) $headers[] = 'Content-Type: application/json';
    curl_setopt_array($curl, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_HTTPHEADER => $headers,
        CURLOPT_FOLLOWLOCATION => false, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => 15,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS, CURLOPT_PROXY => '',
        CURLOPT_WRITEFUNCTION => function ($curl, $chunk) use (&$body) { if (strlen($body) + strlen($chunk) > 2000000) return 0; $body .= $chunk; return strlen($chunk); }]);
    if ($data !== null) curl_setopt($curl, CURLOPT_POSTFIELDS, json_encode($data, JSON_THROW_ON_ERROR));
    try { $ok = curl_exec($curl); $status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE); }
    finally { curl_close($curl); }
    requireControl($ok !== false, 'HTTP_TRANSPORT_FAILED');
    return ['status' => (int) $status, 'body' => $body];
}
function removeTreeControl(string $root): void {
    if (!is_dir($root)) return;
    requireControl(!is_link($root), 'REMOVE_SYMLINK_REFUSED');
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST) as $item) {
        requireControl(!$item->isLink(), 'REMOVE_SYMLINK_REFUSED');
        requireControl($item->isDir() ? rmdir($item->getPathname()) : unlink($item->getPathname()), 'OWNED_REMOVE_FAILED');
    }
    requireControl(rmdir($root), 'OWNED_REMOVE_FAILED');
}
function recoveryControl(string $caPath): array {
    requireControl(Schema::hasTable('node_endpoint_settings') && !is_link($caPath), 'RECOVERY_STATE_REQUIRED');
    $rows = [];
    foreach (DB::table('node_endpoint_settings')->orderBy('id')->get() as $row) {
        $values = (array) $row; ksort($values); $rows[] = $values;
    }
    return ['settings_sha256' => hash('sha256', json_encode($rows, JSON_THROW_ON_ERROR)),
        'public_ca_sha256' => is_file($caPath) ? hash_file('sha256', $caPath) : null];
}
function tokenRecordControl(array $token, bool $required = true): ?object {
    $record = DB::table('personal_access_tokens')->where('id', $token['id'])->first();
    if (!$record) { requireControl(!$required, 'EPHEMERAL_TOKEN_RECORD_MISSING'); return null; }
    $wanted = isset($token['plain']) ? hash('sha256', explode('|', $token['plain'], 2)[1] ?? '') : $token['token_sha256'];
    requireControl($record->name === $token['name'] && (int) $record->tokenable_id === $token['user_id'] &&
        $record->tokenable_type === $token['morph'] && hash_equals($record->token, $wanted), 'EPHEMERAL_TOKEN_RECORD_CHANGED');
    return $record;
}
function userLimitRowControl(int $id): array {
    $row = DB::table('v2_user')->where('id', $id)->first();
    requireControl($row !== null, 'SYNTHETIC_USER_MISSING');
    $values = (array) $row; ksort($values); return $values;
}
function userLimitOtherFieldsControl(array $before, array $after): bool {
    // Native Eloquent updates updated_at; every other field must remain exact.
    foreach (['device_limit', 'updated_at'] as $field) { unset($before[$field], $after[$field]); }
    return $before === $after;
}
function nativeUserLimitPostControl(string $base, string $path, string $bearer, int $id, mixed $limit): void {
    $response = httpControl($base, $path, 'POST', ['id' => $id, 'device_limit' => $limit], $bearer);
    $parsed = json_decode($response['body'], true);
    requireControl($response['status'] === 200 && is_array($parsed) && ($parsed['status'] ?? null) === 'success' &&
        ($parsed['data'] ?? null) === true, 'NATIVE_ADMIN_USER_LIMIT_HTTP_FAILED');
}

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
ini_set('display_errors', '0'); umask(0077); $outputLevel = ob_get_level(); ob_start();
try {
    $action = $argv[1] ?? '';
    requireControl(in_array($action, ['install', 'renew-tokens', 'configure', 'import-ca', 'lifecycle', 'inspect', 'cleanup', 'admin-user-limit'], true), 'INVALID_ACTION');
    $options = [];
    foreach (array_slice($argv, 2) as $arg) {
        requireControl((bool) preg_match('/^--([a-z][a-z0-9-]*)=(.*)$/D', $arg, $match) && !isset($options[$match[1]]), 'INVALID_OPTION');
        $options[$match[1]] = $match[2];
    }
    requireControl(!array_diff(array_keys($options), ['app', 'stage', 'run', 'base-url', 'zip-file', 'zip-sha256', 'confirm', 'endpoint-host', 'endpoint-port', 'ca-file', 'fingerprints', 'app-uid', 'app-gid', 'phase']), 'UNKNOWN_OPTION');
    requireControl(!isset($options['phase']) || $action === 'admin-user-limit', 'PHASE_ONLY_FOR_ADMIN_USER_LIMIT');
    $run = $options['run'] ?? ''; requireControl((bool) preg_match('/^[a-z0-9][a-z0-9-]{5,27}$/D', $run), 'INVALID_RUN_ID');
    $appPath = realpath($options['app'] ?? ''); $stage = realpath($options['stage'] ?? '');
    requireControl($appPath && $stage && !is_link($options['stage']) && is_file($appPath . '/vendor/autoload.php') && is_file($appPath . '/bootstrap/app.php'), 'INVALID_APP_OR_STAGE');
    if (PHP_OS_FAMILY !== 'Windows') requireControl(fileowner($stage) === posix_geteuid() && (fileperms($stage) & 0077) === 0, 'STAGE_OWNER_OR_MODE');
    privateControl($stage . '/.node-endpoint-e2e-owner');
    $base = rtrim($options['base-url'] ?? '', '/'); $parts = parse_url($base);
    requireControl(is_array($parts) && in_array($parts['scheme'] ?? '', ['http', 'https'], true) &&
        filter_var($parts['host'] ?? '', FILTER_VALIDATE_IP) && !isset($parts['user']) && !isset($parts['pass']) && !isset($parts['query']) && !isset($parts['fragment']) &&
        !isset($parts['path']), 'BASE_MUST_BE_AUTHORIZED_IP_ORIGIN');
    $statePath = $stage . '/' . $run . '-plugin-state.json'; $tokensPath = $stage . '/' . $run . '-plugin-tokens.json';
    $lockPath = $stage . '/' . $run . '-plugin.lock'; requireControl(!is_link($lockPath), 'LOCK_SYMLINK_REFUSED');
    if (file_exists($lockPath)) privateControl($lockPath);
    $lock = fopen($lockPath, 'c'); requireControl($lock && flock($lock, LOCK_EX | LOCK_NB), 'PLUGIN_RUN_LOCK_BUSY');
    require $appPath . '/vendor/autoload.php'; $app = require $appPath . '/bootstrap/app.php'; $app->make(Kernel::class)->bootstrap();
    $manager = app(PluginManager::class); $pluginDir = base_path('plugins/NodeEndpoint'); $publicDir = public_path('plugins/node_endpoint');
    $caPath = storage_path('app/node_endpoint/trust-bundle.pem'); $migration = '2026_10_09_000001_create_node_endpoint_settings';

    if ($action === 'install') {
        requireControl(($options['confirm'] ?? '') === 'NATIVE_PLUGIN_INSTALL', 'INSTALL_CONFIRMATION_REQUIRED');
        requireControl(!file_exists($statePath) && !file_exists($tokensPath) && !Plugin::where('code', 'node_endpoint')->exists() &&
            !file_exists($pluginDir) && !file_exists(base_path('plugins-core/NodeEndpoint')) && !file_exists($publicDir) && !file_exists($caPath) &&
            !Schema::hasTable('node_endpoint_settings') && !DB::table('migrations')->where('migration', $migration)->exists(), 'PREEXISTING_NODE_ENDPOINT_REFUSED');
        $zipName = $options['zip-file'] ?? ''; requireControl(basename($zipName) === $zipName && preg_match('/^[A-Za-z0-9._-]+\.zip$/D', $zipName), 'ZIP_STAGE_BASENAME_REQUIRED');
        $zipPath = $stage . '/' . $zipName; privateControl($zipPath);
        $wanted = strtolower($options['zip-sha256'] ?? ''); requireControl(preg_match('/^[a-f0-9]{64}$/D', $wanted) && hash_equals($wanted, hash_file('sha256', $zipPath)), 'ZIP_SHA256_MISMATCH');
        $files = zipControl($zipPath);
        $synthetic = readControl($stage . '/' . $run . '-state.json');
        requireControl(($synthetic['run'] ?? null) === $run && ($synthetic['app_path'] ?? null) === $appPath && ($synthetic['status'] ?? null) === 'created', 'SYNTHETIC_STATE_REQUIRED');
        $synthetic['temporary_auth'] = is_file($stage . '/' . $run . '-auth.json');
        $uid = isset($options['app-uid']) ? (int) $options['app-uid'] : (int) fileowner($appPath . '/storage');
        $gid = isset($options['app-gid']) ? (int) $options['app-gid'] : (int) filegroup($appPath . '/storage');
        requireControl($uid > 0 && $gid > 0 && $uid === (int) fileowner($appPath . '/storage') && $gid === (int) filegroup($appPath . '/storage'), 'NONROOT_APP_OWNER_REQUIRED');
        $state = ['schema' => 1, 'run' => $run, 'app_path' => $appPath, 'base' => $base, 'status' => 'prepared', 'guard' => bin2hex(random_bytes(32)),
            'synthetic' => $synthetic, 'files' => $files, 'zip_sha256' => $wanted, 'app_uid' => $uid, 'app_gid' => $gid,
            'public' => treesControl(public_path(), ['plugins/node_endpoint'], true), 'foreign_plugin_files' => treesControl(base_path('plugins'), ['NodeEndpoint'], true),
            'ca_directory_existed' => is_dir(dirname($caPath)), 'created_dirs' => []];
        foreach ([storage_path('tmp'), storage_path('tmp/plugins'), public_path('plugins')] as $directory) if (!is_dir($directory)) $state['created_dirs'][] = $directory;
        $state['foreign_db'] = dbControl($state['guard'], $synthetic); saveControl($statePath, $state);
        $uploadCopy = $stage . '/' . $run . '-native-upload.zip'; requireControl(!file_exists($uploadCopy) && copy($zipPath, $uploadCopy), 'ZIP_COPY_FAILED');
        requireControl($manager->upload(new UploadedFile($uploadCopy, basename($uploadCopy), 'application/zip', null, true)), 'NATIVE_UPLOAD_FAILED');
        requireControl(treesControl($pluginDir) === $files, 'NATIVE_IMPORT_BYTES_DIFFER');
        ownedPermissionsControl($pluginDir, $uid, $gid);
        requireControl($manager->install('node_endpoint') && $manager->enable('node_endpoint'), 'NATIVE_INSTALL_ENABLE_FAILED');
        ownedPermissionsControl($publicDir, $uid, $gid);
        $row = Plugin::where('code', 'node_endpoint')->firstOrFail(); requireControl($row->version === '1.0.0' && $row->is_enabled, 'NATIVE_PLUGIN_VERSION_STATE');
        $state['plugin_id'] = (int) $row->id; $state['public_assets'] = treesControl($publicDir); $state['status'] = 'installed';
        $admin = User::where('is_admin', true)->orderBy('id')->firstOrFail(); $ordinary = User::findOrFail((int) $synthetic['ids']['user']);
        requireControl(!$ordinary->is_admin && $ordinary->email === $run . '@node-endpoint-e2e.invalid', 'ORDINARY_USER_OWNER_MISMATCH');
        $tokens = ['schema' => 1, 'run' => $run, 'tokens' => []];
        foreach (['admin' => $admin, 'ordinary' => $ordinary] as $role => $user) {
            $name = 'NE-E2E-' . $run . '-plugin-' . $role; requireControl(!$user->tokens()->where('name', $name)->exists(), 'EPHEMERAL_TOKEN_NAME_COLLISION');
            $minted = $user->createToken($name, ['*'], now()->addHours(4));
            $tokens['tokens'][$role] = ['id' => (int) $minted->accessToken->id, 'user_id' => (int) $user->id,
                'name' => $name, 'plain' => $minted->plainTextToken, 'morph' => $user->getMorphClass()];
            saveControl($tokensPath, $tokens);
        }
        saveControl($statePath, $state);
        $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_ON_INSTALL');
        $result = ['status' => 'INSTALLED', 'run' => $run, 'version' => '1.0.0', 'zip_members_verified' => count($files), 'checks' => $checks];
    } else {
        $state = readControl($statePath); requireControl(($state['schema'] ?? null) === 1 && ($state['run'] ?? null) === $run &&
            ($state['app_path'] ?? null) === $appPath && ($state['base'] ?? null) === $base && ($state['status'] ?? null) === 'installed', 'PLUGIN_STATE_OWNER_MISMATCH');
        requireControl(treesControl($pluginDir) === $state['files'] && treesControl($publicDir) === $state['public_assets'], 'OWNED_PLUGIN_BYTES_CHANGED');
        $tokens = readControl($tokensPath); requireControl(($tokens['run'] ?? null) === $run, 'TOKEN_STATE_OWNER_MISMATCH');
        $adminBearer = $tokens['tokens']['admin']['plain'];
        if ($action === 'admin-user-limit') {
            requireControl(($options['confirm'] ?? '') === 'SYNTHETIC_ADMIN_USER_LIMIT_TEST', 'ADMIN_USER_LIMIT_CONFIRMATION_REQUIRED');
            $phase = $options['phase'] ?? ''; requireControl(in_array($phase, ['apply', 'restore'], true), 'ADMIN_USER_LIMIT_PHASE_REQUIRED');
            $id = (int) $state['synthetic']['ids']['user'];
            $synthetic = readControl($stage . '/' . $run . '-state.json');
            requireControl(($synthetic['schema'] ?? null) === 1 && ($synthetic['run'] ?? null) === $run &&
                ($synthetic['app_path'] ?? null) === $appPath && ($synthetic['status'] ?? null) === 'created' &&
                (int) $synthetic['ids']['user'] === $id, 'SYNTHETIC_USER_STATE_MISMATCH');
            $user = User::findOrFail($id);
            requireControl(!$user->is_admin && !$user->is_staff && $user->email === $run . '@node-endpoint-e2e.invalid' &&
                $user->remarks === 'NE-E2E-' . $run && (int) $user->group_id === (int) $synthetic['ids']['group'] &&
                (int) $user->plan_id === (int) $synthetic['ids']['plan'] && $user->invite_user_id === null,
                'NATIVE_ADMIN_UPDATE_UNSAFE_SYNTHETIC_USER');
            $admin = $tokens['tokens']['admin']; $record = tokenRecordControl($admin);
            requireControl($record && $record->expires_at !== null && \Illuminate\Support\Carbon::parse($record->expires_at)->isFuture() &&
                $admin['name'] === 'NE-E2E-' . $run . '-plugin-admin' && (bool) User::whereKey($admin['user_id'])->value('is_admin'), 'OWNED_ADMIN_TOKEN_NOT_CURRENT');
            $routes = array_values(array_filter(iterator_to_array(app('router')->getRoutes()), fn ($route) =>
                $route->getActionName() === 'App\\Http\\Controllers\\V2\\Admin\\UserController@update' && in_array('POST', $route->methods(), true)));
            requireControl(count($routes) === 1 && in_array('admin', $routes[0]->gatherMiddleware(), true) &&
                preg_match('~^api/v2/[A-Za-z0-9/_-]+/user/update$~D', $routes[0]->uri()), 'EXACT_NATIVE_ADMIN_USER_ROUTE_REQUIRED');
            $path = '/' . $routes[0]->uri(); // Administrative path and bearer never enter public output.
            $journalPath = $stage . '/' . $run . '-admin-user-limit-state.json';
            $before = userLimitRowControl($id);
            if ($phase === 'apply') {
                requireControl(!file_exists($journalPath), 'ADMIN_USER_LIMIT_RUN_ALREADY_EXISTS');
                $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_BEFORE_ADMIN_USER_LIMIT');
                $limit = (int) ($before['device_limit'] ?? 0) === 8 ? 16 : 8;
                $journal = ['schema' => 1, 'run' => $run, 'app_path' => $appPath, 'base' => $base, 'user_id' => $id,
                    'status' => 'prepared', 'original' => $before, 'applied_limit' => $limit, 'route' => $path];
                saveControl($journalPath, $journal);
            } else {
                $journal = readControl($journalPath);
                requireControl(($journal['schema'] ?? null) === 1 && ($journal['run'] ?? null) === $run &&
                    ($journal['app_path'] ?? null) === $appPath && ($journal['base'] ?? null) === $base &&
                    ($journal['user_id'] ?? null) === $id && ($journal['route'] ?? null) === $path &&
                    in_array($journal['status'] ?? null, ['prepared', 'applied', 'restored_after_failure'], true), 'ADMIN_USER_LIMIT_JOURNAL_MISMATCH');
                requireControl($before['device_limit'] === $journal['applied_limit'] || $before['device_limit'] === $journal['original']['device_limit'], 'ADMIN_USER_LIMIT_CAS_MISMATCH');
                $limit = $journal['original']['device_limit'];
            }
            try {
                nativeUserLimitPostControl($base, $path, $adminBearer, $id, $limit);
                $after = userLimitRowControl($id);
                requireControl($after['device_limit'] === $limit && userLimitOtherFieldsControl($before, $after), 'ADMIN_USER_LIMIT_OTHER_FIELDS_CHANGED');
                requireControl($after['uuid'] === $before['uuid'], 'ADMIN_USER_LIMIT_UUID_CHANGED');
                $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_ON_ADMIN_USER_LIMIT');
                $journal['status'] = $phase === 'apply' ? 'applied' : 'restored';
                $journal['last_post_at'] = time(); saveControl($journalPath, $journal);
                $result = ['status' => $phase === 'apply' ? 'ADMIN_USER_LIMIT_APPLIED' : 'ADMIN_USER_LIMIT_RESTORED',
                    'run' => $run, 'http_status' => 200, 'native_controller_and_admin_middleware' => true,
                    'only_owned_synthetic_user_updated' => true, 'uuid_unchanged' => true,
                    'all_other_user_fields_unchanged_except_native_updated_at' => true,
                    'original_device_limit_restored' => $phase === 'restore', 'checks' => $checks];
            } catch (Throwable $error) {
                // Prioritize restoring only our owned field; never revert traffic or
                // any other columns which may have changed concurrently.
                $current = userLimitRowControl($id);
                if ($current['device_limit'] === $journal['applied_limit']) {
                    nativeUserLimitPostControl($base, $path, $adminBearer, $id, $journal['original']['device_limit']);
                }
                $adminUserLimitRestored = userLimitRowControl($id)['device_limit'] === $journal['original']['device_limit'];
                $journal['status'] = $adminUserLimitRestored ? 'restored_after_failure' : 'failed_restore';
                saveControl($journalPath, $journal); throw $error;
            }
        } elseif ($action === 'renew-tokens') {
            requireControl(($options['confirm'] ?? '') === 'RENEW_OWN_EPHEMERAL_TOKENS', 'TOKEN_RENEWAL_CONFIRMATION_REQUIRED');
            requireControl(count($tokens['tokens']) === 2 && isset($tokens['tokens']['admin'], $tokens['tokens']['ordinary']), 'EPHEMERAL_TOKEN_PAIR_REQUIRED');
            foreach ($tokens['tokens'] as $role => $token) {
                requireControl(in_array($role, ['admin', 'ordinary'], true) && $token['name'] === 'NE-E2E-' . $run . '-plugin-' . $role, 'EPHEMERAL_TOKEN_NAME_MISMATCH');
                tokenRecordControl($token);
            }
            foreach ($tokens['tokens'] as $role => $old) {
                $user = User::findOrFail($old['user_id']);
                requireControl($user->getMorphClass() === $old['morph'] && (bool) $user->is_admin === ($role === 'admin'), 'EPHEMERAL_USER_ROLE_CHANGED');
                if ($role === 'ordinary') requireControl($user->email === $run . '@node-endpoint-e2e.invalid' &&
                    (int) $user->id === (int) $state['synthetic']['ids']['user'], 'ORDINARY_USER_OWNER_MISMATCH');
                $priorTokens = $tokens; DB::beginTransaction();
                try {
                    $minted = $user->createToken($old['name'], ['*'], now()->addHours(4));
                    $retired = $old; unset($retired['plain']); $retired['token_sha256'] = hash('sha256', explode('|', $old['plain'], 2)[1] ?? '');
                    $tokens['retired'][] = $retired;
                    $tokens['tokens'][$role] = ['id' => (int) $minted->accessToken->id, 'user_id' => (int) $user->id,
                        'name' => $old['name'], 'plain' => $minted->plainTextToken, 'morph' => $old['morph']];
                    // Journal both exact IDs before commit; a write failure cannot orphan a newly minted token.
                    saveControl($tokensPath, $tokens); tokenRecordControl($old);
                    DB::table('personal_access_tokens')->where('id', $old['id'])->delete(); DB::commit();
                } catch (Throwable $error) {
                    if (DB::transactionLevel() > 0) DB::rollBack(); $tokens = $priorTokens;
                    saveControl($tokensPath, $tokens); throw $error;
                }
            }
            foreach ($tokens['retired'] ?? [] as $retired) if (tokenRecordControl($retired, false)) DB::table('personal_access_tokens')->where('id', $retired['id'])->delete();
            $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_ON_TOKEN_RENEWAL');
            $result = ['status' => 'TOKENS_RENEWED', 'run' => $run, 'owned_current_tokens' => count($tokens['tokens']),
                'previous_owned_tokens_removed' => true, 'checks' => $checks];
        } elseif ($action === 'configure') {
            $anonymous = httpControl($base, '/api/v1/node-endpoint/settings');
            $ordinary = httpControl($base, '/api/v1/node-endpoint/settings', 'GET', null, $tokens['tokens']['ordinary']['plain']);
            $admin = httpControl($base, '/api/v1/node-endpoint/settings', 'GET', null, $adminBearer);
            requireControl($anonymous['status'] === 403 && $ordinary['status'] === 403 && $admin['status'] === 200, 'NATIVE_HTTP_AUTH_STATUS');
            $settings = ['enabled' => true, 'host' => $options['endpoint-host'] ?? '', 'port' => (int) ($options['endpoint-port'] ?? 8443),
                'https' => true, 'http_acknowledged' => false, 'ws_mode' => 'rewrite'];
            requireControl(filter_var($settings['host'], FILTER_VALIDATE_IP) && $settings['port'] > 0 && $settings['port'] <= 65535, 'INVALID_ENDPOINT_SPEC');
            $saved = httpControl($base, '/api/v1/node-endpoint/settings', 'POST', $settings, $adminBearer);
            requireControl($saved['status'] === 200, 'NATIVE_HTTP_SETTINGS_SAVE');
            $data = json_decode($saved['body'], true, 32, JSON_THROW_ON_ERROR);
            $actualSettings = $data['data']['settings'] ?? []; $expectedSettings = $settings;
            ksort($actualSettings); ksort($expectedSettings);
            requireControl($actualSettings === $expectedSettings, 'NATIVE_HTTP_SETTINGS_DIFFER');
            $expected = 'https://' . (str_contains($settings['host'], ':') ? '[' . $settings['host'] . ']' : $settings['host']) . ':' . $settings['port'];
            requireControl(($data['data']['urls']['url'] ?? null) === $expected, 'NATIVE_HTTP_ENDPOINT_DIFFER');
            foreach (['/node-endpoint', '/node-endpoint/assets/app', '/node-endpoint/assets/style'] as $path) requireControl(httpControl($base, $path)['status'] === 200, 'NATIVE_HTTP_PAGE_OR_ASSET');
            $state['endpoint'] = $settings; saveControl($statePath, $state);
            $result = ['status' => 'CONFIGURED', 'run' => $run, 'http_auth' => ['anonymous' => 403, 'ordinary' => 403, 'admin' => 200], 'page_and_assets' => 'PASS'];
        } elseif ($action === 'import-ca') {
            $name = $options['ca-file'] ?? ''; requireControl(basename($name) === $name && preg_match('/^[A-Za-z0-9._-]+\.pem$/D', $name), 'CA_STAGE_BASENAME_REQUIRED');
            privateControl($stage . '/' . $name); $pem = (string) file_get_contents($stage . '/' . $name);
            requireControl(strlen($pem) <= 32768 && !str_contains($pem, 'PRIVATE KEY'), 'PUBLIC_CA_ONLY');
            $wanted = explode(',', strtolower($options['fingerprints'] ?? ''));
            preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', $pem, $matches);
            requireControl(count($wanted) >= 1 && count($wanted) <= 2 && count($matches[0]) === count($wanted) && trim(str_replace($matches[0], '', $pem)) === '', 'CA_PIN_COUNT');
            foreach ($matches[0] as $index => $cert) {
                $parsed = openssl_x509_parse($cert); $key = openssl_pkey_get_public($cert); $fingerprint = openssl_x509_fingerprint($cert, 'sha256');
                requireControl(preg_match('/^[a-f0-9]{64}$/D', $wanted[$index]) && $fingerprint && hash_equals($wanted[$index], $fingerprint) && $parsed && $key &&
                    openssl_x509_verify($cert, $key) === 1 && str_contains($parsed['extensions']['basicConstraints'] ?? '', 'CA:TRUE') &&
                    str_contains($parsed['extensions']['keyUsage'] ?? '', 'Certificate Sign') && $parsed['validFrom_time_t'] <= time() && $parsed['validTo_time_t'] > time(), 'INDEPENDENT_CA_VERIFICATION_FAILED');
            }
            requireControl(count(array_unique($wanted)) === count($wanted), 'CA_DUPLICATE_PIN');
            $import = httpControl($base, '/api/v1/node-endpoint/ca', 'POST', ['pem' => $pem, 'fingerprints' => $wanted], $adminBearer);
            requireControl($import['status'] === 200, 'NATIVE_HTTP_CA_IMPORT');
            $export = httpControl($base, '/api/v1/node-endpoint/ca', 'GET', null, $adminBearer); requireControl($export['status'] === 200, 'NATIVE_HTTP_CA_EXPORT');
            preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', $export['body'], $downloaded);
            requireControl(array_map(fn($cert) => openssl_x509_fingerprint($cert, 'sha256'), $downloaded[0]) === $wanted, 'CA_EXPORT_PINS_DIFFER');
            requireControl(!is_link($caPath) && is_file($caPath), 'IMPORTED_CA_FILE_REQUIRED');
            $state['ca_sha256'] = hash_file('sha256', $caPath); $state['ca_fingerprints'] = $wanted; saveControl($statePath, $state);
            $result = ['status' => 'CA_IMPORTED', 'run' => $run, 'certificates_verified' => count($wanted), 'independent_pins_and_native_export' => 'PASS'];
        } elseif ($action === 'lifecycle') {
            requireControl(($options['confirm'] ?? '') === 'NATIVE_PLUGIN_LIFECYCLE_STOPPED', 'LIFECYCLE_STOPPED_CONFIRMATION_REQUIRED');
            requireControl((int) Plugin::where('code', 'node_endpoint')->value('id') === $state['plugin_id'] &&
                (bool) Plugin::where('code', 'node_endpoint')->value('is_enabled'), 'ENABLED_PLUGIN_ROW_REQUIRED');
            $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_BEFORE_LIFECYCLE');
            $preserved = recoveryControl($caPath); $disabled = false;
            try {
                $disabled = true; requireControl($manager->disable('node_endpoint'), 'NATIVE_DISABLE_FAILED');
                $disabledPage = httpControl($base, '/node-endpoint');
                $disabledApi = httpControl($base, '/api/v1/node-endpoint/settings', 'GET', null, $adminBearer);
                requireControl($disabledPage['status'] === 404 && $disabledApi['status'] === 404, 'DISABLED_HTTP_NOT_404');
                requireControl(recoveryControl($caPath) === $preserved, 'DISABLE_RECOVERY_STATE_CHANGED');
                requireControl($manager->enable('node_endpoint'), 'NATIVE_REENABLE_FAILED'); $disabled = false;
                requireControl(httpControl($base, '/node-endpoint')['status'] === 200 &&
                    httpControl($base, '/api/v1/node-endpoint/settings', 'GET', null, $adminBearer)['status'] === 200, 'REENABLED_HTTP_NOT_200');
                requireControl(recoveryControl($caPath) === $preserved, 'REENABLE_RECOVERY_STATE_CHANGED');
                requireControl($manager->enable('node_endpoint') && $manager->enable('node_endpoint'), 'NATIVE_REPEAT_ENABLE_FAILED');
                $scoped = 'Plugin\\NodeEndpoint\\Middleware\\ScopedHandshake';
                $scopeCount = count(array_filter(app('router')->getMiddlewareGroups()['api'] ?? [], fn($item) => $item === $scoped));
                requireControl($scopeCount === 1 && recoveryControl($caPath) === $preserved, 'REPEAT_ENABLE_SCOPE_OR_STATE_CHANGED');
            } finally {
                // A failed HTTP assertion must not leave the installed test plugin disabled.
                if ($disabled) requireControl($manager->enable('node_endpoint'), 'NATIVE_FAILURE_REENABLE_FAILED');
            }
            $zipName = $options['zip-file'] ?? 'NodeEndpoint-1.0.0.zip';
            requireControl(basename($zipName) === $zipName && preg_match('/^[A-Za-z0-9._-]+\.zip$/D', $zipName), 'ZIP_STAGE_BASENAME_REQUIRED');
            $zipPath = $stage . '/' . $zipName; privateControl($zipPath);
            requireControl(hash_equals($state['zip_sha256'], hash_file('sha256', $zipPath)) && zipControl($zipPath) === $state['files'], 'LIFECYCLE_ZIP_PIN_MISMATCH');
            $tmpRoot = storage_path('tmp/plugins'); requireControl(is_dir($tmpRoot) && !is_link($tmpRoot), 'NATIVE_TMP_DIRECTORY_REQUIRED');
            $beforeTmp = scandir($tmpRoot); requireControl(is_array($beforeTmp), 'NATIVE_TMP_LIST_FAILED');
            $uploadCopy = $stage . '/' . $run . '-reject-' . bin2hex(random_bytes(8)) . '.zip';
            requireControl(copy($zipPath, $uploadCopy), 'ZIP_COPY_FAILED'); $rejected = false;
            try {
                try { $manager->upload(new UploadedFile($uploadCopy, basename($uploadCopy), 'application/zip', null, true)); }
                catch (Exception $error) { $rejected = $error->getMessage() === '上传插件版本不高于已安装版本，无法升级'; }
                requireControl($rejected, 'NATIVE_SAME_VERSION_NOT_REJECTED');
                requireControl(treesControl($pluginDir) === $state['files'] && treesControl($publicDir) === $state['public_assets'] &&
                    recoveryControl($caPath) === $preserved && (int) Plugin::where('code', 'node_endpoint')->value('id') === $state['plugin_id'] &&
                    Plugin::where('code', 'node_endpoint')->value('version') === '1.0.0' &&
                    (bool) Plugin::where('code', 'node_endpoint')->value('is_enabled'), 'REJECTED_UPLOAD_CHANGED_INSTALLED_STATE');
                $newTmp = array_values(array_diff(scandir($tmpRoot), $beforeTmp));
                requireControl(count($newTmp) === 1 && preg_match('/^[a-f0-9]{13}$/D', $newTmp[0]), 'NATIVE_REJECT_TMP_NOT_UNIQUE');
                $extract = $tmpRoot . '/' . $newTmp[0];
                requireControl(!is_link($extract) && scandir($extract) === ['.', '..', 'NodeEndpoint'] &&
                    treesControl($extract . '/NodeEndpoint') === $state['files'], 'NATIVE_REJECT_TMP_BYTES_DIFFER');
                removeTreeControl($extract);
            } finally {
                requireControl(!is_link($uploadCopy) && is_file($uploadCopy) && hash_equals($state['zip_sha256'], hash_file('sha256', $uploadCopy)), 'PRIVATE_UPLOAD_COPY_CHANGED');
                requireControl(unlink($uploadCopy), 'PRIVATE_UPLOAD_COPY_REMOVE_FAILED');
            }
            $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_ON_LIFECYCLE');
            $state['lifecycle_passed'] = true; saveControl($statePath, $state);
            $result = ['status' => 'LIFECYCLE_PASS', 'run' => $run, 'disabled_http' => ['page' => 404, 'admin_api' => 404],
                'reenabled_http' => ['page' => 200, 'admin_api' => 200], 'settings_and_public_ca_preserved' => true,
                'repeated_enable_cli_api_middleware_count' => $scopeCount, 'same_version_native_upload_rejected' => true,
                'rejected_upload_owned_temp_removed' => true, 'checks' => $checks];
        } elseif ($action === 'cleanup') {
            requireControl(($options['confirm'] ?? '') === 'NATIVE_PLUGIN_CLEANUP', 'CLEANUP_CONFIRMATION_REQUIRED');
            $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_BEFORE_CLEANUP');
            $lastBatch = DB::table('migrations')->max('batch'); $batch = DB::table('migrations')->where('batch', $lastBatch)->pluck('migration')->all();
            requireControl($batch === [$migration], 'NATIVE_LAST_BATCH_NOT_ONLY_OWN_MIGRATION');
            requireControl((int) Plugin::where('code', 'node_endpoint')->value('id') === $state['plugin_id'], 'PLUGIN_ROW_OWNER_MISMATCH');
            requireControl(!is_link(dirname($caPath)) && !is_link($caPath), 'CA_SYMLINK_REFUSED');
            if (is_file($caPath)) requireControl(isset($state['ca_sha256']) && hash_equals($state['ca_sha256'], hash_file('sha256', $caPath)), 'OWNED_CA_CHANGED');
            $ownedTokens = array_merge(array_values($tokens['tokens']), $tokens['retired'] ?? []);
            // Validate every remaining exact owned record before the first destructive step.
            foreach ($ownedTokens as $token) tokenRecordControl($token, false);
            $limitJournalPath = $stage . '/' . $run . '-admin-user-limit-state.json';
            if (file_exists($limitJournalPath)) {
                $limitJournal = readControl($limitJournalPath);
                requireControl(($limitJournal['run'] ?? null) === $run && ($limitJournal['app_path'] ?? null) === $appPath &&
                    in_array($limitJournal['status'] ?? null, ['restored', 'restored_after_failure'], true) &&
                    userLimitRowControl((int) $limitJournal['user_id'])['device_limit'] === $limitJournal['original']['device_limit'],
                    'ADMIN_USER_LIMIT_MUST_BE_RESTORED_BEFORE_CLEANUP');
            }
            $recoveryBefore = recoveryControl($caPath);
            requireControl($manager->disable('node_endpoint') && $manager->uninstall('node_endpoint'), 'NATIVE_UNINSTALL_FAILED');
            requireControl(!Plugin::where('code', 'node_endpoint')->exists() && recoveryControl($caPath) === $recoveryBefore, 'NATIVE_UNINSTALL_DID_NOT_PRESERVE_RECOVERY_STATE');
            $purge = $pluginDir . '/tools/plugin/purge.php'; requireControl(hash_file('sha256', $purge) === $state['files']['tools/plugin/purge.php'], 'PURGE_SCRIPT_CHANGED');
            $process = proc_open([PHP_BINARY, $purge, '--app=' . $appPath, '--confirm=PURGE_NODE_ENDPOINT'], [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes, $appPath);
            requireControl(is_resource($process), 'PURGE_START_FAILED'); fclose($pipes[0]); stream_get_contents($pipes[1]); fclose($pipes[1]); stream_get_contents($pipes[2]); fclose($pipes[2]);
            requireControl(proc_close($process) === 0 && !Schema::hasTable('node_endpoint_settings') && !DB::table('migrations')->where('migration', $migration)->exists() && !is_file($caPath), 'OWN_PURGE_FAILED');
            foreach ($ownedTokens as $token) {
                if (tokenRecordControl($token, false)) DB::table('personal_access_tokens')->where('id', $token['id'])->delete();
            }
            requireControl(treesControl($pluginDir) === $state['files'] && treesControl($publicDir) === $state['public_assets'], 'OWNED_PLUGIN_CHANGED_BEFORE_DELETE');
            removeTreeControl($pluginDir); removeTreeControl($publicDir);
            if (!$state['ca_directory_existed'] && is_dir(dirname($caPath)) && count(scandir(dirname($caPath))) === 2) rmdir(dirname($caPath));
            foreach (array_reverse($state['created_dirs']) as $directory) if (is_dir($directory) && !is_link($directory) && count(scandir($directory)) === 2) rmdir($directory);
            $state['status'] = 'cleaned'; saveControl($statePath, $state); unlink($tokensPath);
            $checks = compareControl($state, $appPath); requireControl(!in_array(false, $checks, true), 'FOREIGN_STATE_CHANGED_ON_CLEANUP');
            $result = ['status' => 'CLEANED', 'run' => $run, 'checks' => $checks,
                'native_uninstall_preserved_settings_and_public_ca_before_explicit_purge' => true,
                'own_settings_table_migration_marker_and_public_ca_purged' => true,
                'own_plugin_source_and_public_assets_removed' => !file_exists($pluginDir) && !file_exists($publicDir),
                'ephemeral_tokens_removed' => true, 'root_entry_and_pki_touched' => false];
        } else {
            $checks = compareControl($state, $appPath);
            $ownedTokens = array_merge(array_values($tokens['tokens']), $tokens['retired'] ?? []);
            foreach ($ownedTokens as $token) tokenRecordControl($token, false);
            $tokenActive = $tokenExpired = $tokenMissing = 0;
            foreach ($tokens['tokens'] as $token) {
                $record = tokenRecordControl($token, false);
                if (!$record) $tokenMissing++;
                elseif ($record->expires_at !== null && \Illuminate\Support\Carbon::parse($record->expires_at)->isPast()) $tokenExpired++;
                else $tokenActive++;
            }
            $lastBatch = DB::table('migrations')->max('batch');
            $lastMigrations = DB::table('migrations')->where('batch', $lastBatch)->pluck('migration')->all();
            $caMatches = !is_link(dirname($caPath)) && !is_link($caPath) &&
                (isset($state['ca_sha256']) ? is_file($caPath) && hash_equals($state['ca_sha256'], hash_file('sha256', $caPath)) : !file_exists($caPath));
            $result = ['status' => 'OK', 'run' => $run, 'version' => Plugin::where('code', 'node_endpoint')->value('version'), 'checks' => $checks,
                'cleanup_preflight' => ['native_plugin_row_matches_owned' => (int) Plugin::where('code', 'node_endpoint')->value('id') === $state['plugin_id'],
                    'last_migration_batch_only_owned' => $lastMigrations === [$migration],
                    'owned_settings_table_present' => Schema::hasTable('node_endpoint_settings'), 'owned_public_ca_matches' => $caMatches,
                    'owned_ephemeral_records_validated' => true, 'owned_current_active_token_count' => $tokenActive,
                    'owned_current_expired_token_count' => $tokenExpired, 'owned_current_missing_token_count' => $tokenMissing,
                    'foreign_state_unchanged' => !in_array(false, $checks, true),
                    'cleanup_executed' => false, 'requires_coordinator_node_cleanup_confirmation' => true]];
        }
    }
    while (ob_get_level() > $outputLevel) ob_end_clean();
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
} catch (Throwable $error) {
    while (ob_get_level() > $outputLevel) ob_end_clean();
    echo json_encode(['status' => 'ERROR', 'reason' => $error instanceof E2EPluginException ? $error->getMessage() : 'APPLICATION_OR_HELPER_ERROR',
        'error_class' => get_class($error), 'helper_line' => $error->getFile() === __FILE__ ? $error->getLine() : null,
        'original_device_limit_restored_on_failure' => $adminUserLimitRestored ?? null], JSON_THROW_ON_ERROR) . "\n";
    exit(1);
}
