<?php
/** Isolated real Laravel/Admin/Sanctum/SQLite tests; Root transport and effects are synthetic. */
declare(strict_types=1);

$project = dirname(__DIR__, 2);
$autoload = getenv('NE_TEST_AUTOLOAD') ?: __DIR__ . '/vendor/autoload.php';
$xboard = getenv('NE_TEST_XBOARD_PATH') ?: $project . '/.audit/Xboard';
if (!is_file($autoload) || !is_file($xboard . '/app/Http/Middleware/Admin.php')) {
    throw new RuntimeException('Supply the integration Composer autoload and audited Xboard source through NE_TEST_AUTOLOAD and NE_TEST_XBOARD_PATH.');
}
require $autoload;
spl_autoload_register(function (string $class) use ($project, $xboard): void {
    foreach (['App\\' => $xboard . '/app/', 'Plugin\\NodeEndpoint\\' => $project . '/plugin/NodeEndpoint/'] as $prefix => $base) {
        if (str_starts_with($class, $prefix)) {
            $file = $base . str_replace('\\', '/', substr($class, strlen($prefix))) . '.php';
            if (is_file($file)) { require_once $file; }
        }
    }
}, true, true);

use Illuminate\Config\Repository;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\{Auth, Cache, DB, Route, Schema};
use Plugin\NodeEndpoint\Services\{Certificates, Endpoint, Settings, WebManager, WebManagerException};

function admin_setting($key, $default = null) { return $key === 'app_url' ? 'https://panel.fixture.invalid' : $default; }
final class WebFixtureApplication extends Application { public function runningInConsole() { return false; } }
final class WebFixtureKernel extends Illuminate\Foundation\Http\Kernel
{
    protected $bootstrappers = [];
    protected $middleware = [];
    protected $middlewareAliases = ['admin' => App\Http\Middleware\Admin::class, 'throttle' => Illuminate\Routing\Middleware\ThrottleRequests::class];
}
final class WebFixtureUser extends Illuminate\Foundation\Auth\User
{
    use Laravel\Sanctum\HasApiTokens;
    protected $table = 'users';
    protected $guarded = [];
    protected $casts = ['is_admin' => 'boolean'];
}

$fixture = __DIR__ . '/work/web-manager-' . gmdate('Ymd-His') . '-' . bin2hex(random_bytes(3));
mkdir($fixture . '/storage/app/node_endpoint', 0700, true);
mkdir($fixture . '/bootstrap/cache', 0700, true);
mkdir($fixture . '/storage/framework/views', 0700, true);
touch($fixture . '/database.sqlite');
$app = new WebFixtureApplication($fixture);
$app->useStoragePath($fixture . '/storage');
$app->instance('config', new Repository([
    'app' => ['name' => 'Root web manager fixture', 'env' => 'testing', 'debug' => false, 'timezone' => 'UTC', 'url' => 'https://panel.fixture.invalid'],
    'database' => ['default' => 'sqlite', 'connections' => ['sqlite' => ['driver' => 'sqlite', 'database' => $fixture . '/database.sqlite', 'prefix' => '']]],
    'cache' => ['default' => 'array', 'stores' => ['array' => ['driver' => 'array', 'serialize' => false]]],
    'auth' => ['defaults' => ['guard' => 'sanctum'], 'guards' => ['sanctum' => ['driver' => 'sanctum', 'provider' => 'users']],
        'providers' => ['users' => ['driver' => 'eloquent', 'model' => WebFixtureUser::class]]],
    'sanctum' => ['guard' => [], 'expiration' => null, 'routes' => false],
    'logging' => ['default' => 'single', 'channels' => ['single' => ['driver' => 'single', 'path' => $fixture . '/storage/fixture.log']]],
    'view' => ['paths' => [], 'compiled' => $fixture . '/storage/framework/views'],
]));
Illuminate\Support\Facades\Facade::setFacadeApplication($app);
$app->instance('request', Illuminate\Http\Request::create('https://panel.fixture.invalid/'));
$app->singleton(Illuminate\Contracts\Debug\ExceptionHandler::class, Illuminate\Foundation\Exceptions\Handler::class);
$app->singleton(Illuminate\Contracts\Http\Kernel::class, WebFixtureKernel::class);
foreach ([Illuminate\Filesystem\FilesystemServiceProvider::class, Illuminate\Foundation\Providers\FoundationServiceProvider::class,
    Illuminate\View\ViewServiceProvider::class, Illuminate\Database\DatabaseServiceProvider::class, Illuminate\Cache\CacheServiceProvider::class,
    Illuminate\Auth\AuthServiceProvider::class, Illuminate\Hashing\HashServiceProvider::class,
    Laravel\Sanctum\SanctumServiceProvider::class] as $provider) { $app->register($provider); }
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
$app->boot();
Schema::create('v2_plugins', function (Blueprint $t): void { $t->id(); $t->string('code'); $t->boolean('is_enabled'); });
Schema::create('node_endpoint_settings', function (Blueprint $t): void { $t->id(); $t->text('settings'); $t->timestamps(); });
$jobMigration = require $project . '/plugin/NodeEndpoint/database/migrations/2026_10_29_000003_create_node_endpoint_web_jobs.php';
$jobMigration->up();
$jobMigration->up();
Schema::create('users', function (Blueprint $t): void { $t->id(); $t->string('name'); $t->boolean('is_admin'); $t->timestamps(); });
Schema::create('personal_access_tokens', function (Blueprint $t): void {
    $t->id(); $t->morphs('tokenable'); $t->string('name'); $t->string('token', 64)->unique(); $t->text('abilities')->nullable();
    $t->timestamp('last_used_at')->nullable(); $t->timestamp('expires_at')->nullable(); $t->timestamps();
});
DB::table('v2_plugins')->insert(['code' => 'node_endpoint', 'is_enabled' => true]);
$admin = WebFixtureUser::create(['name' => 'First administrator', 'is_admin' => true]);
$secondAdmin = WebFixtureUser::create(['name' => 'Second administrator', 'is_admin' => true]);
$user = WebFixtureUser::create(['name' => 'Ordinary user', 'is_admin' => false]);
$adminToken = $admin->createToken('fixture-admin')->plainTextToken;
$secondToken = $secondAdmin->createToken('fixture-second')->plainTextToken;
$userToken = $user->createToken('fixture-user')->plainTextToken;
require $project . '/plugin/NodeEndpoint/routes/api.php';

