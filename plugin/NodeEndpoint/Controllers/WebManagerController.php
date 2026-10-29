<?php
declare(strict_types=1);

namespace Plugin\NodeEndpoint\Controllers;

use App\Http\Controllers\Controller;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\{Auth, Cache, DB};
use Plugin\NodeEndpoint\Services\{Certificates, Endpoint, RootCommandBuilder, Settings, WebManager, WebManagerException};

final class WebManagerController extends Controller
{
    private const TTL = 300;
    private const JOBS = 'node_endpoint_web_jobs';
    private const HEADERS = ['Cache-Control' => 'no-store, private', 'Pragma' => 'no-cache', 'Expires' => '0'];

    public function __construct(private readonly WebManager $manager) {}

    private function respond(callable $body)
    {
        try { return response()->json(['data' => $body()], 200, self::HEADERS); }
        catch (WebManagerException $error) {
            return response()->json(['error' => ['code' => $error->errorCode, 'message' => $error->getMessage()],
                'message' => $error->getMessage()], $error->httpStatus, self::HEADERS);
        } catch (\Throwable) {
            $error = WebManager::failure('helper_operation_failed', 503);
            return response()->json(['error' => ['code' => $error->errorCode, 'message' => $error->getMessage()],
                'message' => $error->getMessage()], $error->httpStatus, self::HEADERS);
        }
    }

    private function owner(Request $request, bool $mutation = false): string
    {
        // Revalidate on every request; the native admin middleware remains mandatory too.
        $user = Auth::guard('sanctum')->user();
        $accessToken = $user && method_exists($user, 'currentAccessToken') ? $user->currentAccessToken() : null;
        if (!$request->bearerToken() || !$user || !$user->is_admin || $user->getAuthIdentifier() === null
            || !($accessToken instanceof \Laravel\Sanctum\PersonalAccessToken)) {
            throw WebManager::failure('unauthorized', 403);
        }
        if ($mutation && (!$request->isSecure() || !is_string($request->header('Origin'))
            || $request->header('Origin') !== $request->getSchemeAndHttpHost())) {
            throw WebManager::failure('secure_origin_required', 403);
        }
        return (string) $user->getAuthIdentifier();
    }

    private function exact(array $input, array $keys): void
    {
        $actual = array_keys($input);
        sort($actual); sort($keys);
        if ($actual !== $keys) { throw WebManager::failure('invalid_request'); }
    }

    private function saved(): array
    {
        try { $saved = Endpoint::validate(Settings::read()); }
        catch (\Throwable) { throw WebManager::failure('invalid_settings'); }
        if (!$saved['enabled'] || !$saved['https'] || $saved['host'] === '') { throw WebManager::failure('invalid_settings'); }
        return $saved;
    }

    private function revision(): string
    {
        return hash('sha256', json_encode(Settings::read(), JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES));
    }

    private function matches(array $target, array $saved, bool $withMode = false): bool
    {
        return $target['host'] === $saved['host'] && $target['port'] === $saved['port'] && $target['https'] === true
            && (!$withMode || $target['ws_mode'] === $saved['ws_mode']);
    }

    private function key(string $kind, string $owner, string $id): string
    {
        return 'ne-web-manager:' . $kind . ':' . hash('sha256', $owner) . ':' . $id;
    }

    private function ownedJobs(string $owner)
    {
        return DB::table(self::JOBS)->where('admin_id', $owner)->where('created_at', '>=', now()->subDays(30));
    }

    private function pruneJobs(string $owner): void
    {
        DB::table(self::JOBS)->where('admin_id', $owner)->where('created_at', '<', now()->subDays(30))->delete();
        $keep = DB::table(self::JOBS)->where('admin_id', $owner)->orderByDesc('id')->limit(100)->pluck('id');
        if ($keep->count() === 100) { DB::table(self::JOBS)->where('admin_id', $owner)->whereNotIn('id', $keep)->delete(); }
    }

    private function bindJob(string $owner, string $id, string $action): void
    {
        $existing = DB::table(self::JOBS)->where('job_id', $id)->first();
        if ($existing) {
            if ((string) $existing->admin_id !== $owner || $existing->action !== $action) {
                throw WebManager::failure('not_found', 404);
            }
            return;
        }
        DB::table(self::JOBS)->insert(['job_id' => $id, 'admin_id' => $owner, 'action' => $action,
            'state' => 'submitted', 'created_at' => now(), 'updated_at' => now()]);
    }

