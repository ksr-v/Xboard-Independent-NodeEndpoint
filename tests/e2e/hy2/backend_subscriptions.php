<?php
declare(strict_types=1);

// Isolated functional probe. No production database, Redis, hooks or secrets.
$root = dirname(__DIR__) . '/xboard-bundled';
$loader = require 'D:/Xboard-Independent/source/Xboard/vendor/autoload.php';
$loader->setPsr4('App\\', [$root . '/app']);
foreach ($loader->getClassMap() as $class => $path) {
    if (str_starts_with($class, 'App\\')) {
        $local = $root . '/app/' . str_replace('\\', '/', substr($class, 4)) . '.php';
        if (is_file($local)) $loader->addClassMap([$class => $local]);
    }
}

use App\Http\Controllers\V2\Admin\Server\ManageController;
use App\Http\Requests\Admin\ServerSave;
use App\Models\{Server, User};
use App\Protocols\{ClashMeta, General, Loon, Shadowrocket, SingBox, Stash, Surge};
use App\Services\ServerService;
use Illuminate\Config\Repository;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\{Facade, Schema, Validator};

$app = new Application($root);
$app->instance('config', new Repository([
    'app' => ['env' => 'testing', 'key' => 'base64:' . base64_encode(str_repeat('t', 32))],
    'database' => ['default' => 'sqlite', 'connections' => ['sqlite' => [
        'driver' => 'sqlite', 'database' => ':memory:', 'prefix' => '',
    ]]],
    'cache' => ['default' => 'array', 'stores' => ['array' => ['driver' => 'array']]],
]));
Facade::setFacadeApplication($app);
foreach ([Illuminate\Filesystem\FilesystemServiceProvider::class,
    Illuminate\Database\DatabaseServiceProvider::class,
    Illuminate\Cache\CacheServiceProvider::class,
    Illuminate\Translation\TranslationServiceProvider::class,
    Illuminate\Validation\ValidationServiceProvider::class] as $provider) $app->register($provider);
$app->boot();
$app->instance(Illuminate\Contracts\Routing\ResponseFactory::class, new class extends Illuminate\Routing\ResponseFactory {
    public function __construct() {}
});
Schema::create('v2_server', function (Blueprint $t): void {
    $t->id(); $t->string('name'); $t->string('type'); $t->string('host');
    $t->string('port'); $t->string('server_port'); $t->text('protocol_settings');
    $t->text('group_ids'); $t->text('route_ids')->nullable();
    $t->boolean('enabled')->default(true); $t->boolean('show')->default(true);
    $t->integer('sort')->default(0); $t->float('rate')->default(1);
    $t->boolean('rate_time_enable')->default(false); $t->text('rate_time_ranges')->nullable();
    $t->bigInteger('transfer_enable')->nullable(); $t->bigInteger('u')->default(0); $t->bigInteger('d')->default(0);
    $t->integer('parent_id')->nullable(); $t->text('custom_outbounds')->nullable();
    $t->text('custom_routes')->nullable(); $t->text('cert_config')->nullable();
    $t->integer('created_at')->nullable(); $t->integer('updated_at')->nullable();
});