class FixtureWebManager extends WebManager
{
    public array $calls = [];
    public array $transactionLevels = [];
    public array $status;
    public array $plans = [];
    public array $jobs = [];
    public ?string $failure = null;
    public bool $loseExecuteResponse = false;
    public $alterPlan = null;
    public $alterJob = null;
    public function request(string $operation, array $parameters = []): array
    {
        $this->calls[] = [$operation, $parameters];
        $this->transactionLevels[] = DB::connection()->transactionLevel();
        if (DB::connection()->transactionLevel() !== 0) { throw new RuntimeException('Root RPC crossed an application database transaction.'); }
        if ($this->failure) { throw self::failure($this->failure, 503); }
        if ($operation === 'status') { return $this->status; }
        if ($operation === 'plan') {
            $id = bin2hex(random_bytes(16));
            $target = ['host' => $parameters['host'] ?? '192.0.2.20', 'port' => $parameters['port'] ?? 9443,
                'https' => true, 'ws_mode' => $parameters['ws_mode'] ?? 'rewrite'];
            $plan = ['id' => $id, 'action' => $parameters['action'], 'digest' => hash('sha256', $id),
                'expires_at' => gmdate('Y-m-d\TH:i:s\Z', time() + 240), 'confirmation' => 'APPLY ' . $target['host'] . ':' . $target['port'],
                'target' => $target, 'stages' => ['entry', 'certificate', 'firewall', 'ca_sync'], 'current' => null];
            if ($this->alterPlan) { $plan = ($this->alterPlan)($plan); }
            $this->plans[$id] = $plan;
            return $plan;
        }
        if ($operation === 'execute') {
            $id = $parameters['plan_id'];
            $this->jobs[$id] = ['id' => $id, 'state' => 'succeeded', 'action' => $this->plans[$parameters['plan_id']]['action'],
                'stages' => [['name' => 'entry', 'state' => 'succeeded'], ['name' => 'ca_sync', 'state' => 'succeeded']],
                'result' => ['endpoint' => $this->plans[$parameters['plan_id']]['target'], 'ca_preserved' => true]];
            $this->status['active_job'] = $id;
            if ($this->loseExecuteResponse) { $this->loseExecuteResponse = false; throw self::failure('helper_timeout', 504); }
            return ['id' => $id, 'state' => 'queued'];
        }
        if (!isset($this->jobs[$parameters['id']])) { throw self::failure('not_found', 404); }
        $job = $this->jobs[$parameters['id']];
        return $this->alterJob ? ($this->alterJob)($job) : $job;
    }
}
$manager = new FixtureWebManager();
$manager->status = ['available' => true, 'version' => '1.0.0',
    'policy' => ['app' => '/opt/fixture-panel', 'app_user' => 'www-data', 'allowed_ports' => [8443, 9443]],
    'root_status' => ['schema_version' => 1, 'version' => '1.0.0', 'installed' => true,
        'endpoint' => ['host' => '192.0.2.20', 'port' => 9443, 'https' => true]], 'ca' => null, 'active_job' => null];
