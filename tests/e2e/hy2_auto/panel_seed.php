<?php
declare(strict_types=1);

// Dedicated SQLite fixture only. private-credentials is captured by the Root wrapper.
if (getenv('XBOARD_HY2_AUTO_PANEL_FIXTURE') !== '20261025') {
    throw new RuntimeException('Dedicated Hysteria2 fixture marker missing.');
}
chdir('/www');
require '/www/vendor/autoload.php';
$app = require '/www/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Http\Controllers\V2\Admin\Server\ManageController;
use App\Http\Requests\Admin\ServerSave;
use App\Models\{Plan, Server, ServerGroup, ServerMachine, User};
use App\Protocols\SingBox;
use App\Services\ServerService;
use App\WebSocket\NodeWorker;
use Illuminate\Support\Facades\{Cache, DB};
use Illuminate\Support\Str;

$action = $argv[1] ?? '';
$inputText = stream_get_contents(STDIN);
$input = trim($inputText) === '' ? [] : json_decode($inputText, true, 16, JSON_THROW_ON_ERROR);
if (!is_array($input) || !in_array($action, ['seed', 'private-credentials', 'get-safe-config', 'mutate-range', 'disable'], true)) {
    throw new RuntimeException('Fixture action/input differs.');
}
$connection = config('database.default');
$database = config("database.connections.{$connection}.database");
if ($connection !== 'sqlite' || !is_string($database) || $database === ':memory:' || !is_file($database)) {
    throw new RuntimeException('Dedicated SQLite database required.');
}
$prefix = 'Codex HY2 auto 20261025';
$ownerPath = storage_path('app/private/hy2-auto-fixture-owner.json');
$owner = is_file($ownerPath) ? json_decode(file_get_contents($ownerPath), true, 16, JSON_THROW_ON_ERROR) : null;
function emit(array $data): void { echo json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n"; }
function saveNode(array $payload): Server {
    global $app;
    $request = ServerSave::create('http://127.0.0.1:17006/fixture/server/save', 'POST', $payload);
    $request->setContainer($app);
    $request->setRedirector($app->make('redirect'));
    $request->validateResolved();
    $response = (new ManageController())->save($request);
    if ($response->getStatusCode() !== 200 || $response->getData(true)['data'] !== true) {
        throw new RuntimeException('Fixture server controller save failed.');
    }
    return isset($payload['id']) ? Server::findOrFail($payload['id']) : Server::orderByDesc('id')->firstOrFail();
}
function ownedState(): array {
    global $owner, $prefix, $database;
    if (!is_array($owner) || ($owner['tool'] ?? '') !== $prefix || ($owner['database'] ?? '') !== realpath($database)) {
        throw new RuntimeException('Fixture ownership differs.');
    }
    $node = Server::findOrFail($owner['node_id']);
    if ($node->name !== $prefix || $node->type !== 'hysteria' || $node->machine_id !== $owner['machine_id']) {
        throw new RuntimeException('Fixture node identity differs.');
    }
    return $owner;
}
function safeConfig(array $state): array {
    $node = Server::findOrFail($state['node_id']);
    $config = ServerService::buildNodeConfig($node);
    return ['node_id' => $node->id, 'protocol' => $config['protocol'], 'version' => $config['version'],
        'advertised_port' => $node->port, 'server_port' => $config['server_port'],
        'port_hopping' => $config['port_hopping'] ?? null,
        'hop_interval' => $node->protocol_settings['hop_interval'],
        'enabled' => $node->enabled,
        'ws_node_alive' => (bool) Cache::get('node_ws_alive:' . $node->id),
        'ws_worker_heartbeat' => Cache::has(NodeWorker::HEARTBEAT_CACHE_KEY),
        'pull_interval' => (int) admin_setting('server_pull_interval', 60)];
}
if ($action === 'seed') {
    if ($owner !== null || Server::count() !== 0 || ServerMachine::count() !== 0 || ServerGroup::count() !== 0
        || User::where('is_admin', false)->exists()) {
        throw new RuntimeException('Seed refuses a populated or already owned database.');
    }
    $state = DB::transaction(function () use ($prefix, $database): array {
        $group = new ServerGroup(); $group->name = $prefix; $group->save();
        $plan = Plan::create(['group_id' => $group->id, 'name' => $prefix, 'transfer_enable' => 1099511627776,
            'speed_limit' => 0, 'show' => false, 'renew' => false, 'sell' => false,
            'reset_traffic_method' => 2, 'prices' => ['monthly' => 1], 'device_limit' => 0]);
        $machine = ServerMachine::create(['name' => $prefix, 'token' => Str::random(32), 'is_active' => true]);
        $user = User::create(['email' => 'hy2-auto@fixture.invalid',
            'password' => password_hash(bin2hex(random_bytes(24)), PASSWORD_BCRYPT),
            'token' => Str::random(32), 'uuid' => (string) Str::uuid(), 'group_id' => $group->id,
            'plan_id' => $plan->id, 'transfer_enable' => 1099511627776, 'expired_at' => time() + 86400,
            'speed_limit' => 0, 'device_limit' => 0, 'u' => 0, 'd' => 0, 'banned' => false]);
        $node = saveNode(['name' => $prefix, 'type' => 'hysteria', 'host' => '10.213.25.1',
            'port' => '42450-42453', 'server_port' => 42443,
            'protocol_settings' => ['version' => 2, 'hop_interval' => 5,
                'tls' => ['server_name' => 'hy2.fixture.test', 'allow_insecure' => false],
                'bandwidth' => ['up' => 50, 'down' => 100], 'obfs' => ['open' => false]],
            'group_ids' => [(string) $group->id], 'route_ids' => [], 'show' => true,
            'enabled' => true, 'machine_id' => $machine->id, 'rate' => 1]);
        return ['tool' => $prefix, 'database' => realpath($database), 'group_id' => $group->id,
            'plan_id' => $plan->id, 'machine_id' => $machine->id, 'user_id' => $user->id, 'node_id' => $node->id];
    });
    admin_setting(['server_token' => Str::random(32), 'server_pull_interval' => 2,
        'server_push_interval' => 2, 'server_ws_enable' => 1, 'server_ws_url' => '']);
    if (!is_dir(dirname($ownerPath))) mkdir(dirname($ownerPath), 0700, true);
    if (file_put_contents($ownerPath, json_encode($state, JSON_THROW_ON_ERROR)) === false) {
        throw new RuntimeException('Fixture ownership could not be saved.');
    }
    chmod($ownerPath, 0600);
    emit(['seeded' => true, 'nodes' => 1, 'protocol' => 'hysteria', 'version' => 2]);
    exit;
}
$state = ownedState();
if ($action === 'private-credentials') {
    $machine = ServerMachine::findOrFail($state['machine_id']);
    $user = User::findOrFail($state['user_id']);
    $node = Server::findOrFail($state['node_id']);
    $available = ServerService::getAvailableServers($user);
    if (count($available) !== 1 || $available[0]['id'] !== $node->id) {
        throw new RuntimeException('Fixture private subscription differs.');
    }
    $protocol = new SingBox($user->toArray(), $available, 'sing-box', '1.11.0');
    $outbound = (new ReflectionMethod(SingBox::class, 'buildHysteria'))
        ->invoke($protocol, $available[0]['password'], $available[0]);
    emit(['private' => true, 'machine_id' => $machine->id, 'machine_token' => $machine->token,
        'server_token' => admin_setting('server_token'), 'node_id' => $node->id,
        'user' => ['id' => $user->id, 'uuid' => $user->uuid, 'speed_limit' => $user->speed_limit, 'device_limit' => $user->device_limit],
        'node' => ServerService::buildNodeConfig($node), 'client_outbound' => $outbound]);
    exit;
}
if ($action === 'mutate-range' || $action === 'disable') {
    $node = Server::findOrFail($state['node_id']);
    if ($action === 'mutate-range') {
        $start = $input['start'] ?? null; $end = $input['end'] ?? null;
        if (!is_int($start) || !is_int($end) || $start < 1024 || $start > $end || $end > 65535) {
            throw new RuntimeException('Fixture range must be ascending integer UDP ports 1024..65535.');
        }
        $port = $start . '-' . $end;
    } else {
        // Disable hopping, retaining the node itself and its current listener.
        $port = (string) $node->server_port;
    }
    $save = ['id' => $node->id, 'name' => $node->name, 'type' => $node->type, 'host' => $node->host,
        'port' => $port, 'server_port' => $node->server_port, 'protocol_settings' => $node->protocol_settings,
        'group_ids' => $node->group_ids, 'route_ids' => $node->route_ids, 'show' => $node->show,
        'enabled' => $node->enabled, 'machine_id' => $node->machine_id, 'rate' => $node->rate];
    $began = microtime(true);
    DB::transaction(fn () => saveNode($save));
    emit(['changed' => true, 'action' => $action, 'save_ms' => round((microtime(true) - $began) * 1000, 3),
        'config' => safeConfig($state)]);
    exit;
}
emit(safeConfig($state));
