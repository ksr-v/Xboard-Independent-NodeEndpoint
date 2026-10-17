<?php
namespace Plugin\NodeEndpoint;
use App\Services\Plugin\AbstractPlugin;
use Plugin\NodeEndpoint\Middleware\ScopedHandshake;
class Plugin extends AbstractPlugin
{
    public function boot(): void
    {
        $router = app('router');
        if (!in_array(ScopedHandshake::class, $router->getMiddlewareGroups()['api'] ?? [], true)) {
            $router->pushMiddlewareToGroup('api', ScopedHandshake::class);
        }
    }
    public function install(): void {}
    public function update(string $oldVersion, string $newVersion): void {}
    public function cleanup(): void
    {
        // Also called before upgrade. Preserve Root-owned entry, PKI and all other plugins.
    }
}
