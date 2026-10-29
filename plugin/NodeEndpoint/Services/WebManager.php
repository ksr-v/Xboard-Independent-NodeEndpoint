<?php
declare(strict_types=1);

namespace Plugin\NodeEndpoint\Services;

/** The application can only send this small protocol to a separately installed Root helper. */
class WebManager
{
    public const SOCKET = '/run/nodeendpoint-web-manager/control.sock';
    private const LIMIT = 131072;
    private const OPERATIONS = ['status', 'plan', 'execute', 'job'];

    public static function failure(string $code, int $status = 422): WebManagerException
    {
        return new WebManagerException($code, $status);
    }

    protected function metadata(string $path): array|false
    {
        clearstatcache(true, $path);
        return @lstat($path);
    }

    private function verifiedSocket(): array
    {
        // lstat every component: neither a symlink nor a writable ancestor is trusted.
        foreach (['/', '/run', '/run/nodeendpoint-web-manager'] as $path) {
            $stat = $this->metadata($path);
            if ($stat === false) { throw self::failure('helper_unavailable', 503); }
            if (($stat['mode'] & 0170000) !== 0040000 || $stat['uid'] !== 0 || ($stat['mode'] & 0022) !== 0) {
                throw self::failure('helper_unsafe', 503);
            }
        }
        $socket = $this->metadata(self::SOCKET);
        if ($socket === false) { throw self::failure('helper_unavailable', 503); }
        if (($socket['mode'] & 0170000) !== 0140000 || $socket['uid'] !== 0 || ($socket['mode'] & 0777) !== 0660) {
            throw self::failure('helper_unsafe', 503);
        }
        return $socket;
    }

    protected function connect()
    {
        return @stream_socket_client('unix://' . self::SOCKET, $errno, $error, 2.0, STREAM_CLIENT_CONNECT);
    }

