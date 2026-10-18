<?php
/** Isolated real Laravel encryption/SQLite/Admin tests; all secrets are synthetic. */
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';

use App\Services\Plugin\PluginManager;
use Illuminate\Support\Facades\{Cache, Crypt, DB, Schema};
use Plugin\NodeEndpoint\Services\{CaPassphrases, Endpoint, Settings};

$app->register(Illuminate\Encryption\EncryptionServiceProvider::class);
$checks = [];
$secretCanaries = [];
$cipherCanaries = [];
$credentialTable = 'node_endpoint_ca_credentials';
$credentialHost = '192.0.2.10';
$credentialIds = [str_repeat('a', 32), str_repeat('b', 32), str_repeat('c', 32)];
$revealUrl = 'https://old.panel.example/api/v1/node-endpoint/ca-passphrase/reveal';
$unavailableMessage = 'CA 口令不可用，请检查 HTTPS、当前 CA 绑定或使用原有手动口令。';

function clearCredentialLimits(): void
{
    foreach ([sha1('1'), 'ne-ca-reveal:' . sha1('1')] as $key) {
        app(Illuminate\Cache\RateLimiter::class)->clear($key);
    }
}
function credentialCheck(string $label, callable $body): void
{
    global $checks;
    clearCredentialLimits();
    $body();
    $checks[] = $label;
    echo 'PASS ' . $label . PHP_EOL;
}
function credentialFailure(callable $body): void
{
    try { $body(); }
    catch (Throwable $error) {
        ok($error->getMessage() === 'CA credential unavailable', 'Credential failure did not use its fixed non-sensitive message.');
        return;
    }
    throw new RuntimeException('Credential operation unexpectedly succeeded.');
}
function assertNoCredentials(string $text): void
{
    global $secretCanaries, $cipherCanaries;
    foreach (array_merge($secretCanaries, $cipherCanaries) as $canary) {
        ok(!str_contains($text, $canary), 'A credential or encrypted credential appeared in ordinary output.');
    }
}
function assertPrivateCredentialResponse($response): void
{
    $cache = $response->headers->get('Cache-Control', '');
    ok(str_contains($cache, 'no-store') && str_contains($cache, 'private'), 'Credential response was cacheable.');
    eq($response->headers->get('Pragma'), 'no-cache');
    eq($response->headers->get('Expires'), '0');
}
function replaceCredentialKey(string $key): void
{
    global $app;
    $app['config']->set('app.key', $key);
    $app->forgetInstance('encrypter');
    Crypt::clearResolvedInstance('encrypter');
}
function credentialCa(string $name): array
{
    $config = storage_path('app/credential-openssl.cnf');
    file_put_contents($config, "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=fixture\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n");
    $key = openssl_pkey_new(['private_key_type' => OPENSSL_KEYTYPE_EC, 'private_key_bits' => 2048, 'curve_name' => 'prime256v1', 'config' => $config]);
    if (!$key) { throw new RuntimeException('Synthetic CA key generation failed.'); }
    $csr = openssl_csr_new(['commonName' => $name], $key, ['config' => $config, 'digest_alg' => 'sha256']);
    $certificate = openssl_csr_sign($csr, null, $key, 3650,
        ['config' => $config, 'x509_extensions' => 'v3_ca', 'digest_alg' => 'sha256']);
    if (!$certificate || !openssl_x509_export($certificate, $pem)) { throw new RuntimeException('Synthetic CA signing failed.'); }
    return ['pem' => $pem, 'fingerprint' => openssl_x509_fingerprint($pem, 'sha256')];
}
function credentialBridge(string $input, ?string $key = null): array
{
    global $project, $fixture;
    static $prepared = false;
    if (!$prepared) {
        if (!is_dir($fixture . '/vendor')) { mkdir($fixture . '/vendor', 0700, true); }
        $autoload = '<?php $loader = require ' . var_export(__DIR__ . '/vendor/autoload.php', true) . '; '
            . '$loader->addPsr4("App\\\\", ' . var_export($project . '/.audit/Xboard/app/', true) . '); return $loader;';
        file_put_contents($fixture . '/vendor/autoload.php', $autoload);
        $bootstrap = file_get_contents($project . '/tests/panel/ca_fixture_bootstrap.php');
        $bootstrap = str_replace("'key' => 'base64:' . base64_encode(str_repeat('c', 32))",
            "'key' => getenv('NE_CREDENTIAL_TEST_APP_KEY')", $bootstrap, $keyReplacements);
        $bootstrap = str_replace('\\Illuminate\\Cache\\CacheServiceProvider::class,',
            '\\Illuminate\\Encryption\\EncryptionServiceProvider::class, \\Illuminate\\Cache\\CacheServiceProvider::class,',
            $bootstrap, $providerReplacements);
        ok($keyReplacements === 1 && $providerReplacements === 1, 'CLI fixture bootstrap no longer matches its explicit test overrides.');
        file_put_contents($fixture . '/bootstrap/app.php', $bootstrap);
        chmod($fixture . '/bootstrap/app.php', 0600);
        chmod($fixture . '/vendor/autoload.php', 0600);
        $prepared = true;
    }
    $arguments = [PHP_BINARY];
    if (php_ini_loaded_file()) { array_push($arguments, '-c', php_ini_loaded_file()); }
    array_push($arguments, $project . '/tools/panel/panel_passphrase.php', $fixture);
    assertNoCredentials(implode(' ', $arguments));
    $process = new Symfony\Component\Process\Process($arguments, $fixture,
        ['NE_CREDENTIAL_TEST_APP_KEY' => $key ?? config('app.key')]);
    $process->setInput($input);
    $process->setTimeout(20);
    $process->run();
    ok($process->getErrorOutput() === '', 'Credential CLI forwarded application error output.');
    $output = $process->getOutput();
    ok(strlen($output) <= 4096 && substr_count(trim($output), "\n") === 0, 'Credential CLI returned unexpected diagnostic output.');
    try { $response = json_decode($output, true, 8, JSON_THROW_ON_ERROR); }
    catch (Throwable) { throw new RuntimeException('Credential CLI did not return a single JSON response.'); }
    return [$process->getExitCode(), $response];
}
function credentialBridgeFailure(array|string $input, ?string $key = null): void
{
    [$exit, $response] = credentialBridge(is_array($input) ? json_encode($input, JSON_THROW_ON_ERROR) : $input, $key);
    eq($exit, 1, 'Credential CLI did not fail closed.');
    eq($response, ['error' => 'ca_credential_unavailable']);
    assertNoCredentials(json_encode($response));
}

