<?php
namespace Plugin\NodeEndpoint\Controllers;
use App\Http\Controllers\Controller;
use App\Models\Plugin;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Plugin\NodeEndpoint\Services\{Endpoint, Settings, Certificates, Probe, CommandBuilder, CaPassphrases, RootCommandBuilder};
final class SettingsController extends Controller
{
    private function invalidResponse(\Throwable $error)
    {
        return response()->json(['message' => $error->getMessage()], 422);
    }
    public function show()
    {
        $settings = Settings::read();
        $ca = [];
        if (is_file(Settings::caPath()) && extension_loaded('openssl')) {
            preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', file_get_contents(Settings::caPath()), $matches);
            foreach ($matches[0] as $pem) {
                $parsed = openssl_x509_parse($pem);
                if ($parsed) { $ca[] = Certificates::metadata($parsed, openssl_x509_fingerprint($pem, 'sha256')); }
            }
        }
        return response()->json(['data' => ['version' => '1.0.9', 'settings' => $settings,
            'urls' => Endpoint::urls($settings), 'ca' => $ca, 'root_tool' => RootCommandBuilder::build($settings),
            'ca_passphrases' => CaPassphrases::metadata(array_column($ca, 'fingerprint_sha256')),
            'compatibility' => Plugin::whereIn('code', ['smart_expiry', 'qrcode_extend', 'qrcodeextend', 'flow_scope', 'flowscope'])
                ->get(['code', 'version', 'is_enabled']),
            'note' => 'FlowScope 采集器 panel_url 独立维护；本插件不修改其绑定、数据或服务。']]);
    }
    public function revealCaPassphrase(Request $request)
    {
        $headers = ['Cache-Control' => 'no-store, private', 'Pragma' => 'no-cache', 'Expires' => '0'];
        try {
            $input = $request->all();
            if (!$request->isSecure() || array_keys($input) !== ['fingerprint'] || !is_string($input['fingerprint'])
                || !preg_match('/^[a-f0-9]{64}$/D', $input['fingerprint'])) { throw new \RuntimeException(); }
            $path = Settings::caPath();
            if (!extension_loaded('openssl') || !is_file($path) || is_link($path) || filesize($path) > 32768) { throw new \RuntimeException(); }
            $pem = file_get_contents($path);
            if (!is_string($pem) || str_contains($pem, 'PRIVATE KEY')) { throw new \RuntimeException(); }
            preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', $pem, $matches);
            if (!$matches[0] || count($matches[0]) > 2 || trim(str_replace($matches[0], '', $pem)) !== '') { throw new \RuntimeException(); }
            $fingerprints = array_map(fn ($cert) => openssl_x509_fingerprint($cert, 'sha256'), $matches[0]);
            if (!in_array($input['fingerprint'], $fingerprints, true)) { throw new \RuntimeException(); }
            return response()->json(['data' => ['fingerprint_sha256' => $input['fingerprint'],
                'passphrase' => CaPassphrases::reveal($input['fingerprint'])]], 200, $headers);
        } catch (\Throwable) {
            return response()->json(['message' => 'CA 口令不可用，请检查 HTTPS、当前 CA 绑定或使用原有手动口令。'], 422, $headers);
        }
    }
    public function save(Request $request)
    {
        try {
            $new = Endpoint::validate($request->only(array_keys(Endpoint::DEFAULTS)), admin_setting('app_url') ?: $request->getSchemeAndHttpHost());
            $old = Settings::read();
            Settings::write(array_replace($old, $new, ['last_probe' => null]));
            return response()->json(['data' => ['settings' => $new, 'urls' => Endpoint::urls($new),
                'root_tool' => RootCommandBuilder::build($new),
                'note' => '仅保存插件设置。请使用下方一键复制按钮，在面板服务器通过 SSH 执行 Root 工具，完成后再验证入口。']]);
        } catch (\InvalidArgumentException $error) { return $this->invalidResponse($error); }
    }
    public function importCa(Request $request)
    {
        $params = $request->validate(['pem' => 'required|string|max:32768', 'fingerprints' => 'required|array|min:1|max:2',
            'fingerprints.*' => 'required|string|max:95']);
        try {
            $result = Certificates::import($params['pem'], $params['fingerprints']);
            $directory = dirname(Settings::caPath());
            if (!is_dir($directory) && !mkdir($directory, 0700, true)) { throw new \RuntimeException('无法创建 CA 存储目录。'); }
            if (is_link($directory) || is_link(Settings::caPath())) { throw new \RuntimeException('拒绝符号链接存储路径。'); }
            $temporary = tempnam($directory, 'ca-');
            if (!$temporary) { throw new \RuntimeException('无法暂存 CA。'); }
            try {
                chmod($temporary, 0600);
                if (file_put_contents($temporary, $result['pem'], LOCK_EX) === false || !rename($temporary, Settings::caPath())) {
                    throw new \RuntimeException('无法保存 CA。');
                }
            } finally { if (is_file($temporary)) { unlink($temporary); } }
            return response()->json(['data' => $result['certificates']]);
        } catch (\InvalidArgumentException|\RuntimeException $error) { return $this->invalidResponse($error); }
    }
    public function exportCa()
    {
        if (!is_file(Settings::caPath())) { return response()->json(['message' => '尚未导入 CA。'], 404); }
        return response()->download(Settings::caPath(), 'node-endpoint-ca.pem', ['Cache-Control' => 'no-store']);
    }
    public function importStatus(Request $request)
    {
        $params = $request->validate(['status' => 'required|array', 'status.schema_version' => 'required|integer|in:1',
            'status.version' => 'required|string|max:32']);
        $raw = $request->input('status');
        if (strlen(json_encode($raw)) > 32768 || preg_match('/PRIVATE KEY|token|password|secret/i', json_encode($raw))) {
            return response()->json(['message' => '状态报告不得包含私钥、Token 或凭据。'], 422);
        }
        $public = array_intersect_key($raw, array_flip(['schema_version', 'version', 'installed', 'endpoint', 'ca', 'server', 'trust', 'rotation', 'checks', 'generated_at', 'ca_key_online', 'revocation_enforced']));
        Settings::write(array_replace(Settings::read(), ['root_status' => $public, 'status_imported_at' => gmdate('c')]));
        return response()->json(['data' => ['note' => '公共状态快照已导入；快照不等于实时连通性验证。']]);
    }
    public function probe()
    {
        try {
            $tested = Settings::read();
            $result = Probe::run($tested);
            $current = Settings::read();
            if (Endpoint::urls($current)['url'] !== $result['target_url'] || $current['ws_mode'] !== $tested['ws_mode']) {
                throw new \RuntimeException('测试期间设置已变更，请对新地址重新测试。');
            }
            Settings::write(array_replace($current, ['last_probe' => $result]));
            return response()->json(['data' => $result]);
        } catch (\RuntimeException|\InvalidArgumentException $error) { return $this->invalidResponse($error); }
    }
    public function detectIp()
    {
        try {
            $response = Http::connectTimeout(3)->timeout(5)->withoutRedirecting()->get('https://api.ipify.org');
            $ip = trim($response->body());
            if (!$response->successful() || !filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
                throw new \RuntimeException('公网 IP 检测没有返回合法地址。');
            }
            return response()->json(['data' => ['detected_ip' => $ip, 'note' => '仅显示检测结果；不会覆盖已保存地址。']]);
        } catch (\Throwable) { return response()->json(['message' => '公网 IP 检测失败；请手动填写地址。'], 422); }
    }
    public function command(Request $request)
    {
        try {
            $fingerprints = [];
            if (is_file(Settings::caPath()) && extension_loaded('openssl')) {
                preg_match_all('/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/s', file_get_contents(Settings::caPath()), $matches);
                foreach ($matches[0] as $pem) { $fingerprints[] = openssl_x509_fingerprint($pem, 'sha256'); }
            }
            return response()->json(['data' => CommandBuilder::build(Settings::read(), $request->all(), $fingerprints)]);
        } catch (\InvalidArgumentException $error) { return $this->invalidResponse($error); }
    }
}
