<?php
/** Isolated native-component integration; not a production or Linux deployment test. */
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use Illuminate\Config\Repository;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\{Auth, Cache, DB, Route, Schema};
use App\Services\Plugin\PluginManager;
use Plugin\NodeEndpoint\Services\{Endpoint, Settings};

$project = dirname(__DIR__, 2);
$fixture = getenv('NE_FIXTURE_PATH') ?: __DIR__ . '/work/' . gmdate('Ymd-His') . '-' . bin2hex(random_bytes(3));
$fresh = !is_file($fixture . '/database.sqlite');

function copyTree(string $source, string $target): void
{
    if (is_link($source)) throw new RuntimeException('Fixture refuses symbolic links.');
    if (!is_dir($target) && !mkdir($target, 0700, true)) throw new RuntimeException('Fixture mkdir failed.');
    foreach (new DirectoryIterator($source) as $item) {
        if ($item->isDot() || $item->getFilename() === '.git') continue;
        if ($item->isLink()) throw new RuntimeException('Fixture refuses symbolic links.');
        $destination = $target . '/' . $item->getFilename();
        if ($item->isDir()) copyTree($item->getPathname(), $destination);
        elseif (!copy($item->getPathname(), $destination)) throw new RuntimeException('Fixture copy failed.');
    }
}
if ($fresh) {
foreach (['bootstrap/cache', 'storage/app/node_endpoint', 'storage/framework/views', 'storage/logs', 'resources/views', 'plugins', 'public'] as $dir) mkdir($fixture . '/' . $dir, 0700, true);
copyTree($project . '/plugin/NodeEndpoint', $fixture . '/plugins/NodeEndpoint');
copyTree($project . '/.audit/xb-SmartExpiry', $fixture . '/plugins/SmartExpiry');
copyTree($project . '/.audit/xb-qrcode-extend/Qrcodeextend', $fixture . '/plugins/Qrcodeextend');
copyTree($project . '/.audit/xb-flowscope/FlowScope', $fixture . '/plugins/Flowscope');
copyTree($project . '/.audit/Xboard-Independent/private-assets/xboard/submodule/admin-dist', $fixture . '/public/assets/admin');
copy($project . '/.audit/Xboard/resources/views/admin.blade.php', $fixture . '/resources/views/admin.blade.php');
touch($fixture . '/database.sqlite');
file_put_contents($fixture . '/storage/app/node_endpoint/root-owned-evidence.txt', 'root-owned: preserve');
}

spl_autoload_register(function (string $class) use ($project, $fixture): void {
    $prefixes = ['App\\' => $project . '/.audit/Xboard/app/', 'Plugin\\' => $fixture . '/plugins/'];
    foreach ($prefixes as $prefix => $base) {
        if (str_starts_with($class, $prefix)) {
            $file = $base . str_replace('\\', '/', substr($class, strlen($prefix))) . '.php';
            if (is_file($file)) require_once $file;
        }
    }
});

// Supplies settings storage only: the tested Controller/middleware/runtime remain real source classes.
$testSettings = ['app_url' => 'https://old.panel.example', 'server_token' => 'fixture-node-token',
    'server_ws_url' => 'wss://old.panel.example/ws', 'server_ws_enable' => 1];
function admin_setting($key, $default = null) { return $GLOBALS['testSettings'][$key] ?? $default; }

final class FixtureApplication extends Application { public function runningInConsole() { return false; } }
final class FixtureHttpKernel extends Illuminate\Foundation\Http\Kernel
{
    protected $bootstrappers = [];
    protected $middleware = [App\Http\Middleware\TrustProxies::class, App\Http\Middleware\InitializePlugins::class];
    protected $middlewareGroups = ['api' => [App\Http\Middleware\ForceJson::class], 'web' => []];
    protected $middlewareAliases = ['admin' => App\Http\Middleware\Admin::class, 'server.v2' => App\Http\Middleware\ServerV2::class,
        'throttle' => Illuminate\Routing\Middleware\ThrottleRequests::class];
}
final class FixtureConsoleKernel extends Illuminate\Foundation\Console\Kernel { protected $bootstrappers = []; }
final class FixtureUser extends Illuminate\Foundation\Auth\User
{
    use Laravel\Sanctum\HasApiTokens;
    protected $table = 'users';
    protected $guarded = [];
    protected $casts = ['is_admin' => 'boolean'];
}