$app->instance(WebManager::class, $manager);
Settings::write(array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => '192.0.2.20', 'port' => 9443]));
$checks = 0;

function eq($actual, $expected, string $label = ''): void
{
    if ($actual !== $expected) { throw new RuntimeException($label . ' expected ' . json_encode($expected) . ', received ' . json_encode($actual)); }
}
function ok(bool $actual, string $label): void { if (!$actual) { throw new RuntimeException($label); } }
function check(string $label, callable $body): void
{
    global $checks;
    Cache::flush();
    $body(); ++$checks;
    echo 'PASS ' . $label . PHP_EOL;
}
function req(string $suffix, string $method = 'GET', array $data = [], ?string $token = 'admin', array $server = [], string $scheme = 'https')
{
    global $app, $kernel, $adminToken, $secondToken, $userToken;
    $token = ['admin' => $adminToken, 'second' => $secondToken, 'user' => $userToken][$token] ?? $token;
    $app->forgetScopedInstances(); Auth::forgetGuards();
    $server = array_replace(['HTTP_ACCEPT' => 'application/json', 'REMOTE_ADDR' => '192.0.2.44',
        'HTTP_ORIGIN' => 'https://panel.fixture.invalid'], $server);
    if ($token !== null) { $server['HTTP_AUTHORIZATION'] = 'Bearer ' . $token; }
    $request = Illuminate\Http\Request::create($scheme . '://panel.fixture.invalid/api/v1/node-endpoint/root/' . $suffix,
        $method, $data, [], [], $server);
    $response = $kernel->handle($request); $kernel->terminate($request, $response);
    return $response;
}
function data($response): array
{
    eq($response->getStatusCode(), 200, $response->getContent());
    ok(str_contains($response->headers->get('Cache-Control'), 'no-store'), 'Root response was cacheable.');
    return json_decode($response->getContent(), true, 32, JSON_THROW_ON_ERROR)['data'];
}
function error($response, string $code, int $status): void
{
    eq($response->getStatusCode(), $status, $response->getContent());
    eq(json_decode($response->getContent(), true, 32, JSON_THROW_ON_ERROR)['error']['code'], $code);
    ok(!str_contains($response->getContent(), 'fixture-secret-canary'), 'An error exposed internal details.');
}
function makePlan(string $action = 'configure'): array { return data(req('plan', 'POST', ['action' => $action])); }
function executePlan(array $plan, ?string $token = 'admin')
{
    return req('execute', 'POST', ['plan_id' => $plan['id'], 'digest' => $plan['digest'], 'confirmation' => $plan['confirmation']], $token);
}

