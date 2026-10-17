<?php
namespace Plugin\NodeEndpoint\Services;
use InvalidArgumentException;
final class Endpoint
{
    public const DEFAULTS = ['enabled' => false, 'host' => '', 'port' => 8443, 'https' => true,
        'http_acknowledged' => false, 'ws_mode' => 'rewrite'];
    public static function host(string $host): string
    {
        $host = strtolower(trim($host));
        if ($host === '') { return ''; }
        if (str_ends_with($host, '.')) { $host = substr($host, 0, -1); }
        if (filter_var($host, FILTER_VALIDATE_IP)) {
            return str_contains($host, ':') ? inet_ntop(inet_pton($host)) : $host;
        }
        if (strlen($host) > 253 || preg_match('/^[0-9.]+$/D', $host)) {
            throw new InvalidArgumentException('请输入合法 IPv4、IPv6 或 DNS 主机名。');
        }
        foreach (explode('.', $host) as $label) {
            if (!preg_match('/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/D', $label)) {
                throw new InvalidArgumentException('地址不含协议、端口、路径、方括号或空格；国际域名请使用 punycode。');
            }
        }
        return $host;
    }
    private static function boolean(mixed $value, string $key): bool
    {
        if (!is_bool($value)) { throw new InvalidArgumentException("{$key} 必须为布尔值。"); }
        return $value;
    }
    public static function validate(array $input, ?string $website = null): array
    {
        $input = array_replace(self::DEFAULTS, $input);
        $out = [];
        foreach (['enabled', 'https', 'http_acknowledged'] as $key) {
            $out[$key] = self::boolean($input[$key], $key);
        }
        if (!is_string($input['host'])) { throw new InvalidArgumentException('host 必须为字符串。'); }
        $out['host'] = self::host($input['host']);
        if (!is_int($input['port']) || $input['port'] < 1 || $input['port'] > 65535) {
            throw new InvalidArgumentException('端口必须为 1–65535 的整数。');
        }
        $out['port'] = $input['port'];
        if (!in_array($input['ws_mode'], ['rewrite', 'rest'], true)) {
            throw new InvalidArgumentException('WS 模式必须为 rewrite 或 rest。');
        }
        $out['ws_mode'] = $input['ws_mode'];
        if ($out['enabled'] && $out['host'] === '') { throw new InvalidArgumentException('启用前必须设置通讯地址。'); }
        if (!$out['https'] && !$out['http_acknowledged']) {
            throw new InvalidArgumentException('HTTP 会明文传输 Token 和通讯内容，必须显式确认风险。');
        }
        if ($out['enabled'] && $website) {
            $parts = parse_url($website);
            $webHost = self::host(trim(strtolower($parts['host'] ?? ''), '[]'));
            $webPort = $parts['port'] ?? (($parts['scheme'] ?? '') === 'https' ? 443 : 80);
            if ($webHost === $out['host'] && $webPort === $out['port']) {
                throw new InvalidArgumentException('该地址和端口与当前网站相同，不能作为独立入口。');
            }
        }
        return $out;
    }
    public static function authority(array $settings): string
    {
        $host = $settings['host'];
        return (str_contains($host, ':') ? "[{$host}]" : $host) . ':' . $settings['port'];
    }
    public static function urls(array $settings): array
    {
        if ($settings['host'] === '') { return ['url' => null, 'ws_url' => null]; }
        $authority = self::authority($settings);
        return ['url' => ($settings['https'] ? 'https://' : 'http://') . $authority,
            'ws_url' => ($settings['https'] ? 'wss://' : 'ws://') . $authority . '/ws'];
    }
    public static function matches(array $settings, string $host, int $port, bool $secure): bool
    {
        try {
            return $settings['enabled'] && $settings['host'] !== ''
                && self::host(trim($host, '[]')) === $settings['host']
                && $port === $settings['port'] && $secure === $settings['https'];
        } catch (InvalidArgumentException) { return false; }
    }
}