credentialCheck('native plugin migration creates a separate credential vault', function (): void {
    global $credentialTable, $credentialHost;
    $manager = app(PluginManager::class);
    eq($manager->install('node_endpoint'), true);
    eq($manager->enable('node_endpoint'), true);
    ok(Schema::hasTable($credentialTable), 'Credential migration did not run through the native installer.');
    foreach (['id', 'host', 'fingerprint', 'ciphertext'] as $column) {
        ok(Schema::hasColumn($credentialTable, $column), 'Credential vault column missing.');
    }
    Settings::write(array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => $credentialHost, 'https' => true]));
    eq(DB::table($credentialTable)->count(), 0);
});

credentialCheck('generated passphrases use real APP_KEY encryption and remain outside normal settings', function (): void {
    global $credentialIds, $credentialHost, $credentialTable, $secretCanaries, $cipherCanaries;
    foreach ($credentialIds as $id) {
        $secret = CaPassphrases::prepare($id, $credentialHost);
        ok(strlen($secret) === 64 && preg_match('/^[A-Za-z0-9_-]{64}$/D', $secret) === 1, 'Generated credential format or entropy length differs.');
        $secretCanaries[] = $secret;
        $row = DB::table($credentialTable)->where('id', $id)->first();
        ok($row !== null && is_string($row->ciphertext) && $row->ciphertext !== '', 'Encrypted vault record missing.');
        $cipherCanaries[] = $row->ciphertext;
        ok(!str_contains($row->ciphertext, $secret), 'Vault ciphertext contains the plaintext credential.');
        ok(str_contains(Crypt::decryptString($row->ciphertext), $secret), 'Stored credential was not encrypted with the application encrypter.');
    }
    ok(count(array_unique($secretCanaries)) === count($credentialIds), 'Separate operations reused a generated credential.');
    $rows = DB::table($credentialTable)->get()->toJson();
    foreach ($secretCanaries as $secret) { ok(!str_contains($rows, $secret), 'Plaintext credential stored in database columns.'); }
    assertNoCredentials((string) DB::table('node_endpoint_settings')->value('settings'));
});

