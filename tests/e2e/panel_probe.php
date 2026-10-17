<?php
declare(strict_types=1);

/* Test-only CLI. It boots the installed application and never patches its core.
 * Credentials remain in a private stage directory; public output is explicit.
 * Usage: php panel_probe.php inventory|create|inspect|rotate|cleanup|settings-enable|settings-restore|auth-enable|auth-restore --app=/path/xboard
 *        --stage=/private/e2e-stage --run=run-id [--export-node-token=YES]
 * create reads <run>-spec.json from stage (0600), e.g.
 * {"nodes":[{"label":"xray-a","mode":"node","protocol":"vless","port":25101},
 *           {"label":"singbox-a","mode":"machine","protocol":"socks","port":25102}]}
 * cleanup additionally requires --confirm=STOPPED_AND_DRAINED.
 * rotate accepts --sync-mode=observer-only to use the native UserObserver and
 * node_sync queue without the historical helper's extra full-sync messages.
 * --sync-mode=delta explicitly invokes the original NodeSyncService after save;
 * this mode verifies native Redis/push behavior, not Observer/queue delivery.
 * The owner must stop its own node processes before cleanup. No shared queue is
 * drained and no shared cache is flushed. Only settings-enable can temporarily
 * change server_ws_enable (and optionally server_ws_url); settings-restore uses
 * an exact applied-row comparison before restoring the original raw rows.
 */

use App\Models\Plan;
use App\Models\Server;
use App\Models\ServerGroup;
use App\Models\ServerMachine;
use App\Models\User;
use App\Services\ServerService;
use App\Services\NodeSyncService;
use App\Utils\CacheKey;
use Illuminate\Contracts\Console\Kernel;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Queue;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