    private function statusData(): array
    {
        $raw = $this->manager->request('status');
        if (($raw['available'] ?? null) !== true || !is_string($raw['version'] ?? null)
            || strlen($raw['version']) > 32 || !is_array($raw['policy'] ?? null)) { throw WebManager::failure('helper_protocol', 502); }
        $policy = $raw['policy'];
        if (!is_string($policy['app'] ?? null) || strlen($policy['app']) > 4096
            || !is_string($policy['app_user'] ?? null) || !preg_match('/^[a-z_][a-z0-9_-]{0,63}$/iD', $policy['app_user'])
            || !is_array($policy['allowed_ports'] ?? null) || count($policy['allowed_ports']) > 64) { throw WebManager::failure('helper_protocol', 502); }
        foreach ($policy['allowed_ports'] as $port) {
            if (!is_int($port) || $port < 1 || $port > 65535) { throw WebManager::failure('helper_protocol', 502); }
        }
        $out = ['available' => true, 'version' => $raw['version'], 'policy' => array_intersect_key($policy,
            array_flip(['app', 'app_user', 'allowed_ports'])), 'root_status' => null, 'ca' => null, 'active_job' => null];
        if (($raw['root_status'] ?? null) !== null) { $out['root_status'] = WebManager::publicStatus($raw['root_status']); }
        if (($raw['ca'] ?? null) !== null) {
            $ca = $raw['ca'];
            if (!is_array($ca) || !is_string($ca['pem'] ?? null) || strlen($ca['pem']) > 32768
                || !is_array($ca['fingerprints'] ?? null) || !array_is_list($ca['fingerprints'])
                || count($ca['fingerprints']) < 1 || count($ca['fingerprints']) > 2) { throw WebManager::failure('helper_protocol', 502); }
            foreach ($ca['fingerprints'] as $fingerprint) {
                if (!is_string($fingerprint) || !preg_match('/^[a-f0-9]{64}$/D', $fingerprint)) { throw WebManager::failure('helper_protocol', 502); }
            }
            if (str_contains($ca['pem'], 'PRIVATE KEY')) { throw WebManager::failure('helper_protocol', 502); }
            $out['ca'] = ['pem' => $ca['pem'], 'fingerprints' => $ca['fingerprints']];
        }
        if (($raw['active_job'] ?? null) !== null) {
            if (!WebManager::identifier($raw['active_job'])) { throw WebManager::failure('helper_protocol', 502); }
            $out['active_job'] = $raw['active_job'];
        }
        return $out;
    }

    public function status(Request $request)
    {
        return $this->respond(function () use ($request): array {
            $owner = $this->owner($request);
            $this->pruneJobs($owner);
            $last = $this->ownedJobs($owner)->where('state', '<>', 'missing')->orderByDesc('id')->value('job_id');
            try { $data = $this->statusData(); }
            catch (WebManagerException $error) {
                if ($error->errorCode !== 'helper_unavailable') { throw $error; }
                $tool = RootCommandBuilder::build(Settings::read());
                return ['available' => false, 'code' => 'helper_unavailable',
                    'guidance' => '请先通过 SSH 安装并启用 Root Web 管理助手。',
                    'initialization_command' => $tool['web_manager_install_command'] ?? null, 'active_job' => null, 'last_job' => $last];
            }
            $data['busy'] = $data['active_job'] !== null;
            if ($data['active_job'] !== null && !$this->ownedJobs($owner)->where('job_id', $data['active_job'])->exists()) {
                $data['active_job'] = null;
            }
            $data['last_job'] = $last;
            return $data;
        });
    }