credentialCheck('prepare is idempotent for the same operation and rejects host substitution', function (): void {
    global $credentialIds, $credentialHost, $credentialTable, $secretCanaries;
    $before = DB::table($credentialTable)->where('id', $credentialIds[0])->first()->ciphertext;
    ok(hash_equals($secretCanaries[0], CaPassphrases::prepare($credentialIds[0], $credentialHost)), 'Retry regenerated a pending credential.');
    ok(hash_equals($secretCanaries[0], CaPassphrases::get($credentialIds[0])), 'Pending credential cannot be recovered by the local bridge service.');
    credentialFailure(fn () => CaPassphrases::prepare($credentialIds[0], '198.51.100.20'));
    ok(DB::table($credentialTable)->where('id', $credentialIds[0])->first()->ciphertext === $before, 'Rejected host change altered stored ciphertext.');
});

credentialCheck('malformed identities and hosts fail generically without inserting credentials', function (): void {
    global $credentialTable, $credentialHost;
    $before = DB::table($credentialTable)->count();
    foreach (['', '../state', str_repeat('a', 31), str_repeat('z', 32), str_repeat('a', 33)] as $id) {
        credentialFailure(fn () => CaPassphrases::prepare($id, $credentialHost));
        credentialFailure(fn () => CaPassphrases::get($id));
    }
    foreach (['', 'https://192.0.2.10', '192.0.2.10:8443', '../host'] as $host) {
        credentialFailure(fn () => CaPassphrases::prepare(str_repeat('d', 32), $host));
    }
    eq(DB::table($credentialTable)->count(), $before);
});

$credentialRoots = [credentialCa('Credential Fixture Root A'), credentialCa('Credential Fixture Root B')];
credentialCheck('binding is idempotent and cannot replace another CA or another operation', function (): void {
    global $credentialIds, $credentialHost, $credentialRoots, $credentialTable;
    [$first, $second] = array_column($credentialRoots, 'fingerprint');
    CaPassphrases::bind($credentialIds[0], $credentialHost, $first);
    CaPassphrases::bind($credentialIds[0], $credentialHost, $first);
    $before = DB::table($credentialTable)->orderBy('id')->get()->toJson();
    credentialFailure(fn () => CaPassphrases::bind($credentialIds[0], $credentialHost, $second));
    credentialFailure(fn () => CaPassphrases::bind($credentialIds[1], $credentialHost, $first));
    credentialFailure(fn () => CaPassphrases::bind($credentialIds[1], '198.51.100.20', $second));
    credentialFailure(fn () => CaPassphrases::bind($credentialIds[1], $credentialHost, 'invalid'));
    ok(DB::table($credentialTable)->orderBy('id')->get()->toJson() === $before, 'Rejected binding modified credentials.');
    CaPassphrases::bind($credentialIds[1], $credentialHost, $second);
});

