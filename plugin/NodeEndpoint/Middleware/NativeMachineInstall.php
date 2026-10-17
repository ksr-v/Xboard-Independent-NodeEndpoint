<?php
namespace Plugin\NodeEndpoint\Middleware;
use App\Models\ServerMachine;
use Closure;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Auth;
use Plugin\NodeEndpoint\Services\{Settings, MachineInstallCommand};
final class NativeMachineInstall
{
    public function handle($request, Closure $next)
    {
        $action = ltrim($request->route()?->getActionName() ?? '', '\\');
        $prefix = 'App\\Http\\Controllers\\V2\\Admin\\Server\\MachineController@';
        $create = $action === $prefix . 'save' && !$request->filled('id');
        if ((!$create && $action !== $prefix . 'installCommand')
            || !Settings::pluginEnabled() || !(Auth::guard('sanctum')->user()?->is_admin)) {
            return $next($request);
        }
        $settings = Settings::read();
        if (!($settings['enabled'] ?? false)) { return $next($request); }
        try {
            // Validate before a machine is created, avoiding a partial success.
            $context = MachineInstallCommand::context($settings);
        } catch (\Throwable) {
            return response()->json(['message' => '独立入口的 HTTPS / 公共 CA 尚未就绪，请保存配置并同步 CA 后重试。'], 422);
        }
        $response = $next($request);
        if (!$response instanceof JsonResponse || $response->getStatusCode() !== 200) { return $response; }
        $data = $response->getData(true);
        $key = $create ? 'install_command' : 'command';
        if (!is_array($data['data'] ?? null) || !is_string($data['data'][$key] ?? null)) { return $response; }
        $id = $create ? ($data['data']['id'] ?? 0) : $request->input('id');
        $machine = ServerMachine::find((int) $id);
        if (!$machine) { return $response; }
        $data['data'][$key] = MachineInstallCommand::build($context, (int) $machine->id, (string) $machine->token);
        $response->setData($data);
        $response->headers->set('Cache-Control', 'no-store');
        return $response;
    }
}