$app = new FixtureApplication($fixture);
$app->useStoragePath($fixture . '/storage');
$app->usePublicPath($fixture . '/public');
$app->instance('config', new Repository([
    'app' => ['name' => 'NodeEndpoint component fixture', 'env' => 'testing', 'debug' => true, 'timezone' => 'UTC',
        'url' => 'https://old.panel.example', 'key' => 'base64:' . base64_encode(random_bytes(32)), 'cipher' => 'AES-256-CBC'],
    'database' => ['default' => 'sqlite', 'connections' => ['sqlite' => ['driver' => 'sqlite', 'database' => $fixture . '/database.sqlite', 'prefix' => '', 'foreign_key_constraints' => true]], 'migrations' => ['table' => 'migrations']],
    'cache' => ['default' => 'array', 'stores' => ['array' => ['driver' => 'array', 'serialize' => false]]],
    'logging' => ['default' => 'single', 'channels' => ['single' => ['driver' => 'single', 'path' => $fixture . '/storage/logs/fixture.log', 'level' => 'debug']]],
    'auth' => ['defaults' => ['guard' => 'sanctum'], 'guards' => ['sanctum' => ['driver' => 'sanctum', 'provider' => 'users']], 'providers' => ['users' => ['driver' => 'eloquent', 'model' => FixtureUser::class]]],
    'sanctum' => ['guard' => [], 'expiration' => null, 'routes' => false],
    'view' => ['paths' => [$fixture . '/resources/views'], 'compiled' => $fixture . '/storage/framework/views'],
    'filesystems' => ['default' => 'local', 'disks' => ['local' => ['driver' => 'local', 'root' => $fixture . '/storage/app']]],
]));
Illuminate\Support\Facades\Facade::setFacadeApplication($app);
$app->instance('request', Illuminate\Http\Request::create('https://old.panel.example/'));
$app->singleton(Illuminate\Contracts\Debug\ExceptionHandler::class, App\Exceptions\Handler::class);
$app->singleton(Illuminate\Contracts\Http\Kernel::class, FixtureHttpKernel::class);
$app->singleton(Illuminate\Contracts\Console\Kernel::class, FixtureConsoleKernel::class);
foreach ([Illuminate\Filesystem\FilesystemServiceProvider::class, Illuminate\Database\DatabaseServiceProvider::class,
    Illuminate\Database\MigrationServiceProvider::class, Illuminate\Cache\CacheServiceProvider::class,
    Illuminate\View\ViewServiceProvider::class, Illuminate\Translation\TranslationServiceProvider::class,
    Illuminate\Validation\ValidationServiceProvider::class, Illuminate\Foundation\Providers\FoundationServiceProvider::class,
    Illuminate\Auth\AuthServiceProvider::class,
    Illuminate\Hashing\HashServiceProvider::class, Laravel\Sanctum\SanctumServiceProvider::class,
    App\Providers\PluginServiceProvider::class] as $provider) $app->register($provider);
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
$app->boot();

