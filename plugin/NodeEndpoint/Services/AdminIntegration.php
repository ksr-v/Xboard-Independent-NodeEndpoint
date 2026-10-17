<?php
namespace Plugin\NodeEndpoint\Services;
use Illuminate\View\View;
use Plugin\NodeEndpoint\Plugin;
final class AdminIntegration
{
    private const EVENT = 'composing: admin';
    private const LISTENER = self::class . '@compose';
    public function register(): void
    {
        $events = app('events');
        if (!in_array(self::LISTENER, $events->getRawListeners()[self::EVENT] ?? [], true)) {
            $events->listen(self::EVENT, self::LISTENER);
        }
    }
    public function compose(View $view): void
    {
        if (!Settings::pluginEnabled()) { return; }
        $wrapper = dirname(__DIR__) . '/resources/views/admin.blade.php';
        $original = $view->getPath();
        if ($original === $wrapper || !is_file($original) || !is_file($wrapper)) { return; }
        // Preserve the current template and data, including other plugins' composers.
        $data = $view->getData();
        $view->with('__nodeEndpointOriginalPath', $original);
        $view->with('__nodeEndpointOriginalData', $data);
        $view->setPath($wrapper);
    }
    public static function inject(string $html): string
    {
        if (!Settings::pluginEnabled() || str_contains($html, 'data-ne-install-loader')) { return $html; }
        $position = strripos($html, '</body>');
        if ($position === false) { return $html; }
        $version = rawurlencode(Plugin::VERSION);
        $assets = "\n" . '<link data-ne-install-style rel="stylesheet" href="/node-endpoint/assets/native-style?v=' . $version . '">'
            . "\n" . '<script data-ne-install-loader defer src="/node-endpoint/assets/native-script?v=' . $version . '"></script>' . "\n";
        return substr($html, 0, $position) . $assets . substr($html, $position);
    }
}
