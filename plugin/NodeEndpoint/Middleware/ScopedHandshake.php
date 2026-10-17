<?php
namespace Plugin\NodeEndpoint\Middleware;
use Closure;
use Illuminate\Http\JsonResponse;
use Plugin\NodeEndpoint\Services\Endpoint;
use Plugin\NodeEndpoint\Services\Settings;
final class ScopedHandshake
{
    public function handle($request, Closure $next)
    {
        $response = $next($request);
        $route = $request->route();
        if (!$route || ltrim($route->getActionName(), '\\') !== 'App\\Http\\Controllers\\V2\\Server\\ServerController@handshake'
            || $request->path() !== 'api/v2/server/handshake'
            || !$response instanceof JsonResponse || $response->getStatusCode() !== 200
            || !Settings::pluginEnabled()) { return $response; }
        try { $settings = Endpoint::validate(Settings::read()); }
        catch (\Throwable) { return $response; }
        if (!Endpoint::matches($settings, $request->getHost(), $request->getPort(), $request->isSecure())) { return $response; }
        $data = $response->getData(true);
        if (!is_array($data) || !isset($data['websocket']) || !is_array($data['websocket'])) { return $response; }
        if ($settings['ws_mode'] === 'rest') { $data['websocket'] = ['enabled' => false]; }
        elseif (($data['websocket']['enabled'] ?? false) === true) {
            $data['websocket']['ws_url'] = Endpoint::urls($settings)['ws_url'];
        }
        $response->setData($data);
        return $response;
    }
}