credentialCheck('metadata exposes availability for current public trust only', function (): void {
    global $credentialRoots;
    $pins = array_column($credentialRoots, 'fingerprint');
    $metadata = CaPassphrases::metadata($pins);
    eq($metadata, array_map(fn ($pin) => ['fingerprint_sha256' => $pin, 'available' => true], $pins));
    assertNoCredentials(json_encode($metadata));
    foreach ($metadata as $item) { eq(array_keys($item), ['fingerprint_sha256', 'available']); }
    $unknown = str_repeat('0', 64);
    eq(CaPassphrases::metadata([$unknown]), [['fingerprint_sha256' => $unknown, 'available' => false]]);
    $response = requestFixture('https://old.panel.example/api/v1/node-endpoint/ca', 'POST',
        ['pem' => implode('', array_column($credentialRoots, 'pem')), 'fingerprints' => $pins], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200, 'Synthetic public CA import failed.');
});

credentialCheck('anonymous and ordinary accounts cannot reveal a CA passphrase', function (): void {
    global $revealUrl, $credentialRoots;
    foreach ([null, $GLOBALS['userToken']] as $token) {
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $token);
        eq($response->getStatusCode(), 403);
        assertNoCredentials($response->getContent());
    }
});

credentialCheck('HTTPS administrator reveals only the selected current CA with no-store headers', function (): void {
    global $revealUrl, $credentialRoots, $secretCanaries, $credentialTable;
    $before = DB::table($credentialTable)->orderBy('id')->get()->toJson();
    foreach ($credentialRoots as $index => $ca) {
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $ca['fingerprint']], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 200, 'Administrator credential reveal failed.');
        assertPrivateCredentialResponse($response);
        $data = jsonResponse($response)['data'];
        eq($data['fingerprint_sha256'], $ca['fingerprint']);
        ok(hash_equals($secretCanaries[$index], $data['passphrase']), 'Reveal returned a different CA credential.');
        eq(array_keys($data), ['fingerprint_sha256', 'passphrase']);
    }
    ok(DB::table($credentialTable)->orderBy('id')->get()->toJson() === $before, 'Reveal changed stored credentials.');
});

credentialCheck('untrusted unknown and malformed fingerprints fail without exposing credentials', function (): void {
    global $revealUrl, $credentialRoots, $unavailableMessage;
    $bundle = file_get_contents(Settings::caPath());
    file_put_contents(Settings::caPath(), $credentialRoots[1]['pem']);
    try {
        foreach ([$credentialRoots[0]['fingerprint'], str_repeat('0', 64), 'invalid', ''] as $pin) {
            clearCredentialLimits();
            $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $pin], $GLOBALS['adminToken']);
            eq($response->getStatusCode(), 422);
            eq(jsonResponse($response), ['message' => $unavailableMessage]);
            assertPrivateCredentialResponse($response);
            assertNoCredentials($response->getContent());
        }
    } finally { file_put_contents(Settings::caPath(), $bundle); }
});

credentialCheck('HTTP access never reveals the encrypted CA passphrase', function (): void {
    global $revealUrl, $credentialRoots, $unavailableMessage;
    $response = requestFixture(str_replace('https:', 'http:', $revealUrl), 'POST',
        ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 422);
    eq(jsonResponse($response), ['message' => $unavailableMessage]);
    assertPrivateCredentialResponse($response);
    assertNoCredentials($response->getContent());
});

credentialCheck('wrong APP_KEY cannot recover or overwrite an existing credential', function (): void {
    global $app, $credentialIds, $credentialHost, $credentialTable, $revealUrl, $credentialRoots, $secretCanaries, $unavailableMessage;
    $key = config('app.key');
    $before = DB::table($credentialTable)->orderBy('id')->get()->toJson();
    replaceCredentialKey('base64:' . base64_encode(random_bytes(32)));
    try {
        credentialFailure(fn () => CaPassphrases::get($credentialIds[0]));
        credentialFailure(fn () => CaPassphrases::prepare($credentialIds[0], $credentialHost));
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 422);
        eq(jsonResponse($response), ['message' => $unavailableMessage]);
        assertPrivateCredentialResponse($response);
        assertNoCredentials($response->getContent());
        ok(DB::table($credentialTable)->orderBy('id')->get()->toJson() === $before, 'Wrong APP_KEY replaced a credential.');
    } finally { replaceCredentialKey($key); }
    ok(hash_equals($secretCanaries[0], CaPassphrases::get($credentialIds[0])), 'Original APP_KEY could not recover its credential.');
});