function check(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
function validation(array $payload): array {
    $request = ServerSave::create('/isolated/admin/server/save', 'POST', $payload);
    $validator = Validator::make($request->all(), $request->rules());
    return [$request, $validator];
}
function uriQuery(string $uri): array {
    parse_str(parse_url(trim($uri), PHP_URL_QUERY) ?: '', $query);
    return $query;
}
function sing(array $server, string|null $name, string|null $version): array {
    $p = new SingBox([], [], $name, $version);
    return (new ReflectionMethod(SingBox::class, 'buildHysteria'))->invoke($p, 'synthetic-hy2-password', $server);
}

$payload = [
    'type' => 'hysteria', 'name' => 'HY2 fixture', 'host' => '127.0.0.1',
    'port' => '42400-42403', 'server_port' => 42400, 'rate' => 1,
    'group_ids' => ['1'], 'route_ids' => [], 'show' => true, 'enabled' => true,
    'protocol_settings' => ['version' => 2, 'hop_interval' => 7,
        'tls' => ['server_name' => 'hy2.fixture.test', 'allow_insecure' => false],
        'bandwidth' => ['up' => 50, 'down' => 100], 'obfs' => ['open' => false]],
];
[$request, $validator] = validation($payload);
check($validator->passes(), 'Valid synthetic payload rejected.');
$request->setValidator($validator);
$response = (new ManageController())->save($request);
check($response->getStatusCode() === 200, 'Controller save failed.');
$node = Server::firstOrFail();
check($node->port === '42400-42403' && $node->protocol_settings['hop_interval'] === 7, 'Persistence changed range or interval.');
$user = new User(['group_id' => 1, 'uuid' => '00000000-0000-4000-8000-000000000007']);
$available = ServerService::getAvailableServers($user);
check(count($available) === 1, 'Synthetic subscription server absent.');
$server = $available[0];
check($server['ports'] === '42400-42403' && $server['port'] >= 42400 && $server['port'] <= 42403, 'Range conversion failed.');
$nodeConfig = ServerService::buildNodeConfig($node);
check($nodeConfig['server_port'] === 42400 && $nodeConfig['version'] === 2, 'Node configuration mismatch.');

$result = [
    'fixture' => 'In-memory SQLite; real ServerSave rules, ManageController save, Eloquent, ServerService and protocol builders.',
    'source_revision' => '2b3000781d95ae43255471688e9bb8d4220f9551',
    'source_release' => 'v2026.10.25.1',
    'synthetic_payload' => $payload,
    'persistence' => ['port' => $node->port, 'server_port' => $node->server_port, 'hop_interval' => $node->protocol_settings['hop_interval']],
    'available_server' => ['port' => $server['port'], 'ports' => $server['ports']],
    'node_api' => $nodeConfig,
    'subscriptions' => [
        'sing_box_1_11' => sing($server, 'sing-box', '1.11.0'),
        'sing_box_1_10' => sing($server, 'sing-box', '1.10.7'),
        'hiddify_2_5' => sing($server, 'hiddify', '2.5.0'),
        'sfm_1_12' => sing($server, 'sfm', '1.12.0'),
        'sing_box_unknown_version' => sing($server, 'sing-box', null),
        'clash_meta' => ClashMeta::buildHysteria('synthetic-hy2-password', $server, []),
        'stash' => Stash::buildHysteria('synthetic-hy2-password', $server),
        'general_query' => uriQuery(General::buildHysteria('synthetic-hy2-password', $server)),
        'shadowrocket_query' => uriQuery(Shadowrocket::buildHysteria('synthetic-hy2-password', $server)),
        'surge' => Surge::buildHysteria('synthetic-hy2-password', $server),
        'loon' => Loon::buildHysteria('synthetic-hy2-password', $server, []),
    ],
    'boundary_validation' => [],
];
check($result['subscriptions']['sing_box_1_11']['server_ports'] === ['42400:42403'], 'Sing-box range mismatch.');
check($result['subscriptions']['sing_box_1_11']['hop_interval'] === '7s', 'Sing-box interval mismatch.');
check($result['subscriptions']['clash_meta']['hop-interval'] === 7, 'Clash interval mismatch.');
foreach ([
    'hop_negative' => ['protocol_settings.hop_interval', -1],
    'hop_zero' => ['protocol_settings.hop_interval', 0],
    'hop_non_integer' => ['protocol_settings.hop_interval', 'bad'],
    'version_three' => ['protocol_settings.version', 3],
    'range_reversed' => ['port', '42403-42400'],
    'port_zero' => ['port', 0],
    'port_above_65535' => ['port', 70000],
    'range_mixed_list' => ['port', '42400-42402,42405'],
    'range_comma_only' => ['port', '42400,42401'],
    'listen_range' => ['server_port', '42400-42403'],
] as $name => [$path, $value]) {
    $probe = $payload;
    data_set($probe, $path, $value);
    [, $v] = validation($probe);
    $row = ['field' => $path, 'input' => $value, 'accepted' => $v->passes(), 'errors' => $v->errors()->toArray()];
    if ($v->passes()) {
        $probeNode = new Server($v->validated());
        $row['model_value'] = data_get($probeNode->toArray(), $path);
        $row['node_api_listen_port'] = ServerService::buildNodeConfig($probeNode)['server_port'];
        if ($path === 'port') {
            $copy = $server;
            $copy['port'] = str_contains((string)$value, '-') ? App\Utils\Helper::randomPort($value) : (int)$value;
            if (str_contains((string)$value, '-')) $copy['ports'] = $value; else unset($copy['ports']);
            $row['service_random_port'] = $copy['port'];
            $row['sing_box_range'] = sing($copy, 'sing-box', '1.11.0')['server_ports'] ?? null;
        }
    }
    $result['boundary_validation'][$name] = $row;
}
$result['checks'] = ['valid_controller_save' => true, 'persisted_range_and_interval' => true,
    'available_subscription_range' => true, 'single_port_node_api' => true,
    'sing_box_range_and_interval' => true, 'clash_interval' => true];
file_put_contents(__DIR__ . '/backend-subscriptions.json', json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n");
echo json_encode(['checks' => $result['checks'], 'boundary_validation' => array_map(fn ($v) => $v['accepted'], $result['boundary_validation'])], JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . "\n";
