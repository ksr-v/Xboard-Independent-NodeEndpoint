<?php
namespace Plugin\NodeEndpoint\Services;
use InvalidArgumentException;
final class CommandBuilder
{
    private static function quote(string $value): string { return "'" . str_replace("'", "'\"'\"'", $value) . "'"; }
    public static function build(array $settings, array $options, array $fingerprints): array
    {
        $settings = Endpoint::validate($settings);
        if (!$settings['enabled']) { throw new InvalidArgumentException('请先保存并启用通讯入口。'); }
        foreach (['mode' => ['node', 'machine'], 'kernel' => ['singbox', 'xray'], 'architecture' => ['amd64', 'arm64']] as $key => $allowed) {
            if (!in_array($options[$key] ?? '', $allowed, true)) { throw new InvalidArgumentException("{$key} 无效。"); }
        }
        $id = $options['instance'] ?? '';
        if (!is_string($id) || !preg_match('/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$/D', $id)) {
            throw new InvalidArgumentException('实例名仅包含字母、数字、下划线和连字符，长度 1–48。');
        }
        $identity = $options['identity_id'] ?? 0;
        if (!is_int($identity) || $identity < 1) { throw new InvalidArgumentException('节点或机器 ID 必须为正整数。'); }
        $validFingerprints = count($fingerprints) >= 1 && count($fingerprints) <= 2
            && count(array_unique($fingerprints)) === count($fingerprints);
        foreach ($fingerprints as $fingerprint) {
            $validFingerprints = $validFingerprints && is_string($fingerprint)
                && preg_match('/^[a-f0-9]{64}$/D', $fingerprint) === 1;
        }
        if ($settings['https'] && !$validFingerprints) {
            throw new InvalidArgumentException('HTTPS 对接前必须先核对并导入根 CA。');
        }
        $args = ['python3', 'tools/node/node_endpoint.py', 'prepare', '--output-dir', "/etc/node-endpoint-node/{$id}",
            '--panel-url', Endpoint::urls($settings)['url'], '--mode', $options['mode'], '--kernel', $options['kernel'],
            '--architecture', $options['architecture']];
        if ($options['mode'] === 'machine') { array_push($args, '--machine-id', (string) $identity); }
        else {
            $type = $options['node_type'] ?? '';
            if (!in_array($type, ['shadowsocks', 'vmess', 'vless', 'trojan', 'hysteria', 'hysteria2', 'tuic', 'anytls'], true)) {
                throw new InvalidArgumentException('节点类型无效。');
            }
            // Xboard accepts hysteria2 as an authentication alias; the pinned
            // kernels use protocol=hysteria and the REST config's version.
            if ($type === 'hysteria2') { $type = 'hysteria'; }
            if ($options['kernel'] === 'xray' && !in_array($type, ['shadowsocks', 'vmess', 'vless', 'trojan', 'hysteria'], true)) {
                throw new InvalidArgumentException('当前固定版本的 Xray 不支持该节点类型，请选择 sing-box。');
            }
            array_push($args, '--node-id', (string) $identity, '--node-type', $type);
        }
        if ($settings['https']) { array_push($args, '--ca-file', '/root/node-endpoint-ca.pem', '--ca-fingerprint', implode(',', $fingerprints)); }
        else { $args[] = '--allow-http'; }
        array_push($args, '--confirm-new-identity', 'UNUSED', '--download');
        return ['prepare' => implode(' ', array_map([self::class, 'quote'], $args)),
            'install' => "python3 tools/node/node_endpoint.py install-new --prepared-dir " . self::quote("/etc/node-endpoint-node/{$id}") . " --confirm-install NEW",
            'notes' => ['在已校验版本和 SHA-256 的工具包目录执行；先通过可信渠道复制并核对 CA。',
                'Token 将交互输入，命令不含 Token。仅创建新实例；迁移已有实例使用 inspect/migrate。',
                '二进制下载版本和摘要由工具内锁定；不运行上游重装脚本。',
                '仅用于尚未运行的新身份。认证 WSS 会注册连接和清理设备状态；已有实例必须走停服迁移。',
                'prepare 会分别预检 REST 和 WSS；请查看工具报告和生产验收限制。']];
    }
}