credentialCheck('ciphertext corruption is refused with a fixed non-sensitive error', function (): void {
    global $credentialIds, $credentialTable, $revealUrl, $credentialRoots, $unavailableMessage;
    $before = DB::table($credentialTable)->where('id', $credentialIds[0])->value('ciphertext');
    DB::table($credentialTable)->where('id', $credentialIds[0])->update(['ciphertext' => 'damaged-ciphertext']);
    try {
        credentialFailure(fn () => CaPassphrases::get($credentialIds[0]));
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 422);
        eq(jsonResponse($response), ['message' => $unavailableMessage]);
        assertNoCredentials($response->getContent());
        eq(DB::table($credentialTable)->where('id', $credentialIds[0])->value('ciphertext'), 'damaged-ciphertext');
    } finally { DB::table($credentialTable)->where('id', $credentialIds[0])->update(['ciphertext' => $before]); }
});

credentialCheck('ordinary settings and both machine command responses contain no CA credential material', function (): void {
    $response = requestFixture('https://old.panel.example/api/v1/node-endpoint/settings', 'GET', [], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200);
    assertNoCredentials($response->getContent());
    eq(count(jsonResponse($response)['data']['ca_passphrases']), 2);
    $response = requestFixture('https://old.panel.example/api/v2/admin/server/machine/installCommand', 'GET',
        ['id' => $GLOBALS['machine']->id], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200);
    assertNoCredentials($response->getContent());
    $response = requestFixture('https://old.panel.example/api/v1/node-endpoint/command', 'POST',
        ['mode' => 'machine', 'kernel' => 'xray', 'architecture' => 'amd64', 'instance' => 'credential-fixture', 'identity_id' => 7], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200);
    assertNoCredentials($response->getContent());
    assertNoCredentials(json_encode(Settings::read()));
});

credentialCheck('reveal endpoint has a separate five-attempt administrator limit', function (): void {
    global $revealUrl, $credentialRoots;
    for ($index = 0; $index < 5; ++$index) {
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 200, 'Credential rate limiter blocked before the fifth request.');
    }
    $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 429);
    assertNoCredentials($response->getContent());
});

credentialCheck('plugin disable blocks credential access and preserves encrypted recovery records', function (): void {
    global $credentialTable, $revealUrl, $credentialRoots;
    $before = DB::table($credentialTable)->orderBy('id')->get()->toJson();
    app(PluginManager::class)->disable('node_endpoint');
    try {
        $response = requestFixture($revealUrl, 'POST', ['fingerprint' => $credentialRoots[0]['fingerprint']], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 404);
        assertNoCredentials($response->getContent());
        ok(DB::table($credentialTable)->orderBy('id')->get()->toJson() === $before, 'Disabling the plugin removed encrypted recovery material.');
    } finally { app(PluginManager::class)->enable('node_endpoint'); }
});

