<?php
namespace Plugin\NodeEndpoint\Services;
use App\Models\Plugin;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
final class Settings
{
    public static function pluginEnabled(): bool
    {
        return (bool) Plugin::where('code', 'node_endpoint')->value('is_enabled');
    }
    public static function read(): array
    {
        if (!Schema::hasTable('node_endpoint_settings')) { return Endpoint::DEFAULTS; }
        $raw = DB::table('node_endpoint_settings')->where('id', 1)->value('settings');
        $data = $raw ? json_decode($raw, true, 32, JSON_THROW_ON_ERROR) : [];
        return array_replace(Endpoint::DEFAULTS, $data);
    }
    public static function write(array $data): void
    {
        DB::table('node_endpoint_settings')->updateOrInsert(['id' => 1], [
            'settings' => json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES),
            'updated_at' => now(), 'created_at' => now(),
        ]);
    }
    public static function caPath(): string { return storage_path('app/node_endpoint/trust-bundle.pem'); }
}