check('all Root routes require real native administrator Bearer authentication', function (): void {
    global $manager;
    $before = count($manager->calls);
    foreach ([null, 'user', 'invalid-token'] as $token) {
        foreach ([['status', 'GET', []], ['plan', 'POST', ['action' => 'configure']],
            ['execute', 'POST', []], ['jobs/' . str_repeat('b', 32), 'GET', []], ['sync-ca', 'POST', []]] as [$path, $method, $body]) {
            eq(req($path, $method, $body, $token)->getStatusCode(), 403);
        }
    }
    eq(count($manager->calls), $before, 'Unauthorized request contacted Root.');
});
check('the real job ownership migration is idempotent and stores only safe identifiers and states', function (): void {
    global $jobMigration;
    eq(Schema::getColumnListing('node_endpoint_web_jobs'), ['id', 'job_id', 'admin_id', 'action', 'state', 'created_at', 'updated_at']);
    $indexes = Schema::getIndexes('node_endpoint_web_jobs');
    ok(count(array_filter($indexes, fn ($index) => $index['columns'] === ['job_id'] && $index['unique'])) === 1, 'Job ID is not unique.');
    ok(count(array_filter($indexes, fn ($index) => $index['columns'] === ['admin_id'])) === 1, 'Administrator ownership is not indexed.');
    $jobMigration->down();
    ok(Schema::hasTable('node_endpoint_web_jobs'), 'Plugin rollback removed durable recovery ownership.');
});
check('status is read only and an absent helper offers SSH guidance', function (): void {
    global $manager;
    $before = Settings::read(); $start = count($manager->calls);
    eq(data(req('status'))['available'], true);
    eq(array_slice($manager->calls, $start), [['status', []]]);
    eq(Settings::read(), $before);
    $manager->failure = 'helper_unavailable';
    try { $missing = data(req('status')); eq($missing['available'], false); ok($missing['guidance'] !== '', 'No setup guidance.'); }
    finally { $manager->failure = null; }
});
check('the controller rejects cookie-derived Sanctum users and revoked administrator rights', function (): void {
    global $admin, $manager;
    $calls = count($manager->calls);
    Auth::forgetGuards();
    Auth::guard('sanctum')->setUser($admin->fresh()->withAccessToken(new Laravel\Sanctum\TransientToken()));
    $request = Illuminate\Http\Request::create('https://panel.fixture.invalid/api/v1/node-endpoint/root/status', 'GET', [], [], [], ['HTTP_AUTHORIZATION' => 'Bearer arbitrary']);
    error(app(Plugin\NodeEndpoint\Controllers\WebManagerController::class)->status($request), 'unauthorized', 403);
    Auth::forgetGuards();
    DB::table('users')->where('id', $admin->id)->update(['is_admin' => false]);
    try { eq(req('status')->getStatusCode(), 403); }
    finally { DB::table('users')->where('id', $admin->id)->update(['is_admin' => true]); }
    eq(count($manager->calls), $calls);
});
check('mutations require HTTPS and exact same Origin before contacting Root', function (): void {
    global $manager;
    $before = count($manager->calls);
    foreach (['plan', 'execute', 'sync-ca'] as $path) {
        foreach ([['HTTP_ORIGIN' => 'https://evil.fixture.invalid'], ['HTTP_ORIGIN' => 'null'], ['HTTP_ORIGIN' => ''],
            ['HTTP_ORIGIN' => 'https://panel.fixture.invalid.evil'], ['HTTP_ORIGIN' => 'https://panel.fixture.invalid/']] as $server) {
            error(req($path, 'POST', [], 'admin', $server), 'secure_origin_required', 403);
            Cache::flush();
        }
        error(req($path, 'POST', [], 'admin', ['HTTP_ORIGIN' => 'http://panel.fixture.invalid'], 'http'), 'secure_origin_required', 403);
        Cache::flush();
    }
    eq(count($manager->calls), $before);
});
check('plans accept only action and derive every configure parameter from saved settings', function (): void {
    global $manager;
    foreach ([['action' => 'configure', 'host' => 'evil.fixture.invalid'], ['action' => 'configure', 'command' => 'touch /tmp/never'],
        ['action' => 'configure', 'path' => '/etc/shadow'], ['action' => 'shell']] as $input) {
        error(req('plan', 'POST', $input), 'invalid_request', 422);
    }
    $plan = makePlan();
    eq(end($manager->calls), ['plan', ['action' => 'configure', 'host' => '192.0.2.20', 'port' => 9443, 'ws_mode' => 'rewrite']]);
    eq($plan['target'], ['host' => '192.0.2.20', 'port' => 9443, 'https' => true, 'ws_mode' => 'rewrite']);
    foreach (['refresh', 'renew', 'firewall'] as $action) { makePlan($action); eq(end($manager->calls), ['plan', ['action' => $action]]); }
});
check('HTTP disabled and invalid saved settings cannot create plans', function (): void {
    $before = Settings::read();
    try {
        foreach ([['enabled' => false], ['https' => false, 'http_acknowledged' => true], ['port' => 65536], ['host' => '']] as $change) {
            Settings::write(array_replace($before, $change));
            error(req('plan', 'POST', ['action' => 'configure']), 'invalid_settings', 422);
        }
    } finally { Settings::write($before); }
});
check('plans for an installed entry explicitly preserve the current Root CA', function (): void {
    global $manager;
    $manager->alterPlan = function ($plan) use ($manager): array {
        $plan['current'] = $manager->status['root_status'];
        return $plan;
    };
    try { eq(makePlan()['ca_preserved'], true, 'The plan left existing CA preservation ambiguous.'); }
    finally { $manager->alterPlan = null; }
    ok(!array_key_exists('ca_preserved', makePlan()), 'A plan without an installed current entry asserted CA preservation.');
});
check('plans validate Root target mode expiry digest and confirmation', function (): void {
    global $manager;
    foreach ([['target' => ['host' => '192.0.2.21', 'port' => 9443, 'https' => true, 'ws_mode' => 'rewrite']],
        ['expires_at' => gmdate('Y-m-d\TH:i:s\Z', time() - 1)], ['expires_at' => '2099-02-30T00:00:00Z'],
        ['expires_at' => gmdate('Y-m-d\TH:i:s\Z', time() + 3600)], ['digest' => 'not-a-digest'], ['confirmation' => "APPLY\ncommand"]] as $change) {
        $manager->alterPlan = fn ($plan) => array_replace($plan, $change);
        error(req('plan', 'POST', ['action' => 'configure']), isset($change['target']) ? 'target_mismatch' : 'helper_protocol', isset($change['target']) ? 409 : 502);
    }
    $manager->alterPlan = null;
});
check('a settings change during the unlocked Root plan is rejected before confirmation becomes usable', function (): void {
    global $manager;
    $saved = Settings::read();
    $manager->alterPlan = function ($plan) use ($saved): array {
        Settings::write(array_replace($saved, ['port' => 8443]));
        return $plan;
    };
    try {
        error(req('plan', 'POST', ['action' => 'configure']), 'settings_changed', 409);
        $orphan = end($manager->plans);
        error(executePlan($orphan), 'not_found', 404);
    } finally { $manager->alterPlan = null; Settings::write($saved); }
});
check('administrator ownership typed confirmation settings revision and idempotency gate execute', function (): void {
    global $manager;
    $plan = makePlan(); $calls = count($manager->calls);
    error(executePlan($plan, 'second'), 'not_found', 404);
    error(executePlan(array_replace($plan, ['confirmation' => 'wrong'])), 'confirmation_mismatch', 422);
    error(executePlan(array_replace($plan, ['digest' => str_repeat('f', 64)])), 'plan_mismatch', 409);
    eq(count($manager->calls), $calls);
    $saved = Settings::read();
    Settings::write(array_replace($saved, ['port' => 8443]));
    error(executePlan($plan), 'settings_changed', 409);
    Settings::write($saved);
    $job = data(executePlan($plan));
    eq(data(executePlan($plan)), $job);
    eq(count($manager->calls), $calls + 1, 'Duplicate request executed Root twice.');
    eq(data(req('jobs/' . $job['id']))['state'], 'succeeded');
    error(req('jobs/' . $job['id'], 'GET', [], 'second'), 'not_found', 404);
    eq(data(req('status', 'GET', [], 'second'))['active_job'], null);
    eq(data(req('status'))['active_job'], $job['id']);
});
check('job output allows public fields and suppresses raw Root diagnostics', function (): void {
    global $manager;
    $job = data(executePlan(makePlan()));
    $manager->alterJob = function ($job): array {
        $job['state'] = 'partial'; $job['result']['private_path'] = '/root/fixture-secret-canary';
        $job['error'] = ['code' => 'unexpected', 'message' => 'fixture-secret-canary'];
        $job['stages'][] = ['name' => 'firewall', 'state' => 'failed', 'code' => 'policy_denied', 'message' => 'fixture-secret-canary'];
        return $job;
    };
    try { $response = req('jobs/' . $job['id']); eq(data($response)['state'], 'partial'); ok(!str_contains($response->getContent(), 'fixture-secret-canary'), 'Raw helper output leaked.'); }
    finally { $manager->alterJob = null; }
});
check('an uncertain execute response retains owner recovery and idempotent retry', function (): void {
    global $manager;
    $plan = makePlan(); $manager->loseExecuteResponse = true;
    error(executePlan($plan), 'helper_timeout', 504);
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', $plan['id'])->value('state'), 'submitted', 'Unknown response rolled back durable ownership.');
    eq(data(req('status'))['active_job'], $plan['id']);
    eq(data(req('status', 'GET', [], 'second'))['active_job'], null);
    eq(data(req('jobs/' . $plan['id']))['state'], 'succeeded');
    eq(data(executePlan($plan))['id'], $plan['id']);
});
check('durable job recovery survives cache loss five-minute expiry and a Root restart', function (): void {
    global $manager;
    $job = data(executePlan(makePlan()));
    $future = now()->addSeconds(601);
    Cache::flush(); Illuminate\Support\Carbon::setTestNow($future);
    try {
        eq(data(req('status'))['active_job'], $job['id'], 'Cache loss or plan expiry hid an owned Root job.');
        $manager->status['active_job'] = null;
        $manager->jobs[$job['id']]['state'] = 'interrupted';
        $manager->jobs[$job['id']]['stages'] = [['name' => 'entry', 'state' => 'interrupted']];
        $manager->jobs[$job['id']]['error'] = ['code' => 'interrupted', 'message' => 'Synthetic helper restart'];
        $status = data(req('status'));
        eq($status['active_job'], null, 'A terminal job was incorrectly presented as active.');
        eq($status['last_job'], $job['id'], 'Root restart lost the last owned durable job.');
        eq(data(req('jobs/' . $status['last_job']))['state'], 'interrupted');
        eq(DB::table('node_endpoint_web_jobs')->where('job_id', $job['id'])->value('state'), 'interrupted');
        $other = data(req('status', 'GET', [], 'second'));
        eq($other['active_job'], null); eq($other['last_job'], null);
        error(req('jobs/' . $job['id'], 'GET', [], 'second'), 'not_found', 404);
    } finally { Illuminate\Support\Carbon::setTestNow(); }
});
check('durable ownership must commit before any Root execution is attempted', function (): void {
    global $manager;
    $plan = makePlan(); $calls = count($manager->calls);
    DB::statement("CREATE TRIGGER reject_web_job_owner BEFORE INSERT ON node_endpoint_web_jobs BEGIN SELECT RAISE(ABORT, 'fixture-secret-canary'); END");
    try { error(executePlan($plan), 'helper_operation_failed', 503); }
    finally { DB::statement('DROP TRIGGER reject_web_job_owner'); }
    eq(count($manager->calls), $calls, 'Root executed before ownership was durably stored.');
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', $plan['id'])->exists(), false);
});
check('a missing Root job keeps its owner but leaves recovery suggestions until it appears again', function (): void {
    global $manager;
    $job = data(executePlan(makePlan())); $saved = $manager->jobs[$job['id']];
    unset($manager->jobs[$job['id']]); $manager->status['active_job'] = null;
    eq(data(req('status'))['last_job'], $job['id']);
    error(req('jobs/' . $job['id']), 'not_found', 404);
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', $job['id'])->value('state'), 'missing');
    $status = data(req('status'));
    eq($status['available'], true);
    ok($status['last_job'] !== $job['id'], 'A missing Root job remained the suggested last job.');
    $manager->jobs[$job['id']] = $saved;
    $manager->status['active_job'] = $job['id'];
    Cache::flush();
    eq(data(req('status'))['active_job'], $job['id'], 'A late Root job lost its durable owner association.');
    eq(data(req('jobs/' . $job['id']))['state'], 'succeeded');
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', $job['id'])->value('state'), 'succeeded');
    eq(data(req('status'))['last_job'], $job['id']);
    $other = data(req('status', 'GET', [], 'second'));
    eq($other['active_job'], null); eq($other['last_job'], null);
    error(req('jobs/' . $job['id'], 'GET', [], 'second'), 'not_found', 404);
});
check('durable ownership retention is bounded to thirty days and one hundred jobs per administrator', function (): void {
    global $admin, $secondAdmin;
    $rows = [];
    for ($index = 0; $index < 110; ++$index) {
        $rows[] = ['job_id' => 'retention_' . str_pad((string) $index, 20, '0', STR_PAD_LEFT), 'admin_id' => $admin->id,
            'action' => 'refresh', 'state' => 'succeeded', 'created_at' => now(), 'updated_at' => now()];
    }
    $rows[] = ['job_id' => 'retention_expired_first', 'admin_id' => $admin->id, 'action' => 'refresh', 'state' => 'succeeded',
        'created_at' => now()->subDays(31), 'updated_at' => now()->subDays(31)];
    $rows[] = ['job_id' => 'retention_expired_other', 'admin_id' => $secondAdmin->id, 'action' => 'refresh', 'state' => 'succeeded',
        'created_at' => now()->subDays(31), 'updated_at' => now()->subDays(31)];
    DB::table('node_endpoint_web_jobs')->insert($rows);
    data(req('status'));
    eq(DB::table('node_endpoint_web_jobs')->where('admin_id', $admin->id)->count(), 100);
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', 'retention_expired_first')->exists(), false);
    eq(DB::table('node_endpoint_web_jobs')->where('job_id', 'retention_expired_other')->exists(), true, 'Retention changed another administrator\'s records.');
    DB::table('node_endpoint_web_jobs')->where('job_id', 'like', 'retention_%')->delete();
});
check('CA synchronization trusts only Root status and checks the saved target', function (): void {
    global $manager;
    error(req('sync-ca', 'POST', ['pem' => 'client-upload', 'fingerprints' => []]), 'invalid_request', 422);
    $before = $manager->status['root_status'];
    $manager->status['root_status']['endpoint']['host'] = '192.0.2.21';
    try { error(req('sync-ca', 'POST'), 'target_mismatch', 409); }
    finally { $manager->status['root_status'] = $before; }
    error(req('sync-ca', 'POST'), 'ca_unavailable', 422);
});
check('CA synchronization verifies real public certificates and atomically updates local trust', function (): void {
    global $manager, $fixture;
    $config = $fixture . '/ca.cnf';
    file_put_contents($config, "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=fixture\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n");
    $key = openssl_pkey_new(['private_key_type' => OPENSSL_KEYTYPE_EC, 'private_key_bits' => 2048, 'curve_name' => 'prime256v1', 'config' => $config]);
    $csr = openssl_csr_new(['commonName' => 'Synthetic Root web manager CA'], $key, ['config' => $config, 'digest_alg' => 'sha256']);
    $certificate = openssl_csr_sign($csr, null, $key, 365, ['config' => $config, 'x509_extensions' => 'v3_ca', 'digest_alg' => 'sha256']);
    ok(openssl_x509_export($certificate, $pem), 'Synthetic CA generation failed.');
    $fingerprint = openssl_x509_fingerprint($pem, 'sha256');
    $manager->status['ca'] = ['pem' => $pem, 'fingerprints' => [$fingerprint]];
    $manager->status['root_status']['ca'] = ['sha256' => $fingerprint];
    $result = data(req('sync-ca', 'POST'));
    eq($result['certificates'][0]['fingerprint_sha256'], $fingerprint);
    eq(file_get_contents(Settings::caPath()), Certificates::import($pem, [$fingerprint])['pem']);
    eq(Settings::read()['root_status'], $manager->status['root_status']);
    $savedPem = file_get_contents(Settings::caPath()); $saved = Settings::read();
    $manager->status['ca']['fingerprints'] = [str_repeat('0', 64)];
    error(req('sync-ca', 'POST'), 'ca_unavailable', 422);
    eq(file_get_contents(Settings::caPath()), $savedPem); eq(Settings::read(), $saved);
    $replacement = openssl_csr_sign($csr, null, $key, 365,
        ['config' => $config, 'x509_extensions' => 'v3_ca', 'digest_alg' => 'sha256'], 2);
    ok(openssl_x509_export($replacement, $replacementPem), 'Synthetic replacement CA generation failed.');
    $replacementFingerprint = openssl_x509_fingerprint($replacementPem, 'sha256');
    ok($replacementFingerprint !== $fingerprint, 'Replacement fixture used the original CA.');
    $manager->status['ca'] = ['pem' => $replacementPem, 'fingerprints' => [$replacementFingerprint]];
    $manager->status['root_status']['ca'] = ['sha256' => $replacementFingerprint];
    DB::statement("CREATE TRIGGER reject_web_ca_update BEFORE UPDATE ON node_endpoint_settings BEGIN SELECT RAISE(ABORT, 'fixture-secret-canary'); END");
    try { error(req('sync-ca', 'POST'), 'storage_failed', 503); eq(file_get_contents(Settings::caPath()), $savedPem); eq(Settings::read(), $saved); }
    finally { DB::statement('DROP TRIGGER reject_web_ca_update'); }
    DB::statement('PRAGMA foreign_keys = ON');
    DB::statement('CREATE TABLE fixture_ca_parent (id INTEGER PRIMARY KEY)');
    DB::statement('CREATE TABLE fixture_ca_child (parent_id INTEGER REFERENCES fixture_ca_parent(id) DEFERRABLE INITIALLY DEFERRED)');
    DB::statement('CREATE TRIGGER reject_web_ca_commit AFTER UPDATE ON node_endpoint_settings BEGIN INSERT INTO fixture_ca_child(parent_id) VALUES(999); END');
    try {
        error(req('sync-ca', 'POST'), 'storage_failed', 503);
        eq(file_get_contents(Settings::caPath()), $savedPem, 'Commit failure did not restore the previous CA.');
        eq(Settings::read(), $saved, 'Commit failure did not restore the previous public status.');
        eq(DB::connection()->getPdo()->inTransaction(), false, 'Commit failure left a transaction open.');
    } finally { DB::statement('DROP TRIGGER reject_web_ca_commit'); }
});
check('public status rejects private material instead of forwarding it', function (): void {
    global $manager;
    $original = $manager->status['root_status'];
    $manager->status['root_status']['ca'] = ['password' => 'fixture-secret-canary'];
    try { error(req('status'), 'helper_protocol', 502); }
    finally { $manager->status['root_status'] = $original; }
});
check('plan execute status job and CA-sync RPCs never hold the application row lock', function (): void {
    global $manager;
    ok(count($manager->transactionLevels) > 0, 'The RPC boundary was not exercised.');
    eq(array_values(array_unique($manager->transactionLevels)), [0], 'A Root RPC held an application database transaction.');
});
check('plugin disable gates every Root operation without touching the helper', function (): void {
    global $manager;
    $calls = count($manager->calls); DB::table('v2_plugins')->update(['is_enabled' => false]);
    try { foreach (['status', 'plan', 'execute', 'sync-ca'] as $path) { eq(req($path, $path === 'status' ? 'GET' : 'POST')->getStatusCode(), 404); } }
    finally { DB::table('v2_plugins')->update(['is_enabled' => true]); }
    eq(count($manager->calls), $calls);
});
check('Root operations retain per-administrator throttling', function (): void {
    for ($index = 0; $index < 10; ++$index) { eq(req('plan', 'POST', ['action' => 'configure'])->getStatusCode(), 200); }
    eq(req('plan', 'POST', ['action' => 'configure'])->getStatusCode(), 429);
});
check('a minute of task polling does not consume the settings or mutation budget', function (): void {
    $job = data(executePlan(makePlan()));
    for ($index = 0; $index < 42; ++$index) { eq(req('jobs/' . $job['id'])->getStatusCode(), 200); }
    eq(req('plan', 'POST', ['action' => 'refresh'])->getStatusCode(), 200);
    eq(req('status')->getStatusCode(), 200);
});

