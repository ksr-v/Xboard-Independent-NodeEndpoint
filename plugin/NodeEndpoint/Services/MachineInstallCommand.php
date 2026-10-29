<?php
namespace Plugin\NodeEndpoint\Services;
use InvalidArgumentException;
final class MachineInstallCommand
{
    public const INSTALLER = 'https://github.com/ksr-v/Xboard-Node--Custom-Source/releases/download/v1.13-orphan.8/install.sh';
    public static function context(array $settings): array
    {
        $settings = Endpoint::validate($settings);
        if (!$settings['enabled'] || !$settings['https']) {
            throw new InvalidArgumentException('自动机器安装需要已启用的 HTTPS 独立入口。');
        }
        $path = Settings::caPath();
        if (!is_file($path) || is_link($path) || filesize($path) > 32768) {
            throw new InvalidArgumentException('请先用 Root 工具导入公共 CA，再生成机器安装命令。');
        }
        $pem = (string) file_get_contents($path);
        preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', $pem, $matches);
        $pins = array_map(fn ($cert) => openssl_x509_fingerprint($cert, 'sha256'), $matches[0]);
        if (!$pins || in_array(false, $pins, true)) {
            throw new InvalidArgumentException('已导入的公共 CA 无效，请重新同步。');
        }
        $verified = Certificates::import($pem, $pins);
        return ['url' => Endpoint::urls($settings)['url'], 'pem' => $verified['pem'], 'fingerprints' => $pins];
    }
    public static function buildDomain(string $panelUrl, int $id, string $token): string
    {
        return self::command(self::arguments($panelUrl, $id, $token));
    }
    public static function build(array $context, int $id, string $token): string
    {
        return self::command(array_merge(self::arguments($context['url'], $id, $token), [
            '--endpoint-ca-base64', base64_encode($context['pem']),
            '--endpoint-ca-fingerprint', implode(',', $context['fingerprints']),
        ]));
    }
    private static function arguments(string $panelUrl, int $id, string $token): array
    {
        if ($id < 1 || $token === '' || $panelUrl === '') { throw new InvalidArgumentException('机器身份或面板地址无效。'); }
        return ['--mode', 'machine', '--panel', $panelUrl, '--token', $token, '--machine-id', (string) $id,
            '--version', 'v1.13-orphan.8'];
    }
    private static function command(array $args): string
    {
        return 'curl -fsSL ' . escapeshellarg(self::INSTALLER) . ' | sudo bash -s -- '
            . implode(' ', array_map('escapeshellarg', $args));
    }
}