    public function plan(Request $request)
    {
        return $this->respond(function () use ($request): array {
            $owner = $this->owner($request, true);
            $input = $request->all(); $this->exact($input, ['action']);
            if (!in_array($input['action'], ['configure', 'refresh', 'renew', 'firewall'], true)) { throw WebManager::failure('invalid_request'); }
            [$saved, $revision] = DB::transaction(function (): array {
                DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
                return [$this->saved(), $this->revision()];
            });
            $parameters = ['action' => $input['action']];
            if ($input['action'] === 'configure') {
                $parameters += array_intersect_key($saved, array_flip(['host', 'port', 'ws_mode']));
            }
            // Root's fixed CLI bridge locks this same row. No application transaction may cross the RPC.
            $raw = $this->manager->request('plan', $parameters);
            return DB::transaction(function () use ($owner, $input, $saved, $revision, $raw): array {
                DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
                if (!hash_equals($revision, $this->revision())) { throw WebManager::failure('settings_changed', 409); }
                $target = WebManager::target($raw['target'] ?? null, true);
                if (!$this->matches($target, $saved, true)) { throw WebManager::failure('target_mismatch', 409); }
                $expires = $raw['expires_at'] ?? null;
                $date = null;
                if (is_string($expires) && preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/D', $expires)) {
                    try { $date = new \DateTimeImmutable($expires); }
                    catch (\Throwable) {}
                    $errors = \DateTimeImmutable::getLastErrors();
                    if ($errors && ($errors['warning_count'] || $errors['error_count'])) { $date = null; }
                }
                if (!WebManager::identifier($raw['id'] ?? null) || !is_string($raw['digest'] ?? null)
                    || !preg_match('/^[a-f0-9]{64}$/D', $raw['digest']) || ($raw['action'] ?? null) !== $input['action']
                    || !$date || $date->getTimestamp() <= time() || $date->getTimestamp() > time() + self::TTL + 5
                    || !is_string($raw['confirmation'] ?? null) || strlen($raw['confirmation']) < 1 || strlen($raw['confirmation']) > 320
                    || preg_match('/[\x00-\x1f\x7f]/', $raw['confirmation'])) { throw WebManager::failure('helper_protocol', 502); }
                $plan = ['id' => $raw['id'], 'digest' => $raw['digest'], 'action' => $raw['action'], 'expires_at' => $expires,
                    'confirmation' => $raw['confirmation'], 'target' => $target, 'stages' => WebManager::stages($raw['stages'] ?? null),
                    'current' => isset($raw['current']) ? WebManager::publicStatus($raw['current']) : null];
                if (($plan['current']['installed'] ?? false) === true) { $plan['ca_preserved'] = true; }
                if (!Cache::put($this->key('plan', $owner, $plan['id']), ['plan' => $plan, 'revision' => $revision,
                    'expires' => min($date->getTimestamp(), time() + self::TTL), 'job' => null], self::TTL)) {
                    throw WebManager::failure('owner_storage_failed', 503);
                }
                return $plan;
            });
        });
    }

    public function execute(Request $request)
    {
        return $this->respond(function () use ($request): array {
            $owner = $this->owner($request, true);
            $input = $request->all(); $this->exact($input, ['plan_id', 'digest', 'confirmation']);
            if (!WebManager::identifier($input['plan_id']) || !is_string($input['digest'])
                || !preg_match('/^[a-f0-9]{64}$/D', $input['digest']) || !is_string($input['confirmation'])
                || strlen($input['confirmation']) > 320) { throw WebManager::failure('invalid_request'); }
            $key = $this->key('plan', $owner, $input['plan_id']);
            // Commit ownership before contacting Root: an unknown response must not roll the binding back.
            $record = DB::transaction(function () use ($owner, $input, $key): array {
                DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
                $record = Cache::get($key);
                if (!is_array($record)) { throw WebManager::failure('not_found', 404); }
                $plan = $record['plan'];
                if (!hash_equals($plan['digest'], $input['digest'])) { throw WebManager::failure('plan_mismatch', 409); }
                if (!hash_equals($plan['confirmation'], $input['confirmation'])) { throw WebManager::failure('confirmation_mismatch'); }
                if (!is_array($record['job'])) {
                    if ($record['expires'] <= time()) { throw WebManager::failure('plan_expired', 409); }
                    if (!hash_equals($record['revision'], $this->revision()) || !$this->matches($plan['target'], $this->saved(), true)) {
                        throw WebManager::failure('settings_changed', 409);
                    }
                }
                // The helper durably uses the plan ID as its job ID, including after an interrupted response.
                $this->bindJob($owner, $plan['id'], $plan['action']);
                $this->pruneJobs($owner);
                return $record;
            });
            if (is_array($record['job'])) { return $record['job']; }
            $existing = DB::transaction(function () use ($key, $record): ?array {
                // Recheck after prebinding commits, then release the row before Root's own CLI reads it.
                DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
                $plan = $record['plan'];
                $latest = Cache::get($key);
                if (is_array($latest) && is_array($latest['job'])) { return $latest['job']; }
                if ($record['expires'] <= time()) { throw WebManager::failure('plan_expired', 409); }
                if (!hash_equals($record['revision'], $this->revision()) || !$this->matches($plan['target'], $this->saved(), true)) {
                    throw WebManager::failure('settings_changed', 409);
                }
                return null;
            });
            if ($existing !== null) { return $existing; }
            // Root revalidates the reviewed settings and state digest in its worker before any mutation.
            $raw = $this->manager->request('execute', $input);
            return DB::transaction(function () use ($owner, $key, $record, $raw): array {
                $plan = $record['plan'];
                if (($raw['id'] ?? null) !== $plan['id']
                    || !in_array($raw['state'] ?? null, ['queued', 'running', 'succeeded', 'partial', 'failed', 'interrupted'], true)) {
                    throw WebManager::failure('helper_protocol', 502);
                }
                $job = ['id' => $raw['id'], 'state' => $raw['state']];
                $this->ownedJobs($owner)->where('job_id', $job['id'])->update(['state' => $job['state'], 'updated_at' => now()]);
                $record['job'] = $job; Cache::put($key, $record, self::TTL);
                return $job;
            });
        });
    }