/** Test the production framing/permissions code with an isolated loopback stream, never /run. */
class StreamFixtureWebManager extends WebManager
{
    public array $stats;
    public $client;
    public $peer;
    public bool $connected = false;
    public function __construct(string $response)
    {
        $directory = ['mode' => 0040755, 'uid' => 0, 'dev' => 1, 'ino' => 1];
        $this->stats = ['/' => $directory, '/run' => $directory, '/run/nodeendpoint-web-manager' => array_replace($directory, ['mode' => 0040750]),
            self::SOCKET => ['mode' => 0140660, 'uid' => 0, 'dev' => 1, 'ino' => 4]];
        $server = stream_socket_server('tcp://127.0.0.1:0', $errno, $error);
        if (!$server) { throw new RuntimeException('Loopback fixture server failed.'); }
        $this->client = stream_socket_client('tcp://' . stream_socket_get_name($server, false));
        $this->peer = stream_socket_accept($server); fclose($server);
        fwrite($this->peer, $response);
    }
    protected function metadata(string $path): array|false { return $this->stats[$path] ?? false; }
    protected function connect() { $this->connected = true; return $this->client; }
    public function close(): void
    {
        foreach ([$this->client, $this->peer] as $stream) { if (is_resource($stream)) { fclose($stream); } }
    }
}
function transportError(StreamFixtureWebManager $client, string $code, ?int $status = null): void
{
    try { $client->request('status'); }
    catch (WebManagerException $error) { eq($error->errorCode, $code); if ($status !== null) { eq($error->httpStatus, $status); } return; }
    finally { $client->close(); }
    throw new RuntimeException('Unsafe transport unexpectedly succeeded.');
}
check('socket transport rejects symlinks foreign ownership and writable ancestors before connect', function (): void {
    foreach (['/', '/run', '/run/nodeendpoint-web-manager', WebManager::SOCKET] as $path) {
        foreach (['symlink', 'foreign', 'writable'] as $case) {
            $client = new StreamFixtureWebManager("{\"ok\":true,\"data\":{}}\n");
            if ($case === 'symlink') { $client->stats[$path]['mode'] = 0120777; }
            elseif ($case === 'foreign') { $client->stats[$path]['uid'] = 1000; }
            elseif ($path === WebManager::SOCKET) { $client->stats[$path]['mode'] = 0140666; }
            else { $client->stats[$path]['mode'] |= 0020; }
            transportError($client, 'helper_unsafe'); eq($client->connected, false);
        }
    }
    $client = new StreamFixtureWebManager("{}\n"); unset($client->stats[WebManager::SOCKET]);
    transportError($client, 'helper_unavailable');
});
check('socket transport uses schema 1 line JSON and bounded safe response framing', function (): void {
    $client = new StreamFixtureWebManager("{\"ok\":true,\"data\":{\"available\":true}}\n");
    try {
        eq($client->request('status'), ['available' => true]);
        stream_set_timeout($client->peer, 2);
        eq(json_decode(fgets($client->peer), true), ['schema' => 1, 'operation' => 'status', 'parameters' => []]);
    } finally { $client->close(); }
    foreach (["{broken}\n", "{\"ok\":true,\"data\":[]}\nextra\n", str_repeat('x', 131073) . "\n"] as $wire) {
        transportError(new StreamFixtureWebManager($wire), 'helper_protocol');
    }
    $client = new StreamFixtureWebManager("{\"ok\":false,\"error\":{\"code\":\"unexpected\",\"message\":\"fixture-secret-canary\"}}\n");
    transportError($client, 'helper_operation_failed');
    foreach (['expired' => 'plan_expired', 'plan_changed' => 'plan_mismatch', 'port_not_allowed' => 'policy_denied',
        'settings_mismatch' => 'settings_changed', 'unavailable' => 'helper_unavailable'] as $rootCode => $mapped) {
        transportError(new StreamFixtureWebManager(json_encode(['ok' => false, 'error' => ['code' => $rootCode, 'message' => 'fixture-secret-canary']]) . "\n"), $mapped);
    }
    transportError(new StreamFixtureWebManager("{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"Task is not present\"}}\n"), 'not_found', 404);
});
check('socket reads stop at the ten-second response deadline', function (): void {
    $start = microtime(true);
    transportError(new StreamFixtureWebManager(''), 'helper_timeout');
    $elapsed = microtime(true) - $start;
    ok($elapsed >= 9.0 && $elapsed < 15.0, 'Root response timeout exceeded its bounded deadline.');
});

echo json_encode(['result' => 'PASS', 'checks' => $checks, 'framework' => Application::VERSION,
    'scope' => 'Isolated real Admin/Sanctum/SQLite API; mocked Root effects and loopback transport; no production server']) . PHP_EOL;
