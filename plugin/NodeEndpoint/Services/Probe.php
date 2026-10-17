<?php
namespace Plugin\NodeEndpoint\Services;
use RuntimeException;
final class Probe
{
    public static function run(array $settings): array
    {
        $settings = Endpoint::validate($settings);
        if (!$settings['enabled']) { throw new RuntimeException('请先启用并保存通讯地址。'); }
        if ($settings['https'] && !is_file(Settings::caPath())) {
            throw new RuntimeException('请先导入已独立核对指纹的根 CA。');
        }
        $context = stream_context_create(['ssl' => [
            'verify_peer' => true, 'verify_peer_name' => true, 'allow_self_signed' => false,
            'cafile' => Settings::caPath(), 'peer_name' => $settings['host'],
            'capture_peer_cert' => true, 'SNI_enabled' => true,
        ]]);
        $socket = @stream_socket_client(($settings['https'] ? 'tls://' : 'tcp://') . Endpoint::authority($settings),
            $errno, $error, 5, STREAM_CLIENT_CONNECT, $context);
        if (!$socket) { throw new RuntimeException('连接或证书校验失败。请核对监听、安全组、CA、SAN 和证书时间。'); }
        try {
            stream_set_timeout($socket, 5);
            fwrite($socket, "GET /api/v2/server/handshake HTTP/1.1\r\nHost: " . Endpoint::authority($settings)
                . "\r\nAccept: application/json\r\nConnection: close\r\n\r\n");
            $line = fgets($socket, 1024);
            if (!$line || !preg_match('/^HTTP\/1\.[01] ([0-9]{3}) /', $line, $match)) {
                throw new RuntimeException('入口未返回有效 HTTP 响应。');
            }
            $options = stream_context_get_options($socket);
            $cert = $options['ssl']['peer_certificate'] ?? null;
            $metadata = $cert ? Certificates::metadata(openssl_x509_parse($cert), openssl_x509_fingerprint($cert, 'sha256')) : null;
            $code = (int) $match[1];
            return ['tested_at' => gmdate('c'), 'target_url' => Endpoint::urls($settings)['url'],
                'tls' => $settings['https'] ? 'verified' : 'HTTP explicitly accepted',
                'http_status' => $code, 'rest_endpoint_responding' => in_array($code, [200, 401, 403, 422], true),
                'authentication' => 'NOT TESTED', 'websocket' => 'NOT TESTED', 'server_certificate' => $metadata,
                'note' => '无凭据连通性检查；REST 身份认证和 WSS 需在 Node 上分别预检。重定向不跟随。'];
        } finally { fclose($socket); }
    }
}