    public function job(Request $request, string $id)
    {
        return $this->respond(function () use ($request, $id): array {
            $owner = $this->owner($request);
            if (!WebManager::identifier($id)) { throw WebManager::failure('invalid_request'); }
            $binding = $this->ownedJobs($owner)->where('job_id', $id)->first();
            if (!$binding) { throw WebManager::failure('not_found', 404); }
            try { $raw = $this->manager->request('job', ['id' => $id]); }
            catch (WebManagerException $error) {
                if ($error->errorCode === 'not_found') {
                    $this->ownedJobs($owner)->where('job_id', $id)->update(['state' => 'missing', 'updated_at' => now()]);
                }
                throw $error;
            }
            if (($raw['id'] ?? null) !== $id || ($raw['action'] ?? null) !== $binding->action
                || !in_array($raw['state'] ?? null, ['queued', 'running', 'succeeded', 'partial', 'failed', 'interrupted'], true)) {
                throw WebManager::failure('helper_protocol', 502);
            }
            $data = ['id' => $id, 'action' => $raw['action'], 'state' => $raw['state'], 'stages' => WebManager::stages($raw['stages'] ?? null)];
            if (isset($raw['result']) && is_array($raw['result'])) {
                $result = $raw['result']; $data['result'] = [];
                if (isset($result['root_status'])) { $data['result']['root_status'] = WebManager::publicStatus($result['root_status']); }
                if (isset($result['endpoint'])) { $data['result']['endpoint'] = WebManager::target($result['endpoint'], true); }
                if (isset($result['backup_id']) && is_string($result['backup_id']) && preg_match('/^[A-Za-z0-9_.-]{1,128}$/D', $result['backup_id'])) { $data['result']['backup_id'] = $result['backup_id']; }
                if (isset($result['ca_preserved']) && is_bool($result['ca_preserved'])) { $data['result']['ca_preserved'] = $result['ca_preserved']; }
                // Dynamic helper output, logs and paths are deliberately excluded.
                foreach (['firewall_result', 'ca_sync'] as $field) {
                    if (is_array($result[$field] ?? null) && is_string($result[$field]['status'] ?? null)
                        && preg_match('/^[a-z][a-z0-9_]{0,63}$/D', $result[$field]['status'])) {
                        $data['result'][$field] = ['status' => $result[$field]['status']];
                        if ($field === 'firewall_result' && is_int($result[$field]['port'] ?? null)
                            && $result[$field]['port'] > 0 && $result[$field]['port'] <= 65535) {
                            $data['result'][$field]['port'] = $result[$field]['port'];
                        }
                        if ($field === 'ca_sync' && is_int($result[$field]['certificates'] ?? null)
                            && $result[$field]['certificates'] >= 1 && $result[$field]['certificates'] <= 2
                            && is_array($result[$field]['fingerprints'] ?? null) && array_is_list($result[$field]['fingerprints'])
                            && count($result[$field]['fingerprints']) === $result[$field]['certificates']) {
                            $pins = $result[$field]['fingerprints'];
                            if (count(array_filter($pins, fn ($pin) => is_string($pin) && preg_match('/^[a-f0-9]{64}$/D', $pin))) === count($pins)) {
                                $data['result'][$field]['certificates'] = count($pins);
                                $data['result'][$field]['fingerprints'] = $pins;
                            }
                        }
                    }
                }
            }
            if (isset($raw['error'])) { $data['error'] = ['code' => 'helper_operation_failed', 'message' => 'Root 任务未完成，请根据阶段状态检查并重新生成计划。']; }
            $this->ownedJobs($owner)->where('job_id', $id)->update(['state' => $data['state'], 'updated_at' => now()]);
            return $data;
        });
    }