final class PanelProbeException extends RuntimeException {}
function abortProbe(string $reason): never { throw new PanelProbeException($reason); }
function outputProbe(array $result): void {
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
}
function privateFile(string $path): void {
    if (!is_file($path) || is_link($path)) abortProbe('PRIVATE_FILE_REQUIRED');
    if (PHP_OS_FAMILY !== 'Windows' &&
        ((fileperms($path) & 0077) !== 0 || fileowner($path) !== posix_geteuid())) {
        abortProbe('PRIVATE_FILE_OWNER_OR_MODE');
    }
}
function readPrivate(string $path): array {
    privateFile($path);
    $data = json_decode((string) file_get_contents($path), true, 32, JSON_THROW_ON_ERROR);
    if (!is_array($data)) abortProbe('INVALID_PRIVATE_JSON');
    return $data;
}
function writePrivate(string $path, array $data, bool $replace = false): void {
    if (is_link($path)) abortProbe('SYMLINK_REFUSED');
    if (file_exists($path)) {
        privateFile($path);
        if (!$replace) abortProbe('PRIVATE_FILE_ALREADY_EXISTS');
    }
    $temp = $path . '.tmp-' . bin2hex(random_bytes(8));
    $handle = fopen($temp, 'x');
    if (!$handle) abortProbe('PRIVATE_WRITE_FAILED');
    try {
        if (PHP_OS_FAMILY !== 'Windows' && !chmod($temp, 0600)) abortProbe('CHMOD_FAILED');
        $json = json_encode($data, JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n";
        if (fwrite($handle, $json) !== strlen($json)) abortProbe('PRIVATE_WRITE_FAILED');
        fflush($handle);
    } finally { fclose($handle); }
    if (!rename($temp, $path)) { unlink($temp); abortProbe('PRIVATE_RENAME_FAILED'); }
}
function checkColumns(string $table, array $columns): void {
    if (!Schema::hasTable($table) || array_diff($columns, Schema::getColumnListing($table))) {
        abortProbe('UNSUPPORTED_SCHEMA_' . $table);
    }
}
function schemaProbe(): void {
    foreach ([
        'v2_server_group' => ['id', 'name'],
        'v2_plan' => ['id', 'name', 'group_id', 'transfer_enable', 'sell', 'reset_traffic_method'],
        'v2_user' => ['id', 'email', 'uuid', 'token', 'password', 'group_id', 'plan_id', 'u', 'd', 'online_count'],
        'v2_server_machine' => ['id', 'name', 'token', 'is_active'],
        'v2_server' => ['id', 'name', 'type', 'code', 'machine_id', 'enabled', 'group_ids', 'protocol_settings', 'custom_routes', 'u', 'd'],
        'v2_stat_user' => ['id', 'user_id', 'u', 'd'],
        'v2_stat_server' => ['id', 'server_id', 'server_type', 'u', 'd'],
    ] as $table => $columns) checkColumns($table, $columns);
}
function globalGuard(): array {
    return [
        'app_url' => config('app.url'),
        'server_token' => admin_setting('server_token'),
        'server_ws_enable' => admin_setting('server_ws_enable', 1),
        'server_ws_url' => admin_setting('server_ws_url', ''),
        'server_push_interval' => admin_setting('server_push_interval', 60),
        'server_pull_interval' => admin_setting('server_pull_interval', 60),
    ];
}
function rowsGuard(string $key, array $exclude = []): array {
    $result = [];
    foreach (['v2_server_group', 'v2_plan', 'v2_user', 'v2_server_machine', 'v2_server', 'v2_stat_user', 'v2_stat_server', 'v2_plugins'] as $table) {
        if (!Schema::hasTable($table)) continue;
        $query = DB::table($table)->orderBy('id');
        if ($table === 'v2_plugins') $query->where('code', '<>', 'node_endpoint');
        foreach ($exclude[$table] ?? [] as $field => $ids) $query->whereNotIn($field, $ids);
        $hash = hash_init('sha256', HASH_HMAC, $key);
        $count = 0;
        foreach ($query->cursor() as $row) {
            $values = (array) $row;
            ksort($values);
            hash_update($hash, json_encode($values, JSON_THROW_ON_ERROR) . "\n");
            $count++;
        }
        $result[$table] = ['count' => $count, 'hmac' => hash_final($hash)];
    }
    return $result;
}
function queueProbe(): array {
    $result = [];
    foreach (['traffic_fetch', 'stat', 'node_sync'] as $queue) $result[$queue] = Queue::size($queue);
    return $result;
}
function wsRows(): array {
    $result = [];
    foreach (['server_ws_enable', 'server_ws_url'] as $key) {
        $row = DB::table('v2_settings')->where('name', $key)->first();
        $result[$key] = $row ? (array) $row : null;
    }
    return $result;
}
function nodeTokenRow(): ?array {
    $row = DB::table('v2_settings')->where('name', 'server_token')->first();
    return $row ? (array) $row : null;
}
function resetSettingsCache(): void {
    Cache::store('redis')->forget(\App\Support\Setting::CACHE_KEY);
    app()->forgetInstance(\App\Support\Setting::class);
}
function loadOwned(array $state, string $run): array {
    if (($state['schema'] ?? null) !== 1 || ($state['run'] ?? null) !== $run || ($state['status'] ?? null) !== 'created') {
        abortProbe('STATE_OWNER_MISMATCH');
    }
    $prefix = 'NE-E2E-' . $run;
    $group = ServerGroup::findOrFail((int) $state['ids']['group']);
    $plan = Plan::findOrFail((int) $state['ids']['plan']);
    $user = User::findOrFail((int) $state['ids']['user']);
    $machine = ServerMachine::findOrFail((int) $state['ids']['machine']);
    if ($group->name !== $prefix || $plan->name !== $prefix || $machine->name !== $prefix ||
        $user->email !== $run . '@node-endpoint-e2e.invalid' || $user->remarks !== $prefix ||
        (int) $user->group_id !== (int) $group->id || (int) $user->plan_id !== (int) $plan->id ||
        (int) $plan->group_id !== (int) $group->id || $user->is_admin || $user->is_staff) {
        abortProbe('SYNTHETIC_RECORD_MARKER_MISMATCH');
    }
    $nodes = [];
    foreach ($state['nodes'] as $entry) {
        $node = Server::findOrFail((int) $entry['id']);
        if ($node->name !== $prefix . '-' . $entry['label'] || $node->code !== $prefix . '-' . $entry['label'] ||
            $node->type !== $entry['protocol'] || $node->group_ids !== [(string) $group->id] ||
            (int) $node->machine_id !== ($entry['mode'] === 'machine' ? (int) $machine->id : 0) || $node->parent_id) {
            abortProbe('SYNTHETIC_NODE_MARKER_MISMATCH');
        }
        if (ServerService::getServer($node->id)?->id !== $node->id) abortProbe('OFFICIAL_NODE_ID_RESOLUTION_COLLISION');
        $nodes[] = $node;
    }
    return compact('group', 'plan', 'user', 'machine', 'nodes');
}
function excludeOwned(array $state): array {
    return [
        'v2_server_group' => ['id' => [$state['ids']['group']]],
        'v2_plan' => ['id' => [$state['ids']['plan']]],
        'v2_user' => ['id' => [$state['ids']['user']]],
        'v2_server_machine' => ['id' => [$state['ids']['machine']]],
        'v2_server' => ['id' => array_column($state['nodes'], 'id')],
        'v2_stat_user' => ['user_id' => [$state['ids']['user']]],
        'v2_stat_server' => ['server_id' => array_column($state['nodes'], 'id')],
    ];
}
function inspectOwned(array $state, array $owned): array {
    $uid = (int) $owned['user']->id;
    $devices = Redis::hgetall('user_devices:' . $uid);
    $nodes = [];
    foreach ($owned['nodes'] as $index => $node) {
        $id = (int) $node->id;
        $type = strtoupper($node->type);
        $metrics = Cache::get(CacheKey::get("SERVER_{$type}_METRICS", $id), []);
        $nodeDevices = $storedNodeDevices = 0;
        foreach ($devices as $field => $timestamp) {
            if (str_starts_with((string) $field, $id . ':')) {
                $storedNodeDevices++;
                if (time() - (int) $timestamp <= 300) $nodeDevices++;
            }
        }
        $effective = ServerService::buildNodeConfig($node);
        $availableUsers = ServerService::getAvailableUsers($node)->pluck('id')->map(fn($id) => (int) $id)->all();
        if (array_diff($availableUsers, [$uid])) abortProbe('FOREIGN_USER_IN_SYNTHETIC_NODE');
        $nodes[] = [
            'id' => $id, 'label' => $state['nodes'][$index]['label'], 'type' => $node->type,
            'effective_protocol' => $effective['protocol'] ?? null,
            'server_port' => (int) ($effective['server_port'] ?? 0),
            'available_users' => $availableUsers,
            'ws_alive' => (bool) Cache::get('node_ws_alive:' . $id, false),
            'last_check_at' => (int) Cache::get(CacheKey::get("SERVER_{$type}_LAST_CHECK_AT", $id), 0),
            'last_push_at' => (int) Cache::get(CacheKey::get("SERVER_{$type}_LAST_PUSH_AT", $id), 0),
            'online_users' => (int) Cache::get(CacheKey::get("SERVER_{$type}_ONLINE_USER", $id), 0),
            'online_connections' => (int) Cache::get(CacheKey::get("USER_ONLINE_CONN_{$node->type}_{$id}", $uid), 0),
            'device_entries' => $nodeDevices,
            'stored_device_entries' => $storedNodeDevices,
            'metrics_updated_at' => (int) ($metrics['updated_at'] ?? 0),
            'metrics_kernel_status' => (bool) ($metrics['kernel_status'] ?? false),
            'metrics_active_connections' => (int) ($metrics['active_connections'] ?? 0),
            'metrics_ws_enabled' => (bool) ($metrics['ws']['enabled'] ?? false),
            'metrics_ws_connected' => (bool) ($metrics['ws']['connected'] ?? false),
            'u' => (int) $node->u, 'd' => (int) $node->d,
            'stat_u' => (int) DB::table('v2_stat_server')->where('server_id', $id)->sum('u'),
            'stat_d' => (int) DB::table('v2_stat_server')->where('server_id', $id)->sum('d'),
        ];
    }
    $globals = globalGuard(); $otherGlobals = $globals; $expectedOtherGlobals = $state['globals'];
    unset($otherGlobals['server_token'], $expectedOtherGlobals['server_token']);
    $authActive = $authMatches = false;
    if (is_file($GLOBALS['authPath'] ?? '')) {
        $auth = readPrivate($GLOBALS['authPath']);
        $authActive = ($auth['schema'] ?? null) === 1 && ($auth['run'] ?? null) === $state['run'] &&
            ($auth['app_path'] ?? null) === $state['app_path'] && ($auth['status'] ?? null) === 'applied';
        $authMatches = $authActive && nodeTokenRow() === ($auth['applied'] ?? null) &&
            $globals['server_token'] === ($auth['applied']['value'] ?? null);
    }
    return [
        'run' => $state['run'], 'worker_heartbeat' => Cache::has('ws_server:heartbeat'),
        'queue' => queueProbe(), 'nodes' => $nodes,
        'user' => ['id' => $uid, 'u' => (int) $owned['user']->u, 'd' => (int) $owned['user']->d,
            'stat_u' => (int) DB::table('v2_stat_user')->where('user_id', $uid)->sum('u'),
            'stat_d' => (int) DB::table('v2_stat_user')->where('user_id', $uid)->sum('d'),
            'online_count' => (int) $owned['user']->online_count],
        'machine' => ['id' => (int) $owned['machine']->id, 'last_seen_at' => (int) $owned['machine']->last_seen_at,
            'binding_count' => $owned['machine']->servers()->count(),
            'history_count' => Schema::hasTable('v2_server_machine_load_history')
                ? DB::table('v2_server_machine_load_history')->where('machine_id', $owned['machine']->id)->count() : 0],
        'global_settings_unchanged' => $state['globals'] === $globals,
        'test_auth_active' => $authActive,
        'test_auth_setting_matches_owned' => $authMatches,
        'other_global_settings_unchanged' => $otherGlobals === $expectedOtherGlobals,
        'foreign_records_unchanged' => $state['foreign'] === rowsGuard($state['guard_key'], excludeOwned($state)),
    ];
}

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
ini_set('display_errors', '0');
umask(0077);
$bootLevel = ob_get_level();
ob_start();
try {
    $command = $argv[1] ?? '';
    if (!in_array($command, ['inventory', 'create', 'inspect', 'rotate', 'cleanup', 'settings-enable', 'settings-restore', 'auth-enable', 'auth-restore'], true)) abortProbe('INVALID_COMMAND');
    $options = [];
    foreach (array_slice($argv, 2) as $arg) {
        if (!preg_match('/^--([a-z-]+)=(.*)$/D', $arg, $match) || isset($options[$match[1]])) abortProbe('INVALID_OPTION');
        $options[$match[1]] = $match[2];
    }
    if (array_diff(array_keys($options), ['app', 'stage', 'run', 'confirm', 'export-node-token', 'clear-ws-url', 'sync-mode'])) abortProbe('UNKNOWN_OPTION');
    if (isset($options['sync-mode']) && $command !== 'rotate') abortProbe('SYNC_MODE_ONLY_FOR_ROTATE');
    $syncMode = $options['sync-mode'] ?? 'full';
    if (!in_array($syncMode, ['full', 'observer-only', 'delta'], true)) abortProbe('INVALID_SYNC_MODE');
    $run = $options['run'] ?? '';
    if (!preg_match('/^[a-z0-9][a-z0-9-]{5,27}$/D', $run)) abortProbe('INVALID_RUN_ID');
    $appPath = realpath($options['app'] ?? '');
    $stage = realpath($options['stage'] ?? '');
    if (!$appPath || !$stage || is_link($options['stage']) || !is_dir($stage) ||
        !is_file($appPath . '/vendor/autoload.php') || !is_file($appPath . '/bootstrap/app.php')) abortProbe('INVALID_APP_OR_STAGE');
    if (PHP_OS_FAMILY !== 'Windows' && ((fileperms($stage) & 0077) !== 0 || fileowner($stage) !== posix_geteuid())) {
        abortProbe('STAGE_OWNER_OR_MODE');
    }
    privateFile($stage . '/.node-endpoint-e2e-owner');
    $statePath = $stage . '/' . $run . '-state.json';
    $credentialsPath = $stage . '/' . $run . '-credentials.json';
    $settingsPath = $stage . '/' . $run . '-settings.json';
    $authPath = $stage . '/' . $run . '-auth.json';
    if (is_link($stage . '/' . $run . '.lock')) abortProbe('SYMLINK_REFUSED');
    if (file_exists($stage . '/' . $run . '.lock')) privateFile($stage . '/' . $run . '.lock');
    $lock = fopen($stage . '/' . $run . '.lock', 'c');
    if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) abortProbe('RUN_LOCK_BUSY');
    if (PHP_OS_FAMILY !== 'Windows') chmod($stage . '/' . $run . '.lock', 0600);
    require $appPath . '/vendor/autoload.php';
    $app = require $appPath . '/bootstrap/app.php';
    $app->make(Kernel::class)->bootstrap();
    schemaProbe();

    if ($command === 'inventory') {
        $counts = [];
        foreach (['v2_user', 'v2_server_group', 'v2_plan', 'v2_server', 'v2_server_machine', 'v2_stat_user', 'v2_stat_server'] as $table) {
            $counts[$table] = DB::table($table)->count();
        }
        $result = ['status' => 'OK', 'laravel' => $app->version(), 'counts' => $counts,
            'worker_heartbeat' => Cache::has('ws_server:heartbeat'),
            'ws_enabled' => (bool) admin_setting('server_ws_enable', 1),
            'ws_url_present' => trim((string) admin_setting('server_ws_url', '')) !== '',
            'ws_setting_row_exists' => DB::table('v2_settings')->where('name', 'server_ws_enable')->exists(),
            'node_token_present' => is_string(admin_setting('server_token')) && admin_setting('server_token') !== '',
            'queue_driver' => config('queue.default'), 'queue' => queueProbe()];
    } elseif ($command === 'auth-enable') {
        if (($options['confirm'] ?? '') !== 'TEMPORARY_NODE_TOKEN_ON_EMPTY_PANEL') abortProbe('AUTH_CONFIRMATION_REQUIRED');
        if (file_exists($authPath)) abortProbe('AUTH_SNAPSHOT_ALREADY_EXISTS');
        if (!in_array(admin_setting('server_token'), [null, ''], true) || Server::exists() || ServerMachine::exists()) {
            abortProbe('AUTH_REQUIRES_EMPTY_PANEL_AND_EMPTY_TOKEN');
        }
        $auth = ['schema' => 1, 'run' => $run, 'app_path' => $appPath, 'status' => 'prepared',
            'original' => nodeTokenRow(), 'original_effective' => admin_setting('server_token')];
        writePrivate($authPath, $auth);
        DB::transaction(function () { \App\Models\Setting::createOrUpdate('server_token', bin2hex(random_bytes(24))); });
        $auth['applied'] = nodeTokenRow(); $auth['status'] = 'applied';
        writePrivate($authPath, $auth, true);
        resetSettingsCache();
        $result = ['status' => 'AUTH_APPLIED', 'run' => $run, 'node_token_present' => true];
    } elseif ($command === 'auth-restore') {
        if (($options['confirm'] ?? '') !== 'STOPPED_AND_DRAINED') abortProbe('STOPPED_CONFIRMATION_REQUIRED');
        $auth = readPrivate($authPath);
        if (($auth['schema'] ?? null) !== 1 || ($auth['run'] ?? null) !== $run ||
            ($auth['app_path'] ?? null) !== $appPath || ($auth['status'] ?? null) !== 'applied') abortProbe('AUTH_OWNER_MISMATCH');
        $ownNodeIds = $ownMachineIds = [];
        if (is_file($statePath)) {
            $ownedState = readPrivate($statePath);
            if (($ownedState['run'] ?? null) !== $run || ($ownedState['app_path'] ?? null) !== $appPath) abortProbe('AUTH_STATE_OWNER_MISMATCH');
            if (($ownedState['status'] ?? null) === 'created') loadOwned($ownedState, $run);
            elseif (($ownedState['status'] ?? null) !== 'cleaned') abortProbe('AUTH_STATE_NOT_CREATED_OR_CLEANED');
            $ownNodeIds = array_column($ownedState['nodes'] ?? [], 'id');
            $ownMachineIds = [$ownedState['ids']['machine'] ?? 0];
        }
        if (Server::whereNotIn('id', $ownNodeIds)->exists() || ServerMachine::whereNotIn('id', $ownMachineIds)->exists()) {
            abortProbe('AUTH_FOREIGN_NODES_APPEARED');
        }
        foreach ($ownNodeIds as $id) if (Cache::get('node_ws_alive:' . (int) $id)) abortProbe('AUTH_NODES_STILL_CONNECTED');
        if (array_filter(queueProbe())) abortProbe('AUTH_QUEUES_STILL_ACTIVE');
        DB::transaction(function () use ($auth) {
            if (nodeTokenRow() !== $auth['applied']) abortProbe('AUTH_CHANGED_AFTER_TEST');
            if ($auth['original'] === null) DB::table('v2_settings')->where('name', 'server_token')->delete();
            else {
                $row = $auth['original']; unset($row['id'], $row['name']);
                DB::table('v2_settings')->where('name', 'server_token')->update($row);
            }
        });
        resetSettingsCache();
        $auth['status'] = 'restored'; writePrivate($authPath, $auth, true);
        $result = ['status' => 'AUTH_RESTORED', 'run' => $run, 'raw_row_restored' => nodeTokenRow() === $auth['original']];
    } elseif ($command === 'settings-enable') {
        if (($options['confirm'] ?? '') !== 'TEMPORARY_WS_ONLY') abortProbe('WS_SETTINGS_CONFIRMATION_REQUIRED');
        if (file_exists($settingsPath)) abortProbe('SETTINGS_SNAPSHOT_ALREADY_EXISTS');
        checkColumns('v2_settings', ['name', 'value']);
        $settings = ['schema' => 1, 'run' => $run, 'app_path' => $appPath, 'status' => 'prepared', 'original' => wsRows()];
        // Journal precedes mutation. A failed application can still be inspected privately.
        writePrivate($settingsPath, $settings);
        DB::transaction(function () use ($options) {
            \App\Models\Setting::createOrUpdate('server_ws_enable', '1');
            if (($options['clear-ws-url'] ?? '') === 'YES') \App\Models\Setting::createOrUpdate('server_ws_url', '');
        });
        $settings['applied'] = wsRows(); $settings['status'] = 'applied';
        writePrivate($settingsPath, $settings, true);
        resetSettingsCache();
        $result = ['status' => 'SETTINGS_APPLIED', 'run' => $run, 'ws_enabled' => true,
            'ws_url_cleared' => ($options['clear-ws-url'] ?? '') === 'YES'];
    } elseif ($command === 'settings-restore') {
        $settings = readPrivate($settingsPath);
        if (($settings['schema'] ?? null) !== 1 || ($settings['run'] ?? null) !== $run ||
            ($settings['app_path'] ?? null) !== $appPath || ($settings['status'] ?? null) !== 'applied') abortProbe('SETTINGS_OWNER_MISMATCH');
        DB::transaction(function () use ($settings) {
            if (wsRows() !== $settings['applied']) abortProbe('SETTINGS_CHANGED_AFTER_TEST');
            foreach ($settings['original'] as $key => $row) {
                if ($row === null) DB::table('v2_settings')->where('name', $key)->delete();
                else {
                    unset($row['id'], $row['name']);
                    DB::table('v2_settings')->where('name', $key)->update($row);
                }
            }
        });
        resetSettingsCache();
        $settings['status'] = 'restored'; writePrivate($settingsPath, $settings, true);
        $result = ['status' => 'SETTINGS_RESTORED', 'run' => $run, 'raw_rows_restored' => wsRows() === $settings['original']];
    } elseif ($command === 'create') {
        if (file_exists($statePath) || file_exists($credentialsPath)) abortProbe('RUN_ALREADY_EXISTS');
        $spec = readPrivate($stage . '/' . $run . '-spec.json');
        if (array_keys($spec) !== ['nodes'] || !is_array($spec['nodes']) || !array_is_list($spec['nodes']) ||
            count($spec['nodes']) < 1 || count($spec['nodes']) > 8) abortProbe('INVALID_NODE_SPEC');
        $labels = $ports = [];
        $needsNodeToken = false;
        foreach ($spec['nodes'] as $entry) {
            if (array_diff(array_keys($entry), ['label', 'mode', 'protocol', 'port']) ||
                !preg_match('/^[a-z0-9][a-z0-9-]{0,15}$/D', $entry['label'] ?? '') ||
                !in_array($entry['mode'] ?? '', ['node', 'machine'], true) ||
                !in_array($entry['protocol'] ?? '', ['vless', 'socks'], true) ||
                !is_int($entry['port'] ?? null) || $entry['port'] < 1024 || $entry['port'] > 65535 ||
                in_array($entry['label'], $labels, true) || in_array($entry['port'], $ports, true)) abortProbe('INVALID_NODE_SPEC');
            $labels[] = $entry['label']; $ports[] = $entry['port'];
            $needsNodeToken = $needsNodeToken || $entry['mode'] === 'node';
        }
        if ($needsNodeToken && ($options['export-node-token'] ?? '') !== 'YES') abortProbe('NODE_TOKEN_PRIVATE_EXPORT_NOT_AUTHORIZED');
        if ($needsNodeToken && (!is_string(admin_setting('server_token')) || admin_setting('server_token') === '')) abortProbe('NODE_TOKEN_MISSING');
        $prefix = 'NE-E2E-' . $run;
        foreach ([ServerGroup::class, Plan::class, ServerMachine::class, Server::class] as $model) {
            if ($model::where('name', 'like', $prefix . '%')->exists()) abortProbe('SYNTHETIC_NAME_COLLISION');
        }
        if (User::where('email', $run . '@node-endpoint-e2e.invalid')->exists()) abortProbe('SYNTHETIC_EMAIL_COLLISION');
        $state = ['schema' => 1, 'run' => $run, 'status' => 'preparing', 'created_at' => time(),
            'app_path' => $appPath, 'guard_key' => bin2hex(random_bytes(32)), 'globals' => globalGuard()];
        if (is_file($authPath)) {
            $auth = readPrivate($authPath);
            if (($auth['run'] ?? null) !== $run || ($auth['app_path'] ?? null) !== $appPath ||
                ($auth['status'] ?? null) !== 'applied' || nodeTokenRow() !== $auth['applied']) abortProbe('AUTH_CREATE_OWNER_MISMATCH');
            // The final guard targets the original empty-token state after auth-restore.
            $state['globals']['server_token'] = $auth['original_effective'];
        }
        $state['foreign'] = rowsGuard($state['guard_key']);
        DB::transaction(function () use (&$state, $spec, $prefix, $run, $needsNodeToken, $credentialsPath, $statePath) {
            $group = new ServerGroup(); $group->forceFill(['name' => $prefix])->save();
            $plan = new Plan();
            $plan->forceFill(['name' => $prefix, 'group_id' => $group->id, 'transfer_enable' => 1073741824,
                'show' => false, 'sell' => false, 'renew' => false, 'prices' => [], 'reset_traffic_method' => 2])->save();
            $machine = new ServerMachine();
            $machine->forceFill(['name' => $prefix, 'notes' => $prefix, 'token' => ServerMachine::generateToken(), 'is_active' => true])->save();
            $user = new User();
            $user->forceFill(['email' => $run . '@node-endpoint-e2e.invalid', 'password' => password_hash(bin2hex(random_bytes(32)), PASSWORD_BCRYPT),
                'uuid' => (string) Str::uuid(), 'token' => bin2hex(random_bytes(16)), 'group_id' => $group->id, 'plan_id' => $plan->id,
                'transfer_enable' => 1073741824, 'u' => 0, 'd' => 0, 'banned' => false, 'is_admin' => false, 'is_staff' => false,
                'expired_at' => time() + 86400, 'remind_expire' => false, 'remind_traffic' => false, 'device_limit' => 16,
                'remarks' => $prefix])->save();
            $state['ids'] = ['group' => (int) $group->id, 'plan' => (int) $plan->id, 'machine' => (int) $machine->id, 'user' => (int) $user->id];
            $state['nodes'] = [];
            foreach ($spec['nodes'] as $entry) {
                $node = new Server();
                $customRoutes = $entry['protocol'] === 'vless'
                    ? [['type' => 'field', 'ip' => ['127.0.0.1/32'], 'outboundTag' => 'direct']]
                    : [['ip_cidr' => ['127.0.0.1/32'], 'outbound' => 'direct']];
                $node->forceFill(['name' => $prefix . '-' . $entry['label'], 'code' => $prefix . '-' . $entry['label'],
                    'type' => $entry['protocol'], 'group_ids' => [(string) $group->id], 'route_ids' => [], 'tags' => [$prefix],
                    'machine_id' => $entry['mode'] === 'machine' ? $machine->id : null, 'enabled' => true, 'show' => false,
                    'host' => '127.0.0.1', 'port' => (string) $entry['port'], 'server_port' => $entry['port'], 'rate' => 1,
                    'protocol_settings' => ['tls' => 0, 'flow' => '', 'network' => 'tcp', 'tls_settings' => []],
                    'custom_routes' => $customRoutes,
                    'u' => 0, 'd' => 0, 'transfer_enable' => null])->save();
                $state['nodes'][] = [...$entry, 'id' => (int) $node->id];
            }
            $credentials = ['schema' => 1, 'run_id' => $run,
                'user' => ['id' => (int) $user->id, 'uuid' => $user->uuid],
                'machine' => ['id' => (int) $machine->id, 'token' => $machine->token],
                'nodes' => array_map(fn($entry) => [...$entry, 'kernel' => $entry['protocol'] === 'vless' ? 'xray' : 'singbox'], $state['nodes'])];
            if ($needsNodeToken) $credentials['node_token'] = admin_setting('server_token');
            $state['status'] = 'created';
            // Written before commit so a crash after commit still leaves IDs for cleanup.
            writePrivate($credentialsPath, $credentials);
            writePrivate($statePath, $state);
        });
        $result = inspectOwned($state, loadOwned($state, $run));
        $result['status'] = 'CREATED';
        $result['credentials_private_file'] = basename($credentialsPath);
    } else {
        $state = readPrivate($statePath);
        if (($state['app_path'] ?? null) !== $appPath) abortProbe('APP_STATE_MISMATCH');
        $owned = loadOwned($state, $run);
        $result = inspectOwned($state, $owned);
        if ($command === 'rotate') {
            if (($options['confirm'] ?? '') !== 'ROTATE_SYNTHETIC_USER_ONLY') abortProbe('ROTATE_CONFIRMATION_REQUIRED');
            $credentials = readPrivate($credentialsPath);
            if (($credentials['run_id'] ?? null) !== $run || ($credentials['user']['id'] ?? null) !== (int) $owned['user']->id) {
                abortProbe('CREDENTIALS_OWNER_MISMATCH');
            }
            $uuid = (string) Str::uuid();
            $owned['user']->forceFill(['uuid' => $uuid])->save();
            $credentials['user']['uuid'] = $uuid;
            writePrivate($credentialsPath, $credentials, true);
            // The full mode is retained for reproducing run003. Observer-only
            // matches a native model save: UserObserver dispatches NodeUserSyncJob,
            // which publishes sync.user.delta after reading the current DB row.
            if ($syncMode === 'full') {
                foreach ($owned['nodes'] as $node) NodeSyncService::notifyFullSync((int) $node->id);
            } elseif ($syncMode === 'delta') {
                NodeSyncService::notifyUserChanged($owned['user']);
            }
            $result = inspectOwned($state, loadOwned($state, $run));
            $result['status'] = 'USER_ROTATED';
            $result['sync_mode'] = $syncMode;
            $result['credentials_private_file'] = basename($credentialsPath);
        } elseif ($command === 'cleanup') {
            if (($options['confirm'] ?? '') !== 'STOPPED_AND_DRAINED') abortProbe('STOPPED_CONFIRMATION_REQUIRED');
            if (array_filter($result['queue']) || array_filter($result['nodes'], fn($n) => $n['ws_alive'] || $n['device_entries'])) {
                abortProbe('NODES_OR_QUEUES_STILL_ACTIVE');
            }
            if (User::where('group_id', $owned['group']->id)->where('id', '<>', $owned['user']->id)->exists() ||
                Plan::where('group_id', $owned['group']->id)->where('id', '<>', $owned['plan']->id)->exists() ||
                User::where('plan_id', $owned['plan']->id)->where('id', '<>', $owned['user']->id)->exists() ||
                Server::where('machine_id', $owned['machine']->id)->whereNotIn('id', array_column($state['nodes'], 'id'))->exists() ||
                Server::whereJsonContains('group_ids', (string) $owned['group']->id)->whereNotIn('id', array_column($state['nodes'], 'id'))->exists()) {
                abortProbe('FOREIGN_REFERENCE_TO_SYNTHETIC_RECORD');
            }
            $uid = (int) $owned['user']->id;
            foreach ($owned['nodes'] as $node) {
                $id = (int) $node->id; $type = strtoupper($node->type);
                foreach (['ONLINE_USER', 'LAST_CHECK_AT', 'LAST_PUSH_AT', 'LOAD_STATUS', 'LAST_LOAD_AT', 'METRICS'] as $suffix) {
                    Cache::forget(CacheKey::get("SERVER_{$type}_{$suffix}", $id));
                }
                Cache::forget(CacheKey::get("USER_ONLINE_CONN_{$node->type}_{$id}", $uid));
                Cache::forget('node_ws_alive:' . $id);
                Redis::srem('device:push_pending_nodes', $id);
            }
            Redis::del('user_devices:' . $uid, 'device:db_throttle:' . $uid);
            Redis::srem('traffic:pending_check', $uid);
            DB::transaction(function () use ($owned, $uid, $state) {
                DB::table('v2_stat_user')->where('user_id', $uid)->delete();
                DB::table('v2_stat_server')->whereIn('server_id', array_column($state['nodes'], 'id'))->delete();
                if (Schema::hasTable('v2_server_machine_load_history')) {
                    DB::table('v2_server_machine_load_history')->where('machine_id', $owned['machine']->id)->delete();
                }
                // Query deletes avoid enqueuing deletion sync after synthetic nodes have stopped.
                foreach ($owned['nodes'] as $node) Server::whereKey($node->id)->delete();
                User::whereKey($uid)->delete();
                Plan::whereKey($owned['plan']->id)->delete();
                ServerMachine::whereKey($owned['machine']->id)->delete();
                ServerGroup::whereKey($owned['group']->id)->delete();
            });
            $state['status'] = 'cleaned'; $state['cleaned_at'] = time();
            writePrivate($statePath, $state, true);
            if (file_exists($credentialsPath)) { privateFile($credentialsPath); unlink($credentialsPath); }
            $result = ['status' => 'CLEANED', 'run' => $run,
                'global_settings_unchanged' => $state['globals'] === globalGuard(),
                'foreign_records_unchanged' => $state['foreign'] === rowsGuard($state['guard_key'])];
        } else $result['status'] = 'OK';
    }
    while (ob_get_level() > $bootLevel) ob_end_clean();
    outputProbe($result);
} catch (Throwable $error) {
    while (ob_get_level() > $bootLevel) ob_end_clean();
    // Never echo arbitrary exception messages/stack arguments from the real app.
    $reason = $error instanceof PanelProbeException && preg_match('/^[A-Z][A-Z0-9_]+$/D', $error->getMessage())
        ? $error->getMessage() : 'APPLICATION_OR_HELPER_ERROR';
    outputProbe(['status' => 'ERROR', 'reason' => $reason, 'error_class' => get_class($error),
        'helper_line' => $error->getFile() === __FILE__ ? $error->getLine() : null]);
    exit(1);
}
