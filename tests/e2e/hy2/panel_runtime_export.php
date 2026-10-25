<?php
declare(strict_types=1);

// Reuse the isolated real-Laravel fixture; retain its public boundary results.
ob_start();
try {
    require __DIR__ . '/backend_subscriptions.php';
} finally {
    ob_end_clean();
}

Illuminate\Support\Facades\Schema::create('v2_user', function (Illuminate\Database\Schema\Blueprint $table): void {
    $table->id(); $table->string('email'); $table->string('uuid'); $table->integer('group_id');
    $table->integer('speed_limit')->default(0); $table->integer('device_limit')->default(0);
    $table->integer('created_at')->nullable(); $table->integer('updated_at')->nullable();
});
$runtimeUser = App\Models\User::create([
    'email' => 'hy2-runtime@fixture.test', 'uuid' => (string) Illuminate\Support\Str::uuid(),
    'group_id' => 1, 'speed_limit' => 0, 'device_limit' => 0,
]);
$payload['id'] = $node->id;
$payload['host'] = '10.213.25.1';
$payload['port'] = '42450-42453';
$payload['server_port'] = 42443;
$payload['protocol_settings']['version'] = 2;
$payload['protocol_settings']['hop_interval'] = 5;
[$runtimeRequest, $runtimeValidator] = validation($payload);
check($runtimeValidator->passes(), 'Runtime fixture request validation failed.');
$runtimeRequest->setValidator($runtimeValidator);
$runtimeResponse = (new App\Http\Controllers\V2\Admin\Server\ManageController())->save($runtimeRequest);
check($runtimeResponse->getStatusCode() === 200, 'Runtime fixture controller save failed.');
$runtimeNode = App\Models\Server::findOrFail($node->id);
$runtimeAvailable = App\Services\ServerService::getAvailableServers($runtimeUser);
check(count($runtimeAvailable) === 1, 'Runtime subscription fixture server absent.');
$runtimeProtocol = new App\Protocols\SingBox($runtimeUser->toArray(), $runtimeAvailable, 'sing-box', '1.11.0');
$runtimeOutbound = (new ReflectionMethod(App\Protocols\SingBox::class, 'buildHysteria'))
    ->invoke($runtimeProtocol, $runtimeAvailable[0]['password'], $runtimeAvailable[0]);
$runtimeNodeConfig = App\Services\ServerService::buildNodeConfig($runtimeNode);
check($runtimeOutbound['server'] === '10.213.25.1', 'Runtime subscription host mismatch.');
check($runtimeOutbound['server_ports'] === ['42450:42453'], 'Runtime subscription range mismatch.');
check($runtimeOutbound['server_port'] >= 42450 && $runtimeOutbound['server_port'] <= 42453, 'Runtime subscription initial port mismatch.');
check($runtimeOutbound['hop_interval'] === '5s', 'Runtime subscription interval mismatch.');
check($runtimeOutbound['password'] === $runtimeUser->uuid, 'Runtime identity mismatch.');
check($runtimeNodeConfig['server_port'] === 42443 && $runtimeNodeConfig['version'] === 2, 'Runtime Node API listen mismatch.');
$private = [
    'fixture' => 'Real Laravel, in-memory SQLite, ServerSave/ManageController, Eloquent, ServerService and SingBox 1.11 subscription builder. Synthetic runtime identity only.',
    'source_revision' => '2b3000781d95ae43255471688e9bb8d4220f9551',
    'source_release' => 'v2026.10.25.1',
    'node' => $runtimeNodeConfig,
    'user' => [
        'id' => $runtimeUser->id, 'uuid' => $runtimeUser->uuid,
        'speed_limit' => $runtimeUser->speed_limit, 'device_limit' => $runtimeUser->device_limit,
    ],
    'client_outbound' => $runtimeOutbound,
];
$previousUmask = umask(0077);
try {
    $bytes = file_put_contents(__DIR__ . '/panel-runtime.private.json',
        json_encode($private, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n", LOCK_EX);
    check($bytes !== false, 'Runtime private export failed.');
} finally {
    umask($previousUmask);
}
echo json_encode(['status' => 'exported', 'checks' => 'passed',
    'configuration_keys' => array_keys($private),
    'node_keys' => array_keys($runtimeNodeConfig),
    'client_outbound_keys' => array_keys($runtimeOutbound)], JSON_THROW_ON_ERROR) . "\n";