if ($fresh) {
Schema::create('v2_plugins', function (Blueprint $t): void { $t->id(); $t->string('code')->unique(); $t->string('name'); $t->string('version'); $t->string('type')->default('feature'); $t->boolean('is_enabled')->default(false); $t->text('config')->nullable(); $t->timestamp('installed_at')->nullable(); $t->timestamps(); });
Schema::create('users', function (Blueprint $t): void { $t->id(); $t->string('name'); $t->string('email')->unique(); $t->string('password'); $t->boolean('is_admin')->default(false); $t->timestamps(); });
Schema::create('personal_access_tokens', function (Blueprint $t): void { $t->id(); $t->morphs('tokenable'); $t->string('name'); $t->string('token', 64)->unique(); $t->text('abilities')->nullable(); $t->timestamp('last_used_at')->nullable(); $t->timestamp('expires_at')->nullable(); $t->timestamps(); });
Schema::create('v2_server_machine', function (Blueprint $t): void { $t->id(); $t->string('name'); $t->text('notes')->nullable(); $t->string('token'); $t->boolean('is_active')->default(true); $t->unsignedBigInteger('last_seen_at')->nullable(); $t->timestamps(); });
Schema::create('production_evidence', function (Blueprint $t): void { $t->id(); $t->string('kind'); $t->text('value'); });
DB::table('production_evidence')->insert(['kind' => 'business records', 'value' => 'users,nodes,machine tokens,traffic,subscriptions,certificates']);
$admin = FixtureUser::create(['name' => 'Fixture admin', 'email' => 'admin@fixture.invalid', 'password' => 'not-a-login-password', 'is_admin' => true]);
$user = FixtureUser::create(['name' => 'Fixture user', 'email' => 'user@fixture.invalid', 'password' => 'not-a-login-password', 'is_admin' => false]);
$adminToken = $admin->createToken('test-admin')->plainTextToken;
$userToken = $user->createToken('test-user')->plainTextToken;
$machine = App\Models\ServerMachine::create(['name' => 'Fixture machine', 'token' => 'fixture-machine-token', 'is_active' => true]);
file_put_contents($fixture . '/fixture-auth.json', json_encode(['admin_token' => $adminToken, 'user_token' => $userToken]));
} else {
    $tokens = json_decode(file_get_contents($fixture . '/fixture-auth.json'), true, 512, JSON_THROW_ON_ERROR);
    $adminToken = $tokens['admin_token'];
    $userToken = $tokens['user_token'];
    $machine = App\Models\ServerMachine::first();
}
Route::middleware(['api', 'server.v2'])->match(['GET', 'POST'], 'api/v2/server/handshake', [App\Http\Controllers\V2\Server\ServerController::class, 'handshake']);
Route::middleware('api')->get('api/fixture/other', fn () => response()->json(['websocket' => ['enabled' => true, 'ws_url' => 'wss://other.example/ws']]));
Route::middleware(['api', 'admin'])->get('api/v2/admin/server/machine/installCommand', [App\Http\Controllers\V2\Admin\Server\MachineController::class, 'installCommand']);
Route::middleware(['api', 'admin'])->post('api/v2/admin/server/machine/save', [App\Http\Controllers\V2\Admin\Server\MachineController::class, 'save']);
Route::middleware(['api', 'admin'])->post('api/v2/admin/server/machine/resetToken', [App\Http\Controllers\V2\Admin\Server\MachineController::class, 'resetToken']);
Route::get('fixture-admin', fn () => view('admin', ['title' => 'Fixture', 'version' => 'fixture', 'logo' => '', 'secure_path' => 'fixture-admin']));

function requestFixture(string $url, string $method = 'GET', array $data = [], ?string $bearer = null, array $server = []): Symfony\Component\HttpFoundation\Response
{
    global $app, $kernel;
    $app->forgetScopedInstances();
    Auth::forgetGuards();
    $server = array_replace(['HTTP_ACCEPT' => 'application/json', 'REMOTE_ADDR' => '192.0.2.44'], $server);
    if ($bearer !== null) $server['HTTP_AUTHORIZATION'] = 'Bearer ' . $bearer;
    $request = Illuminate\Http\Request::create($url, $method, $data, [], [], $server);
    $response = $kernel->handle($request);
    $kernel->terminate($request, $response);
    return $response;
}
function eq($actual, $expected, string $message = ''): void { if ($actual !== $expected) throw new RuntimeException($message . ' expected ' . json_encode($expected) . ', received ' . json_encode($actual)); }
function ok(bool $value, string $message): void { if (!$value) throw new RuntimeException($message); }
function jsonResponse($response): array { return json_decode($response->getContent(), true, 512, JSON_THROW_ON_ERROR); }