    public function syncCa(Request $request)
    {
        return $this->respond(function () use ($request): array {
            $this->owner($request, true); $this->exact($request->all(), []);
            // Root may be waiting for the application's CLI bridge, so never hold a database lock here.
            $data = $this->statusData();
            $directory = dirname(Settings::caPath()); $previous = null; $replaced = false;
            try { return DB::transaction(function () use ($data, $directory, &$previous, &$replaced): array {
                DB::table('node_endpoint_settings')->where('id', 1)->lockForUpdate()->first();
                $saved = $this->saved();
                if (!is_array($data['root_status']) || !isset($data['root_status']['endpoint'])
                    || !$this->matches($data['root_status']['endpoint'], $saved)) { throw WebManager::failure('target_mismatch', 409); }
                if (!is_array($data['ca'])) { throw WebManager::failure('ca_unavailable'); }
                try { $import = Certificates::import($data['ca']['pem'], $data['ca']['fingerprints']); }
                catch (\Throwable) { throw WebManager::failure('ca_unavailable'); }
                $temporary = null;
                try {
                    if (!is_dir($directory) && !@mkdir($directory, 0700, true)) { throw WebManager::failure('storage_failed', 503); }
                    if (is_link($directory) || is_link(Settings::caPath())) { throw WebManager::failure('storage_failed', 503); }
                    if (is_file(Settings::caPath())) {
                        if (filesize(Settings::caPath()) > 32768) { throw WebManager::failure('storage_failed', 503); }
                        $previous = file_get_contents(Settings::caPath());
                        if (!is_string($previous)) { throw WebManager::failure('storage_failed', 503); }
                    }
                    $temporary = tempnam($directory, 'ca-');
                    if (!$temporary || !@chmod($temporary, 0600)
                        || file_put_contents($temporary, $import['pem'], LOCK_EX) !== strlen($import['pem'])
                        || !@rename($temporary, Settings::caPath())) { throw WebManager::failure('storage_failed', 503); }
                    $replaced = true;
                    Settings::write(array_replace(Settings::read(), ['root_status' => $data['root_status'], 'status_imported_at' => gmdate('c')]));
                } finally { if (is_string($temporary) && is_file($temporary)) { @unlink($temporary); } }
                return ['certificates' => $import['certificates'], 'root_status' => $data['root_status'], 'status_imported_at' => Settings::read()['status_imported_at']];
            }); } catch (\Throwable $error) {
                // Cover both the update and transaction commit, keeping the previous trust bundle on failure.
                try {
                    $connection = DB::connection();
                    if ($connection->transactionLevel() === 0 && $connection->getPdo()->inTransaction()) {
                        $connection->getPdo()->rollBack();
                    }
                } catch (\Throwable) {}
                if ($replaced) {
                    if ($previous === null) { @unlink(Settings::caPath()); }
                    else {
                        $rollback = @tempnam($directory, 'ca-rollback-');
                        if ($rollback) {
                            @chmod($rollback, 0600);
                            if (@file_put_contents($rollback, $previous, LOCK_EX) !== false) { @rename($rollback, Settings::caPath()); }
                            if (is_file($rollback)) { @unlink($rollback); }
                        }
                    }
                }
                if ($error instanceof WebManagerException) { throw $error; }
                throw WebManager::failure('storage_failed', 503);
            }
        });
    }
}
