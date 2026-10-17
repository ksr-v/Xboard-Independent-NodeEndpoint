<?php
declare(strict_types=1);

// Test-only CLI: read observer registration after the same native CLI bootstrap
// used by panel_probe.php. It never saves models, dispatches events or jobs,
// reads user records, prints configuration values, or modifies application files.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
ini_set('display_errors', '0');
umask(0077);
$level = ob_get_level(); ob_start();
try {
    $options = [];
    foreach (array_slice($argv, 1) as $argument) {
        if (!preg_match('/^--(app|stage|run)=(.+)$/D', $argument, $match) || isset($options[$match[1]])) throw new RuntimeException();
        $options[$match[1]] = $match[2];
    }
    $root = realpath($options['app'] ?? '');
    if (!$root || !is_file($root . '/vendor/autoload.php') || !is_file($root . '/bootstrap/app.php')) throw new RuntimeException();
    require $root . '/vendor/autoload.php';
    $app = require $root . '/bootstrap/app.php';
    $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    $provider = \App\Providers\EventServiceProvider::class;
    $observer = \App\Observers\UserObserver::class;
    $model = new \App\Models\User(); // Boot the model, without querying or saving it.
    $dispatcher = $model->getEventDispatcher();
    $raw = $dispatcher?->getRawListeners() ?? [];
    $listeners = [];
    foreach (['created', 'updated', 'deleted'] as $event) {
        $registered = $raw['eloquent.' . $event . ': ' . \App\Models\User::class] ?? [];
        $wanted = $observer . '@' . $event;
        $listeners[$event] = [
            'registered_listener_count' => count($registered),
            'native_user_observer_registered' => in_array($wanted, $registered, true),
        ];
    }
    $cache = [];
    foreach (['config', 'events', 'services', 'packages'] as $name) {
        $path = $root . '/bootstrap/cache/' . $name . '.php';
        $cache[$name] = ['exists' => is_file($path), 'sha256' => is_file($path) ? hash_file('sha256', $path) : null];
    }
    $attributes = (new ReflectionClass($model))->getAttributes(\Illuminate\Database\Eloquent\Attributes\ObservedBy::class);
    $source = $root . '/app/Providers/EventServiceProvider.php';
    $result = [
        'status' => 'OK', 'laravel' => $app->version(), 'php' => PHP_VERSION,
        'native_cli_bootstrap' => true, 'database_mutation' => false,
        'application_event_provider_loaded' => (bool) ($app->getLoadedProviders()[$provider] ?? false),
        'application_event_provider_in_config' => in_array($provider, config('app.providers', []), true),
        'user_model_observed_by_attribute_count' => count($attributes),
        'user_observer_class_exists' => class_exists($observer),
        'event_dispatcher_present' => $dispatcher !== null,
        'user_observer_after_commit' => (new ReflectionClass($observer))->getDefaultProperties()['afterCommit'] ?? null,
        'user_event_listeners' => $listeners, 'bootstrap_cache' => $cache,
        'event_provider_source_sha256' => is_file($source) ? hash_file('sha256', $source) : null,
        'database_transaction_level' => \Illuminate\Support\Facades\DB::connection()->transactionLevel(),
        'after_commit_pending_transaction_count' => $app->make('db.transactions')->getPendingTransactions()->count(),
        'queue_driver' => config('queue.connections.' . config('queue.default') . '.driver'),
        'queue_after_commit' => (bool) config('queue.connections.' . config('queue.default') . '.after_commit', false),
        'autoloaded_native_source_paths_match' => [
            'user_model' => realpath((new ReflectionClass($model))->getFileName()) === realpath($root . '/app/Models/User.php'),
            'user_observer' => realpath((new ReflectionClass($observer))->getFileName()) === realpath($root . '/app/Observers/UserObserver.php'),
            'node_sync_job' => realpath((new ReflectionClass(\App\Jobs\NodeUserSyncJob::class))->getFileName()) === realpath($root . '/app/Jobs/NodeUserSyncJob.php'),
        ],
        'cli_opcache' => [
            'enabled' => (bool) ini_get('opcache.enable_cli'),
            'validate_timestamps' => (bool) ini_get('opcache.validate_timestamps'),
            'file_cache_configured' => (string) ini_get('opcache.file_cache') !== '',
            'file_cache_only' => (bool) ini_get('opcache.file_cache_only'),
        ],
    ];
    if (isset($options['stage']) || isset($options['run'])) {
        $stage = realpath($options['stage'] ?? ''); $run = $options['run'] ?? '';
        if (!$stage || is_link($options['stage']) || !preg_match('/^[a-z0-9][a-z0-9-]{5,27}$/D', $run)) throw new RuntimeException();
        foreach ([$stage . '/.node-endpoint-e2e-owner', $stage . '/' . $run . '-state.json'] as $private) {
            if (!is_file($private) || is_link($private) || (fileperms($private) & 0077) || fileowner($private) !== posix_geteuid()) throw new RuntimeException();
        }
        $state = json_decode((string) file_get_contents($private), true, 32, JSON_THROW_ON_ERROR);
        if (($state['schema'] ?? null) !== 1 || ($state['run'] ?? null) !== $run || ($state['app_path'] ?? null) !== $root || ($state['status'] ?? null) !== 'created') throw new RuntimeException();
        $user = \App\Models\User::findOrFail((int) $state['ids']['user']);
        if ($user->email !== $run . '@node-endpoint-e2e.invalid' || $user->remarks !== 'NE-E2E-' . $run || $user->is_admin || $user->is_staff || (int) $user->group_id !== (int) $state['ids']['group']) throw new RuntimeException();
        $nodes = \App\Models\Server::whereJsonContains('group_ids', (string) $user->group_id)->pluck('id')->map(fn ($id) => (int) $id)->all();
        $expected = array_column($state['nodes'], 'id'); sort($nodes); sort($expected);
        $result['synthetic_user'] = ['id' => (int) $user->id, 'group_id' => (int) $user->group_id,
            'is_available' => $user->isAvailable(), 'banned' => (bool) $user->banned,
            'group_matching_node_count' => count($nodes), 'group_matches_exact_owned_nodes' => $nodes === $expected];
        $result['queue_sizes'] = [];
        foreach (['traffic_fetch', 'stat', 'node_sync'] as $queue) $result['queue_sizes'][$queue] = \Illuminate\Support\Facades\Queue::size($queue);
        $failures = ['table_present' => \Illuminate\Support\Facades\Schema::hasTable('failed_jobs'), 'node_sync_job_count' => 0, 'exception_class_counts' => []];
        if ($failures['table_present']) {
            foreach (\Illuminate\Support\Facades\DB::table('failed_jobs')->where('queue', 'node_sync')->select(['payload', 'exception'])->cursor() as $row) {
                $payload = json_decode($row->payload, true);
                if (($payload['displayName'] ?? null) !== \App\Jobs\NodeUserSyncJob::class) continue;
                $failures['node_sync_job_count']++;
                $class = preg_match('/^([A-Za-z_\\\\][A-Za-z0-9_\\\\]*)\s*:/D', $row->exception, $found) ? $found[1] : 'OTHER';
                if (!in_array($class, ['Error', 'TypeError', 'PDOException', 'RuntimeException',
                    'Illuminate\\Queue\\MaxAttemptsExceededException', 'Illuminate\\Database\\QueryException',
                    'Illuminate\\Contracts\\Container\\BindingResolutionException',
                    'Illuminate\\Queue\\TimeoutExceededException'], true)) $class = 'OTHER';
                $failures['exception_class_counts'][$class] = ($failures['exception_class_counts'][$class] ?? 0) + 1;
            }
        }
        $result['failed_node_sync_jobs'] = $failures; // Never return payloads or exception text.
    }
    while (ob_get_level() > $level) ob_end_clean();
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
} catch (Throwable $error) {
    while (ob_get_level() > $level) ob_end_clean();
    echo json_encode(['status' => 'ERROR', 'reason' => 'OBSERVER_READ_ONLY_PROBE_FAILED']) . "\n";
    exit(1);
}