credentialCheck('real PHP CLI bridge prepares recovers and binds without credential arguments', function (): void {
    global $credentialIds, $credentialHost, $credentialTable, $credentialRoots, $secretCanaries, $cipherCanaries;
    $id = str_repeat('e', 32);
    $request = ['action' => 'prepare', 'credential_id' => $id, 'host' => $credentialHost];
    [$exit, $prepared] = credentialBridge(json_encode($request, JSON_THROW_ON_ERROR));
    eq($exit, 0, 'Run this isolated test as a non-root application user on Linux.');
    eq(array_keys($prepared), ['credential_id', 'passphrase']);
    eq($prepared['credential_id'], $id);
    ok(preg_match('/^[A-Za-z0-9_-]{64}$/D', $prepared['passphrase']) === 1, 'CLI generated an invalid credential.');
    $secretCanaries[] = $prepared['passphrase'];
    $cipherCanaries[] = DB::table($credentialTable)->where('id', $id)->value('ciphertext');
    ok(hash_equals($prepared['passphrase'], CaPassphrases::get($id)), 'CLI did not use the real application vault.');
    [$exit, $retry] = credentialBridge(json_encode($request, JSON_THROW_ON_ERROR));
    eq($exit, 0);
    ok(hash_equals($prepared['passphrase'], $retry['passphrase']), 'CLI retry replaced the pending credential.');
    $request['action'] = 'get';
    [$exit, $recovered] = credentialBridge(json_encode($request, JSON_THROW_ON_ERROR));
    eq($exit, 0);
    ok(hash_equals($prepared['passphrase'], $recovered['passphrase']), 'CLI recovery returned a different credential.');
    // Omission is supported for recovery by opaque ID; Root normally also sends its original host.
    unset($request['host']);
    [$exit, $recovered] = credentialBridge(json_encode($request, JSON_THROW_ON_ERROR));
    eq($exit, 0);
    ok(hash_equals($prepared['passphrase'], $recovered['passphrase']), 'Opaque-ID recovery unexpectedly regenerated a credential.');
    $pin = str_repeat('e', 64);
    $binding = ['action' => 'bind', 'credential_id' => $id, 'host' => $credentialHost, 'fingerprint' => $pin];
    [$exit, $bound] = credentialBridge(json_encode($binding, JSON_THROW_ON_ERROR));
    eq($exit, 0);
    eq($bound, ['status' => 'stored', 'credential_id' => $id, 'fingerprint' => $pin]);
    ok(hash_equals($prepared['passphrase'], CaPassphrases::reveal($pin)), 'CLI bind did not persist the selected vault association.');
    assertNoCredentials(json_encode($bound));
});

credentialCheck('real PHP CLI bridge rejects invalid input wrong host key and disabled plugin generically', function (): void {
    global $credentialIds, $credentialHost, $credentialTable, $credentialRoots;
    $before = DB::table($credentialTable)->orderBy('id')->get()->toJson();
    $request = ['action' => 'get', 'credential_id' => $credentialIds[0], 'host' => $credentialHost];
    credentialBridgeFailure(array_replace($request, ['host' => '198.51.100.20']));
    credentialBridgeFailure(array_replace($request, ['unexpected' => true]));
    credentialBridgeFailure(array_replace($request, ['action' => 'reveal']));
    credentialBridgeFailure(array_replace($request, ['credential_id' => '../state']));
    credentialBridgeFailure('{broken');
    credentialBridgeFailure(str_repeat('x', 4097));
    credentialBridgeFailure($request, 'base64:' . base64_encode(random_bytes(32)));
    credentialBridgeFailure(['action' => 'bind', 'credential_id' => $credentialIds[2], 'host' => $credentialHost,
        'fingerprint' => $credentialRoots[0]['fingerprint']]);
    app(PluginManager::class)->disable('node_endpoint');
    try { credentialBridgeFailure($request); }
    finally { app(PluginManager::class)->enable('node_endpoint'); }
    ok(DB::table($credentialTable)->orderBy('id')->get()->toJson() === $before, 'Rejected CLI operations changed the credential vault.');
});

credentialCheck('application logs and the normal settings row contain no credential material', function (): void {
    foreach (glob(storage_path('logs/*')) ?: [] as $path) {
        if (is_file($path)) { assertNoCredentials(file_get_contents($path)); }
    }
    assertNoCredentials((string) DB::table('node_endpoint_settings')->value('settings'));
});

echo json_encode(['result' => 'PASS', 'checks' => count($checks), 'framework' => Illuminate\Foundation\Application::VERSION,
    'scope' => 'Isolated Laravel/Crypt/SQLite, native Admin API and real PHP CLI subprocess; generated test credentials only; no production']) . PHP_EOL;
