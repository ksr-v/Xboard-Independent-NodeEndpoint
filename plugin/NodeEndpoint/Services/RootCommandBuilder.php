<?php
namespace Plugin\NodeEndpoint\Services;
use InvalidArgumentException;
final class RootCommandBuilder
{
    private const INSTALLER = 'https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.9/install.sh';
    private static function unavailable(string $note, ?string $target = null): array
    {
        return ['command' => null, 'note' => $note, 'target_url' => $target];
    }
    private static function quote(string $value): string { return "'" . str_replace("'", "'\"'\"'", $value) . "'"; }
    private static function exampleHost(string $host): bool
    {
        if (filter_var($host, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            foreach (['192.0.2.', '198.51.100.', '203.0.113.'] as $prefix) {
                if (str_starts_with($host, $prefix)) { return true; }
            }
        }
        if (filter_var($host, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
            return substr(inet_pton($host), 0, 4) === hex2bin('20010db8');
        }
        return false;
    }
    public static function build(array $saved): array
    {
        try { $settings = Endpoint::validate($saved); }
        catch (InvalidArgumentException) {
            return self::unavailable('请先保存有效的节点通讯地址和端口，再复制 Root 工具命令。');
        }
        $target = Endpoint::urls($settings)['url'];
        if (!$settings['enabled'] || $settings['host'] === '') {
            return self::unavailable('请先填写通讯地址，保存并启用节点通讯设置。', $target);
        }
        if (!$settings['https']) {
            return self::unavailable('此一键命令使用 HTTPS；HTTP 入口请通过 Root 工具手工配置并确认明文传输风险。', $target);
        }
        if (in_array($settings['port'], [80, 443], true)) {
            return self::unavailable('独立通讯入口不能使用 80 或 443 端口，请修改并保存端口。', $target);
        }
        if (self::exampleHost($settings['host'])) {
            return self::unavailable('当前是文档示例 IP，请先改为面板服务器实际通讯 IP 并保存。', $target);
        }
        $mode = $settings['ws_mode'] === 'rewrite' ? 'WebSocket 重写（rewrite）' : 'REST fallback（rest）';
        return ['command' => 'curl -fsSL ' . self::INSTALLER . ' | bash -s -- --host ' . self::quote($settings['host']),
            'target_url' => $target,
            'note' => '请在面板服务器以 root 用户通过 SSH 执行；选择正确站点和 PHP，核对已保存端口 '
                . $settings['port'] . ' 和 WS 模式 ' . $mode
                . '，按提示确认。命令只预填已保存地址，端口和 WS 模式仍需在 SSH 向导中核对；完成后返回本页验证入口。'];
    }
}
