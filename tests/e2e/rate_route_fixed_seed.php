<?php
declare(strict_types=1);

// Dedicated disposable Docker fixture only. The seed/credentials commands emit
// PRIVATE JSON: capture stdout to a 0600 file; never display it in a tool result.
// Example: docker exec -e XBOARD_RATE_ROUTE_FIXTURE=20261024 -i CONTAINER
//          php /tmp/rate-route-seed.php seed < parameters.json
// Other actions: inspect, credentials, mutate, routes, audit, observer-audit.
// Input JSON is optional and is always read from stdin, never command arguments.

if (getenv('XBOARD_RATE_ROUTE_FIXTURE') !== '20261024') {
    throw new RuntimeException('Dedicated fixture authorization marker missing');
}
chdir('/www');
require '/www/vendor/autoload.php';
$app = require '/www/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Models\{Plan, Server, ServerGroup, ServerMachine, ServerRoute, User};
use App\Services\{NodeSyncService, ServerService};
use Illuminate\Support\Facades\{Cache, DB, Redis, Validator};
use Illuminate\Support\Str;

$action = $argv[1] ?? 'inspect';
$raw = stream_get_contents(STDIN);
$input = trim($raw) === '' ? [] : json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
if (!is_array($input)) { throw new RuntimeException('Input must be JSON object'); }
$connection = config('database.default');
$dbPath = config("database.connections.{$connection}.database");
if ($connection !== 'sqlite' || !is_string($dbPath) || $dbPath === ':memory:' || !is_file($dbPath)) {
    throw new RuntimeException('This fixture only accepts an existing dedicated SQLite database');
}
$ownerPath = storage_path('app/private/rate-route-fixture-owner.json');
$owner = is_file($ownerPath) ? json_decode(file_get_contents($ownerPath), true, 32, JSON_THROW_ON_ERROR) : null;
$prefix = 'Codex rate-route 20261024';
function emit(array $result): void { echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n"; }
function requireFixture(): array {
    global $owner, $dbPath, $prefix;
    if (!is_array($owner) || ($owner['tool'] ?? '') !== $prefix || ($owner['database'] ?? '') !== realpath($dbPath)) {
        throw new RuntimeException('Fixture database ownership mismatch');
    }
    foreach ($owner['nodes'] as $id) {
        $node = Server::findOrFail($id);
        if (!str_starts_with($node->name, $prefix)) { throw new RuntimeException('Fixture node identity mismatch'); }
    }
    return $owner;
}
function saveOwner(array $value): void {
    global $ownerPath, $owner;
    if (!is_dir(dirname($ownerPath))) { mkdir(dirname($ownerPath), 0700, true); }
    if (file_put_contents($ownerPath, json_encode($value, JSON_THROW_ON_ERROR)) === false) {
        throw new RuntimeException('Could not save fixture ownership');
    }
    chmod($ownerPath, 0600); $owner = $value;
}
function privateCredentials(array $state): array {
    $machine = ServerMachine::findOrFail($state['machine_id']);
    $users = [];
    foreach ($state['users'] as $alias => $id) {
        $user = User::findOrFail($id);
        $users[$alias] = ['id' => $id, 'uuid' => $user->uuid, 'speed_limit' => $user->speed_limit];
    }
    $nodes = [];
    foreach ($state['nodes'] as $alias => $id) {
        $node = Server::findOrFail($id);
        $nodes[$alias] = ['id' => $id, 'type' => $node->type, 'port' => $node->server_port];
    }
    return ['private' => true, 'machine_id' => $machine->id, 'machine_token' => $machine->token,
        'server_token' => admin_setting('server_token'), 'group_id' => $state['group_id'],
        'plan_id' => $state['plan_id'], 'users' => $users, 'nodes' => $nodes, 'routes' => $state['routes']];
}

if ($action === 'seed') {
    if ($owner !== null || Server::count() !== 0 || ServerMachine::count() !== 0 || ServerGroup::count() !== 0
        || User::where('is_admin', false)->exists()) {
        throw new RuntimeException('Seed refuses a populated or already owned database');
    }
    $ports = ['socks' => (int) ($input['socks_port'] ?? 19090), 'vless' => (int) ($input['vless_port'] ?? 19091)];
    foreach ($ports as $port) { if ($port < 1024 || $port > 65535) { throw new RuntimeException('Invalid test port'); } }
    if ($ports['socks'] === $ports['vless']) { throw new RuntimeException('Test ports must differ'); }
    $state = DB::transaction(function () use ($prefix, $ports, $dbPath): array {
        $group = new ServerGroup(); $group->name = $prefix; $group->save();
        $plan = Plan::create(['group_id' => $group->id, 'name' => $prefix, 'transfer_enable' => 1099511627776,
            'speed_limit' => 0, 'show' => false, 'renew' => false, 'sell' => false,
            'reset_traffic_method' => 2, 'prices' => ['monthly' => 1], 'device_limit' => 0]);
        $machine = ServerMachine::create(['name' => $prefix, 'token' => Str::random(32), 'is_active' => true]);
        $users = [];
        foreach (['A', 'B'] as $alias) {
            $user = User::create(['email' => 'rate-route-' . strtolower($alias) . '@fixture.invalid',
                'password' => password_hash(bin2hex(random_bytes(24)), PASSWORD_BCRYPT),
                'token' => Str::random(32), 'uuid' => (string) Str::uuid(), 'group_id' => $group->id,
                'plan_id' => $plan->id, 'transfer_enable' => 1099511627776, 'expired_at' => time() + 86400,
                'speed_limit' => 0, 'device_limit' => 0, 'u' => 0, 'd' => 0, 'banned' => false]);
            $users[$alias] = $user->id;
        }
        $routes = [];
        foreach (['block' => 'block', 'direct' => 'direct'] as $alias => $ruleAction) {
            $route = ServerRoute::create(['remarks' => $prefix . ' ' . $alias,
                'match' => ['blocked.fixture.test'], 'action' => $ruleAction, 'action_value' => null]);
            $routes[$alias] = $route->id;
        }
        $nodes = [];
        foreach ($ports as $type => $port) {
            $settings = ['tls' => 0, 'tls_settings' => null, 'network' => 'tcp', 'network_settings' => null];
            if ($type === 'vless') { $settings += ['flow' => '', 'encryption' => ['enabled' => false]]; }
            $node = Server::create(['name' => $prefix . ' ' . $type, 'type' => $type,
                'host' => '127.0.0.1', 'port' => (string) $port, 'server_port' => $port,
                'protocol_settings' => $settings, 'group_ids' => [(string) $group->id], 'route_ids' => [],
                'show' => true, 'enabled' => true, 'machine_id' => $machine->id, 'rate' => 1,
                'sort' => $type === 'socks' ? 1 : 2]);
            $nodes[$type] = $node->id;
        }
        return ['tool' => $prefix, 'database' => realpath($dbPath), 'group_id' => $group->id,
            'plan_id' => $plan->id, 'machine_id' => $machine->id, 'users' => $users,
            'nodes' => $nodes, 'routes' => $routes];
    });
    admin_setting(['server_token' => Str::random(32), 'server_pull_interval' => 5, 'server_push_interval' => 5,
        'server_ws_enable' => 1]);
    saveOwner($state); emit(['seeded' => true]); exit;
}

$state = requireFixture();
if ($action === 'credentials') { emit(privateCredentials($state)); exit; }

if ($action === 'mutate') {
    if (array_key_exists('speed_limit', $input)) {
        $limit = $input['speed_limit'];
        if ($limit !== null && (!is_int($limit) || $limit < 0 || $limit > 10000)) {
            throw new RuntimeException('Fixture speed_limit must be null or integer 0..10000');
        }
        $alias = $input['user'] ?? 'A';
        if (!isset($state['users'][$alias])) { throw new RuntimeException('Unknown fixture user'); }
        User::findOrFail($state['users'][$alias])->update(['speed_limit' => $limit]);
    }
    $nodeFields = array_intersect_key($input, array_flip(['route_ids', 'custom_routes', 'custom_outbounds', 'show', 'enabled']));
    if ($nodeFields) {
        $alias = $input['node'] ?? 'socks';
        if (!isset($state['nodes'][$alias])) { throw new RuntimeException('Unknown fixture node'); }
        if (isset($nodeFields['route_ids'])) {
            foreach ($nodeFields['route_ids'] as &$id) {
                if (is_string($id) && isset($state['routes'][$id])) { $id = $state['routes'][$id]; }
                if (!in_array($id, array_values($state['routes']), true)) { throw new RuntimeException('Foreign route ID refused'); }
            }
            unset($id);
        }
        Server::findOrFail($state['nodes'][$alias])->update($nodeFields);
    }
    if (isset($input['route_edit'])) {
        $edit = $input['route_edit']; $alias = $edit['alias'] ?? '';
        if (!isset($state['routes'][$alias])) { throw new RuntimeException('Unknown fixture route'); }
        $fields = array_intersect_key($edit, array_flip(['match', 'action', 'action_value']));
        if (isset($fields['action']) && !in_array($fields['action'], ['block', 'direct', 'dns', 'proxy'], true)) {
            throw new RuntimeException('Unsupported route action');
        }
        ServerRoute::findOrFail($state['routes'][$alias])->update($fields);
    }
    emit(['changed' => true]); exit;
}

if ($action === 'routes') {
    foreach ($input['routes'] ?? [] as $alias => $params) {
        if (!is_string($alias) || !preg_match('/^[a-z][a-z0-9_-]{0,30}$/', $alias)
            || isset($state['routes'][$alias]) || !is_array($params['match'] ?? null)
            || !in_array($params['action'] ?? '', ['block', 'direct', 'dns', 'proxy'], true)) {
            throw new RuntimeException('Invalid new fixture route');
        }
        $route = ServerRoute::create(['remarks' => $prefix . ' ' . $alias, 'match' => $params['match'],
            'action' => $params['action'], 'action_value' => $params['action_value'] ?? null]);
        $state['routes'][$alias] = $route->id;
    }
    saveOwner($state); emit(['route_ids' => $state['routes']]); exit;
}

if ($action === 'audit') {
    // Non-destructive production-method observations; no UUIDs or tokens emitted.
    $routeIds = [$state['routes']['direct'], $state['routes']['block']];
    $actual = ServerService::getRoutes($routeIds)->pluck('id')->all();
    $updateRules = (new App\Http\Requests\Admin\UserUpdate())->rules();
    $negativeAccepted = Validator::make(['id' => $state['users']['A'], 'speed_limit' => -1], $updateRules)->passes();
    emit(['requested_route_order' => $routeIds, 'actual_route_order' => $actual,
        'route_order_preserved' => $routeIds === $actual, 'negative_user_limit_accepted' => $negativeAccepted,
        'hidden_enabled_node_runs' => ServerService::getMachineNodes(ServerMachine::findOrFail($state['machine_id']))
            ->contains(fn($node) => !$node->show && $node->enabled)]); exit;
}

if ($action === 'observer-audit') {
    // Commit synthetic changes to exercise afterCommit, intercept publish, then
    // restore original raw records without events. Only this owned DB is used.
    $nodeId = $state['nodes']['socks']; $routeId = $state['routes']['block'];
    $cacheKey = "node_ws_alive:{$nodeId}";
    $wasCached = Cache::has($cacheKey); $oldCache = Cache::get($cacheKey);
    $originalRedis = Redis::getFacadeRoot();
    $nodeBefore = Server::findOrFail($nodeId)->getRawOriginal();
    $routeBefore = ServerRoute::findOrFail($routeId)->getRawOriginal();
    $spy = new class {
        public array $events = [];
        public function publish(string $channel, string $json): int {
            $data = json_decode($json, true, 16, JSON_THROW_ON_ERROR);
            $this->events[] = ['node_id' => $data['node_id'] ?? null,
                'event' => $data['event'] ?? null, 'transaction_level' => DB::transactionLevel()];
            return 1;
        }
    };
    try {
        Cache::put($cacheKey, true, 120);
        Redis::swap($spy);
        DB::beginTransaction();
        Server::withoutEvents(fn() => Server::findOrFail($nodeId)->update([
            'show' => false, 'enabled' => true, 'route_ids' => [$routeId]]));
        $hiddenRuns = ServerService::getMachineNodes(ServerMachine::findOrFail($state['machine_id']))->contains('id', $nodeId);
        ServerRoute::findOrFail($routeId)->update(['remarks' => $prefix . ' fixed-observer-hidden']);
        $beforeCommit = $spy->events;
        DB::commit();
        $hiddenEvents = $spy->events; $spy->events = [];
        DB::beginTransaction();
        Server::withoutEvents(fn() => Server::findOrFail($nodeId)->update(['show' => true]));
        ServerRoute::findOrFail($routeId)->update(['remarks' => $prefix . ' fixed-observer-visible']);
        DB::commit();
        $visibleEvents = $spy->events;
    } finally {
        while (DB::transactionLevel()) { DB::rollBack(); }
        DB::table('v2_server')->where('id', $nodeId)->update($nodeBefore);
        DB::table('v2_server_route')->where('id', $routeId)->update($routeBefore);
        Redis::swap($originalRedis);
        if ($wasCached) { Cache::put($cacheKey, $oldCache, 86400); }
        else { Cache::forget($cacheKey); }
    }
    emit(['hidden_enabled_node_runs' => $hiddenRuns, 'hidden_route_update_events' => $hiddenEvents,
        'visible_route_update_events' => $visibleEvents, 'before_commit_events' => $beforeCommit,
        'database_changes_restored' => true]); exit;
}

if ($action === 'public-config') {
    $config = ServerService::buildNodeConfig(Server::findOrFail($state['nodes']['vless']));
    emit(array_intersect_key($config, array_flip(['protocol', 'server_port', 'routes', 'custom_routes', 'custom_outbounds'])));
    exit;
}
if ($action !== 'inspect') { throw new RuntimeException('Unknown fixture action'); }
$nodes = [];
foreach ($state['nodes'] as $alias => $id) {
    $node = Server::findOrFail($id); $config = ServerService::buildNodeConfig($node);
    $users = ServerService::getAvailableUsers($node)->map(function ($user) use ($state): array {
        $record = (array) $user;
        return ['alias' => array_search($record['id'], $state['users'], true), 'id' => $record['id'],
            'speed_limit' => $record['speed_limit'], 'device_limit' => $record['device_limit']];
    })->values()->all();
    $routeRows = collect($config['routes'] ?? [])->map(fn($route) => [
        'id' => $route['id'], 'match' => $route['match'], 'action' => $route['action'],
        'action_value' => $route['action_value']])->values()->all();
    $nodes[$alias] = ['id' => $id, 'enabled' => $node->enabled, 'show' => $node->show,
        'protocol' => $config['protocol'] ?? '', 'port' => $config['server_port'] ?? 0,
        'requested_route_ids' => $node->route_ids, 'routes' => $routeRows,
        'custom_route_count' => count($config['custom_routes'] ?? []),
        'custom_outbound_count' => count($config['custom_outbounds'] ?? []),
        'config_sha256' => hash('sha256', json_encode($config)), 'users' => $users];
}
emit(['fixture' => $prefix, 'machine_nodes' => ServerService::getMachineNodes(
    ServerMachine::findOrFail($state['machine_id']))->pluck('id')->all(), 'nodes' => $nodes]);
