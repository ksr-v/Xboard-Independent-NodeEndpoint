<?php
namespace Plugin\NodeEndpoint\Middleware;
use Closure;
use Plugin\NodeEndpoint\Services\Settings;
final class Enabled
{
    public function handle($request, Closure $next)
    {
        if (!Settings::pluginEnabled()) { return response()->json(['message' => 'NodeEndpoint 未启用。'], 404); }
        return $next($request);
    }
}
