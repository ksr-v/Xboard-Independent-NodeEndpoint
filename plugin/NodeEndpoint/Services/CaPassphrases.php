<?php
namespace Plugin\NodeEndpoint\Services;

use Illuminate\Support\Facades\{Crypt, DB, Schema};
use RuntimeException;

final class CaPassphrases
{
    private const TABLE = 'node_endpoint_ca_credentials';

    private static function unavailable(): RuntimeException
    {
        return new RuntimeException('CA credential unavailable');
    }
    private static function id(string $id): void
    {
        if (!preg_match('/^[a-f0-9]{32}$/D', $id)) { throw self::unavailable(); }
    }
    private static function host(string $host): string
    {
        $host = Endpoint::host($host);
        if ($host === '') { throw self::unavailable(); }
        return $host;
    }
    private static function fingerprint(string $fingerprint): void
    {
        if (!preg_match('/^[a-f0-9]{64}$/D', $fingerprint)) { throw self::unavailable(); }
    }
    private static function secret(object $row): string
    {
        $secret = Crypt::decryptString($row->ciphertext);
        if (!preg_match('/^[A-Za-z0-9_-]{64}$/D', $secret)) { throw self::unavailable(); }
        return $secret;
    }
    public static function prepare(string $id, string $host): string
    {
        try {
            self::id($id); $host = self::host($host);
            try {
                return DB::transaction(function () use ($id, $host): string {
                    $row = DB::table(self::TABLE)->where('id', $id)->lockForUpdate()->first();
                    if ($row) {
                        if (!hash_equals($row->host, $host)) { throw self::unavailable(); }
                        return self::secret($row);
                    }
                    $secret = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
                    DB::table(self::TABLE)->insert(['id' => $id, 'host' => $host, 'fingerprint' => null,
                        'ciphertext' => Crypt::encryptString($secret), 'created_at' => now(), 'updated_at' => now()]);
                    return $secret;
                }, 3);
            } catch (\Illuminate\Database\QueryException $error) {
                // A concurrent prepare may have won the primary-key insert.
                // Reuse that exact credential; never replace its ciphertext.
                $row = DB::table(self::TABLE)->where('id', $id)->first();
                if (!$row || !hash_equals($row->host, $host)) { throw self::unavailable(); }
                return self::secret($row);
            }
        } catch (\Throwable) { throw self::unavailable(); }
    }
    public static function get(string $id): string
    {
        try {
            self::id($id);
            $row = DB::table(self::TABLE)->where('id', $id)->first();
            if (!$row) { throw self::unavailable(); }
            return self::secret($row);
        } catch (\Throwable) { throw self::unavailable(); }
    }
    public static function matchesHost(string $id, string $host): bool
    {
        try {
            self::id($id); $host = self::host($host);
            $actual = DB::table(self::TABLE)->where('id', $id)->value('host');
            return is_string($actual) && hash_equals($actual, $host);
        } catch (\Throwable) { return false; }
    }
    public static function bind(string $id, string $host, string $fingerprint): void
    {
        try {
            self::id($id); $host = self::host($host); self::fingerprint($fingerprint);
            DB::transaction(function () use ($id, $host, $fingerprint): void {
                $row = DB::table(self::TABLE)->where('id', $id)->lockForUpdate()->first();
                if (!$row || !hash_equals($row->host, $host)
                    || ($row->fingerprint !== null && !hash_equals($row->fingerprint, $fingerprint))) {
                    throw self::unavailable();
                }
                self::secret($row); // Refuse to bind an unreadable or corrupted credential.
                if (DB::table(self::TABLE)->where('fingerprint', $fingerprint)->where('id', '<>', $id)->exists()) {
                    throw self::unavailable();
                }
                if ($row->fingerprint === null) {
                    DB::table(self::TABLE)->where('id', $id)->whereNull('fingerprint')
                        ->update(['fingerprint' => $fingerprint, 'updated_at' => now()]);
                }
            }, 3);
        } catch (\Throwable) { throw self::unavailable(); }
    }
    public static function reveal(string $fingerprint): string
    {
        try {
            self::fingerprint($fingerprint);
            $row = DB::table(self::TABLE)->where('fingerprint', $fingerprint)->first();
            if (!$row) { throw self::unavailable(); }
            return self::secret($row);
        } catch (\Throwable) { throw self::unavailable(); }
    }
    public static function metadata(array $currentFingerprints): array
    {
        $known = [];
        $pins = array_values(array_unique(array_filter($currentFingerprints, fn ($pin) =>
            is_string($pin) && preg_match('/^[a-f0-9]{64}$/D', $pin))));
        try {
            if ($pins && Schema::hasTable(self::TABLE)) {
                // Ordinary settings reads never select or decrypt ciphertext.
                $known = DB::table(self::TABLE)->whereIn('fingerprint', $pins)->pluck('fingerprint')->all();
            }
        } catch (\Throwable) { /* Older deployments stay usable before migration. */ }
        return array_map(fn ($pin) => ['fingerprint_sha256' => $pin, 'available' => in_array($pin, $known, true)], $pins);
    }
}
