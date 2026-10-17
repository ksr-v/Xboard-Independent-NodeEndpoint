<?php
// Isolated Laravel/SQLite fixture; no production database or administrator token.
$fixture = dirname(__DIR__);
spl_autoload_register(function (string $class) use ($fixture): void {
    if (str_starts_with($class, 'Plugin\\NodeEndpoint\\')) {
        $path = $fixture . '/plugins/NodeEndpoint/' . str_replace('\\', '/', substr($class, strlen('Plugin\\NodeEndpoint\\'))) . '.php';
        if (is_file($path)) { require_once $path; }
    }
}, true, true);
class CaFixtureConsoleKernel extends \Illuminate\Foundation\Console\Kernel { protected $bootstrappers = []; }
$app = new \Illuminate\Foundation\Application($fixture);
$app->useStoragePath($fixture . '/storage');
$app->instance('config', new \Illuminate\Config\Repository([
    'app' => ['name' => 'CA sync fixture', 'env' => 'testing', 'debug' => false, 'timezone' => 'UTC',
              'key' => 'base64:' . base64_encode(str_repeat('c', 32)), 'cipher' => 'AES-256-CBC', 'providers' => []],
    'database' => ['default' => 'sqlite', 'connections' => ['sqlite' => ['driver' => 'sqlite', 'database' => $fixture . '/database.sqlite', 'prefix' => '']]],
    'cache' => ['default' => 'array', 'stores' => ['array' => ['driver' => 'array']]],
    'view' => ['paths' => [$fixture . '/resources/views'], 'compiled' => $fixture . '/storage/framework/views'],
    'logging' => ['default' => 'null', 'channels' => ['null' => ['driver' => 'monolog', 'handler' => \Monolog\Handler\NullHandler::class]]],
]));
\Illuminate\Support\Facades\Facade::setFacadeApplication($app);
$app->instance('request', \Illuminate\Http\Request::create('https://fixture.invalid/'));
$app->singleton(\Illuminate\Contracts\Console\Kernel::class, CaFixtureConsoleKernel::class);
foreach ([\Illuminate\Filesystem\FilesystemServiceProvider::class, \Illuminate\Database\DatabaseServiceProvider::class,
          \Illuminate\Cache\CacheServiceProvider::class, \Illuminate\View\ViewServiceProvider::class] as $provider) {
    $app->register($provider);
}
$app->boot();
return $app;
