<?php
/** Dependency-free release command checks; only synthetic credentials, no shell execution. */
declare(strict_types=1);
require dirname(__DIR__, 2) . '/plugin/NodeEndpoint/Services/MachineInstallCommand.php';
use Plugin\NodeEndpoint\Services\MachineInstallCommand;

$passed = 0;
function releaseCheck(string $name, callable $fn): void {
    global $passed;
    $fn();
    ++$passed;
    echo 'PASS ' . $name . PHP_EOL;
}
function requireTrue(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}

releaseCheck('domain command follows the latest stable Release installer', function (): void {
    $command = MachineInstallCommand::buildDomain('https://panel.example.test', 17, 'synthetic-token');
    requireTrue(MachineInstallCommand::INSTALLER === 'https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/latest/download/install.sh', 'Latest stable installer differs.');
    requireTrue(!str_contains($command, '--version') && !str_contains($command, 'orphan.'), 'Command still pins a Node release.');
    foreach (['--mode'=>'machine', '--panel'=>'https://panel.example.test', '--token'=>'synthetic-token', '--machine-id'=>'17'] as $key=>$value) {
        requireTrue(str_contains($command, escapeshellarg($key) . ' ' . escapeshellarg($value)), 'Argument differs: ' . $key);
    }
    requireTrue(str_starts_with($command, 'curl -fsSL ' . escapeshellarg(MachineInstallCommand::INSTALLER) . ' | sudo bash -s -- '), 'Pipeline differs.');
    requireTrue(!str_contains($command, '--endpoint-ca'), 'Domain command unexpectedly requires a private CA.');
});

releaseCheck('synthetic shell-sensitive machine identity stays one escaped argument', function (): void {
    $token = 'fake token\'";$(do-not-run)`also-not-run`';
    $url = 'https://panel.example.test:8443';
    $command = MachineInstallCommand::buildDomain($url, 42, $token);
    requireTrue(str_contains($command, escapeshellarg('--token') . ' ' . escapeshellarg($token)), 'Token escaping differs.');
    requireTrue(str_contains($command, escapeshellarg('--panel') . ' ' . escapeshellarg($url)), 'URL escaping differs.');
});

releaseCheck('IP command retains CA bundle and both pins with the latest stable installer', function (): void {
    $pem = "-----BEGIN CERTIFICATE-----\nSYNTHETIC-PUBLIC-FIXTURE\n-----END CERTIFICATE-----\n";
    $pins = [str_repeat('a', 64), str_repeat('b', 64)];
    $command = MachineInstallCommand::build(['url'=>'https://192.0.2.18:8443', 'pem'=>$pem, 'fingerprints'=>$pins], 19, 'synthetic-token');
    foreach (['--endpoint-ca-base64'=>base64_encode($pem), '--endpoint-ca-fingerprint'=>implode(',', $pins)] as $key=>$value) {
        requireTrue(str_contains($command, escapeshellarg($key) . ' ' . escapeshellarg($value)), 'IP argument differs: ' . $key);
    }
    requireTrue(str_contains($command, escapeshellarg(MachineInstallCommand::INSTALLER)), 'IP installer differs.');
    requireTrue(!str_contains($command, '--version') && !str_contains($command, 'orphan.'), 'IP command still pins a Node release.');
});

releaseCheck('invalid machine identity is rejected before rendering a command', function (): void {
    foreach ([['https://panel.example.test', 0, 'synthetic-token'], ['https://panel.example.test', 1, ''], ['', 1, 'synthetic-token']] as $args) {
        try { MachineInstallCommand::buildDomain(...$args); }
        catch (InvalidArgumentException) { continue; }
        throw new RuntimeException('Invalid identity was accepted.');
    }
});
echo json_encode(['result'=>'PASS', 'checks'=>$passed, 'scope'=>'synthetic command generation; no installer or shell execution'], JSON_UNESCAPED_SLASHES) . PHP_EOL;