    public function request(string $operation, array $parameters = []): array
    {
        if (!in_array($operation, self::OPERATIONS, true)) { throw self::failure('invalid_request'); }
        $before = $this->verifiedSocket();
        $stream = $this->connect();
        if (!is_resource($stream)) { throw self::failure('helper_unavailable', 503); }
        try {
            $after = $this->verifiedSocket();
            if ($before['dev'] !== $after['dev'] || $before['ino'] !== $after['ino']) {
                throw self::failure('helper_unsafe', 503);
            }
            stream_set_blocking($stream, false);
            $deadline = microtime(true) + 10.0;
            $wire = json_encode(['schema' => 1, 'operation' => $operation, 'parameters' => (object) $parameters],
                JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
            if (strlen($wire) > 8192) { throw self::failure('invalid_request'); }
            for ($offset = 0; $offset < strlen($wire);) {
                $this->ready($stream, $deadline, true);
                $written = @fwrite($stream, substr($wire, $offset));
                if ($written === false || ($written === 0 && feof($stream))) { throw self::failure('helper_protocol', 502); }
                $offset += $written;
            }
            $response = '';
            while (!str_contains($response, "\n")) {
                $this->ready($stream, $deadline, false);
                $chunk = @fread($stream, min(8192, self::LIMIT + 1 - strlen($response)));
                if ($chunk === false || ($chunk === '' && feof($stream))) { throw self::failure('helper_protocol', 502); }
                $response .= $chunk;
                if (strlen($response) > self::LIMIT) { throw self::failure('helper_protocol', 502); }
            }
            if (substr_count($response, "\n") !== 1 || trim(substr($response, strpos($response, "\n") + 1)) !== '') {
                throw self::failure('helper_protocol', 502);
            }
            try { $decoded = json_decode(trim($response), true, 24, JSON_THROW_ON_ERROR); }
            catch (\Throwable) { throw self::failure('helper_protocol', 502); }
            if (!is_array($decoded) || !is_bool($decoded['ok'] ?? null)) { throw self::failure('helper_protocol', 502); }
            if (!$decoded['ok']) {
                $code = $decoded['error']['code'] ?? '';
                $aliases = ['expired' => 'plan_expired', 'plan_changed' => 'plan_mismatch', 'capacity' => 'busy',
                    'port_not_allowed' => 'policy_denied', 'policy_changed' => 'policy_denied', 'settings_mismatch' => 'settings_changed',
                    'unavailable' => 'helper_unavailable', 'unsafe_path' => 'helper_unsafe', 'unauthorized' => 'policy_denied'];
                $code = is_string($code) ? ($aliases[$code] ?? $code) : '';
                $known = ['busy', 'plan_expired', 'plan_mismatch', 'confirmation_mismatch', 'policy_denied',
                    'not_installed', 'not_found', 'invalid_request', 'settings_changed', 'manual_required',
                    'helper_unavailable', 'helper_unsafe', 'entry_failed', 'ca_sync_failed', 'firewall_failed'];
                throw self::failure(is_string($code) && in_array($code, $known, true) ? $code : 'helper_operation_failed',
                    in_array($code, ['helper_unavailable', 'helper_unsafe'], true) ? 503
                        : ($code === 'not_found' ? 404 : (in_array($code, ['busy', 'plan_expired', 'plan_mismatch', 'settings_changed'], true) ? 409 : 422)));
            }
            if (!is_array($decoded['data'] ?? null)) { throw self::failure('helper_protocol', 502); }
            return $decoded['data'];
        } finally { fclose($stream); }
    }

    private function ready($stream, float $deadline, bool $write): void
    {
        $remaining = $deadline - microtime(true);
        if ($remaining <= 0) { throw self::failure('helper_timeout', 504); }
        $read = $write ? [] : [$stream];
        $writes = $write ? [$stream] : [];
        $except = [];
        $seconds = (int) $remaining;
        $selected = @stream_select($read, $writes, $except, $seconds, (int) (($remaining - $seconds) * 1000000));
        if ($selected === false) { throw self::failure('helper_protocol', 502); }
        if ($selected === 0) { throw self::failure('helper_timeout', 504); }
    }

    public static function identifier(mixed $value): bool
    {
        return is_string($value) && preg_match('/^[A-Za-z0-9_-]{16,128}$/D', $value) === 1;
    }

    public static function target(mixed $value, bool $withMode = false): array
    {
        if (!is_array($value) || !is_string($value['host'] ?? null) || !is_int($value['port'] ?? null)
            || ($value['https'] ?? null) !== true) { throw self::failure('helper_protocol', 502); }
        try {
            $validated = Endpoint::validate(array_replace(Endpoint::DEFAULTS,
                ['enabled' => true, 'host' => $value['host'], 'port' => $value['port'], 'https' => true,
                    'ws_mode' => $value['ws_mode'] ?? 'rewrite']));
        } catch (\Throwable) { throw self::failure('helper_protocol', 502); }
        $keys = $withMode ? ['host', 'port', 'https', 'ws_mode'] : ['host', 'port', 'https'];
        if ($withMode && !array_key_exists('ws_mode', $value)) { throw self::failure('helper_protocol', 502); }
        $target = [];
        foreach ($keys as $key) { $target[$key] = $validated[$key]; }
        return $target;
    }

    /** Only Root's existing public status format may enter plugin settings or responses. */
    public static function publicStatus(mixed $raw): array
    {
        $keys = ['schema_version', 'version', 'installed', 'endpoint', 'ca', 'server', 'trust', 'rotation',
            'checks', 'generated_at', 'ca_key_online', 'revocation_enforced'];
        if (!is_array($raw) || ($raw['schema_version'] ?? null) !== 1 || !is_string($raw['version'] ?? null)) {
            throw self::failure('helper_protocol', 502);
        }
        $public = array_intersect_key($raw, array_flip($keys));
        $encoded = json_encode($public, JSON_THROW_ON_ERROR);
        if (strlen($encoded) > 32768 || preg_match('/PRIVATE KEY|"[^"\\\\]*(?:password|passphrase|token|secret)[^"\\\\]*"\s*:/i', $encoded)) {
            throw self::failure('helper_protocol', 502);
        }
        if (isset($public['endpoint'])) { $public['endpoint'] = self::target($public['endpoint']); }
        return $public;
    }

    public static function stages(mixed $raw): array
    {
        if (!is_array($raw) || !array_is_list($raw) || count($raw) > 32) { throw self::failure('helper_protocol', 502); }
        $out = [];
        foreach ($raw as $stage) {
            if (is_string($stage)) {
                if (!preg_match('/^[a-z][a-z0-9_-]{0,63}$/D', $stage)) { throw self::failure('helper_protocol', 502); }
                $out[] = $stage;
                continue;
            }
            if (!is_array($stage) || !is_string($stage['name'] ?? null)
                || !preg_match('/^[a-z][a-z0-9_-]{0,63}$/D', $stage['name'])
                || !in_array($stage['state'] ?? null, ['pending', 'queued', 'running', 'succeeded', 'partial', 'failed', 'interrupted', 'skipped'], true)) {
                throw self::failure('helper_protocol', 502);
            }
            $item = ['name' => $stage['name'], 'state' => $stage['state']];
            if (isset($stage['code']) && is_string($stage['code']) && preg_match('/^[a-z][a-z0-9_]{0,63}$/D', $stage['code'])) {
                $item['code'] = $stage['code'];
            }
            if (isset($stage['message'])) { $item['message'] = '请根据阶段状态检查 Root 助手日志或重新生成操作计划。'; }
            $out[] = $item;
        }
        return $out;
    }
}

final class WebManagerException extends \RuntimeException
{
    public function __construct(public readonly string $errorCode, public readonly int $httpStatus = 422)
    {
        $messages = [
            'unauthorized' => '需要管理员 Bearer 身份验证。', 'secure_origin_required' => '操作需要 HTTPS 和同源 Origin。',
            'invalid_request' => '操作参数无效。', 'invalid_settings' => '请先保存并启用有效的 HTTPS 通信端点。',
            'helper_unavailable' => 'Root Web 管理助手尚未安装或无法连接。', 'helper_unsafe' => 'Root 助手通道权限不安全。',
            'helper_protocol' => 'Root 助手响应格式无效。', 'helper_timeout' => 'Root 助手响应超时，请查询已有任务状态。',
            'helper_operation_failed' => 'Root 操作失败，请检查助手状态。', 'busy' => '已有 Root 任务运行，请稍后重试。',
            'plan_expired' => '操作计划已过期，请重新生成。', 'plan_mismatch' => '操作计划不匹配，请重新生成。',
            'confirmation_mismatch' => '输入的确认文字与计划不一致。', 'policy_denied' => 'Root 助手策略不允许此操作。',
            'not_installed' => '请先通过 SSH 初始化独立通信入口。', 'not_found' => '未找到此管理员的计划或任务。',
            'settings_changed' => '端点设置已改变，请重新生成操作计划。', 'target_mismatch' => 'Root 入口与已保存的 HTTPS 端点不一致。',
            'ca_unavailable' => 'Root 助手尚未提供有效的公共 CA 证书。', 'storage_failed' => '无法原子保存公共 CA 和状态快照。',
            'manual_required' => '此状态需要通过 SSH 使用 Root 工具处理后，再从网页操作。',
            'entry_failed' => '独立通信入口配置失败，请检查 Root 助手状态。', 'ca_sync_failed' => '公共 CA 同步失败，请重新同步。',
            'firewall_failed' => '防火墙端口处理失败，请检查 Root 助手状态。',
            'owner_storage_failed' => '无法保存管理员操作关联，请检查应用缓存后重试。',
        ];
        parent::__construct($messages[$errorCode] ?? $messages['helper_operation_failed']);
    }
}
