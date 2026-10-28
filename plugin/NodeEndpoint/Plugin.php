<?php
namespace Plugin\NodeEndpoint;
use App\Services\Plugin\AbstractPlugin;
use Plugin\NodeEndpoint\Middleware\ScopedHandshake;
use Plugin\NodeEndpoint\Middleware\NativeMachineInstall;
use Plugin\NodeEndpoint\Services\AdminIntegration;
class Plugin extends AbstractPlugin
{
    public const VERSION = '1.0.10';
    public function boot(): void
    {
        app(AdminIntegration::class)->register();
        $router = app('router');
        foreach ([ScopedHandshake::class, NativeMachineInstall::class] as $middleware) {
            if (!in_array($middleware, $router->getMiddlewareGroups()['api'] ?? [], true)) {
                $router->pushMiddlewareToGroup('api', $middleware);
            }
        }
    }
    public function install(): void {}
    public function update(string $oldVersion, string $newVersion): void {}
    public function cleanup(): void
    {
        // Also called before upgrade. Preserve Root-owned entry, PKI and all other plugins.
    }
}
