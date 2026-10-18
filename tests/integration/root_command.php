<?php
/** Saved-settings Root command generation through the real native Admin API. */
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';

use App\Services\Plugin\PluginManager;
use Plugin\NodeEndpoint\Services\{Endpoint, RootCommandBuilder, Settings};

$checks = 0;
$settingsUrl = 'https://old.panel.example/api/v1/node-endpoint/settings';
function rootCommandCheck(string $label, callable $body): void
{
    $body();
    $GLOBALS['checks']++;
    echo 'PASS ' . $label . PHP_EOL;
}
function rootCommandSettings(array $changes = []): array
{
    return array_replace(Endpoint::DEFAULTS, ['enabled' => true, 'host' => '1.1.1.1', 'port' => 9443, 'ws_mode' => 'rest'], $changes);
}

rootCommandCheck('native installation exposes no command before a saved enabled address', function (): void {
    $manager = app(PluginManager::class);
    eq($manager->install('node_endpoint'), true);
    eq($manager->enable('node_endpoint'), true);
    $response = requestFixture($GLOBALS['settingsUrl'], 'GET', [], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200, $response->getContent());
    $tool = jsonResponse($response)['data']['root_tool'];
    eq($tool['command'], null);
    eq($tool['target_url'], null);
    ok($tool['note'] !== '', 'Missing unavailable reason.');
});

rootCommandCheck('saving returns a pinned command for the normalized saved host and explicit SSH guidance', function (): void {
    $response = requestFixture($GLOBALS['settingsUrl'], 'POST', rootCommandSettings(), $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200, $response->getContent());
    $data = jsonResponse($response)['data'];
    $tool = $data['root_tool'];
    eq($tool['command'], 'curl -fsSL https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.6/install.sh | bash -s -- --host \'1.1.1.1\'');
    eq($tool['target_url'], 'https://1.1.1.1:9443');
    foreach (['root', 'SSH', 'PHP', '9443', 'rest', '站点', '确认', '验证入口'] as $detail) {
        ok(str_contains($tool['note'], $detail), 'Missing SSH wizard detail: ' . $detail);
    }
    eq(Settings::read()['host'], $data['settings']['host']);
    eq(Settings::read()['port'], 9443);
});

rootCommandCheck('loading uses persisted values and ignores attempted command or host overrides', function (): void {
    $before = Settings::read();
    $response = requestFixture($GLOBALS['settingsUrl'], 'GET', ['host' => 'evil.example', 'command' => 'touch /tmp/never-run'], $GLOBALS['adminToken']);
    eq($response->getStatusCode(), 200, $response->getContent());
    eq(jsonResponse($response)['data']['root_tool'], RootCommandBuilder::build($before));
    eq(Settings::read(), $before, 'Read-only command generation wrote settings.');
});

rootCommandCheck('DNS and IPv6 are normalized before shell quoting and target URL generation', function (): void {
    $tool = RootCommandBuilder::build(rootCommandSettings(['host' => 'NODE.Example.']));
    ok(str_ends_with($tool['command'], "--host 'node.example'"), 'DNS normalization or quoting differs.');
    eq($tool['target_url'], 'https://node.example:9443');
    $tool = RootCommandBuilder::build(rootCommandSettings(['host' => '2606:4700:4700:0:0:0:0:1111']));
    ok(str_ends_with($tool['command'], "--host '2606:4700:4700::1111'"), 'IPv6 normalization or quoting differs.');
    eq($tool['target_url'], 'https://[2606:4700:4700::1111]:9443');
});

rootCommandCheck('anonymous and ordinary users cannot fetch commands or alter saved settings', function (): void {
    $before = Settings::read();
    foreach ([null, $GLOBALS['userToken']] as $token) {
        foreach (['GET', 'POST'] as $method) {
            $response = requestFixture($GLOBALS['settingsUrl'], $method, rootCommandSettings(['host' => '8.8.8.8']), $token);
            eq($response->getStatusCode(), 403);
            ok(!str_contains($response->getContent(), 'root-installer-v1.0.6'), 'Unauthorized response exposed the command.');
            eq(Settings::read(), $before);
        }
    }
});

rootCommandCheck('injection and all documentation address ranges produce no command', function (): void {
    foreach (["node.example'; id; '", '$(id).example', "node.example\nwhoami", 'https://node.example',
        '192.0.2.1', '198.51.100.254', '203.0.113.10', '2001:db8::1', '2001:DB8:FFFF:FFFF::1'] as $host) {
        $tool = RootCommandBuilder::build(rootCommandSettings(['host' => $host]));
        eq($tool['command'], null, 'Unsafe host produced a Root command.');
        ok($tool['note'] !== '', 'Unsafe host lacked an unavailable reason.');
    }
});

rootCommandCheck('HTTP disabled incomplete and website ports do not generate the HTTPS wizard command', function (): void {
    foreach ([['enabled' => false], ['enabled' => false, 'host' => ''], ['host' => ''],
        ['https' => false, 'http_acknowledged' => true], ['port' => 80], ['port' => 443],
        ['port' => 65536], ['https' => 'true']] as $change) {
        $tool = RootCommandBuilder::build(rootCommandSettings($change));
        eq($tool['command'], null, 'Unsupported configuration produced a command.');
        ok($tool['note'] !== '', 'Unsupported configuration lacked an unavailable reason.');
    }
});

rootCommandCheck('invalid saves leave persisted settings and their command unchanged', function (): void {
    $before = Settings::read();
    $expected = RootCommandBuilder::build($before);
    foreach ([['host' => "1.1.1.1'; id"], ['host' => ['8.8.8.8']], ['port' => -1], ['ws_mode' => 'shell']] as $change) {
        $response = requestFixture($GLOBALS['settingsUrl'], 'POST', rootCommandSettings($change), $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 422);
        ok(!array_key_exists('root_tool', jsonResponse($response)['data'] ?? []), 'Rejected save generated a command.');
        eq(Settings::read(), $before);
        $reload = requestFixture($GLOBALS['settingsUrl'], 'GET', [], $GLOBALS['adminToken']);
        eq(jsonResponse($reload)['data']['root_tool'], $expected);
    }
});

rootCommandCheck('legacy documentation addresses remain readable with an unavailable command reason', function (): void {
    $before = Settings::read();
    try {
        Settings::write(rootCommandSettings(['host' => '203.0.113.10']));
        $response = requestFixture($GLOBALS['settingsUrl'], 'GET', [], $GLOBALS['adminToken']);
        eq($response->getStatusCode(), 200, $response->getContent());
        $data = jsonResponse($response)['data'];
        eq($data['settings']['host'], '203.0.113.10');
        eq($data['root_tool']['command'], null);
        ok(str_contains($data['root_tool']['note'], '示例'), 'Legacy example address lacked a repair hint.');
    } finally { Settings::write($before); }
});

echo 'Root command integration checks passed: ' . $checks . PHP_EOL;
