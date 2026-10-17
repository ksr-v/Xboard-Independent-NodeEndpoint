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
        $response = $next($request);
        if (!$response instanceof JsonResponse || $response->getStatusCode() !== 200) { return $response; }
        $data = $response->getData(true);
        $key = $create ? 'install_command' : 'command';
        if (!is_array($data['data'] ?? null) || !is_string($data['data'][$key] ?? null)) { return $response; }
        $id = $create ? ($data['data']['id'] ?? 0) : $request->input('id');
        $machine = ServerMachine::find((int) $id);
        if (!$machine) { return $response; }
        $panelUrl = rtrim((string) (admin_setting('app_url') ?: $request->getSchemeAndHttpHost()), '/');
        $data['data'][$key] = MachineInstallCommand::buildDomain($panelUrl, (int) $machine->id, (string) $machine->token);
        $options = ['owner' => 'node_endpoint', 'schema' => 1,
            'domain' => ['available' => true, 'command' => $data['data'][$key]],
            'endpoint' => ['available' => false, 'message' => '独立 IP 对接尚未就绪，请启用 HTTPS 独立入口并同步公共 CA。']];
        try {
            // Private-CA readiness must never prevent domain installation or machine creation.
            $context = MachineInstallCommand::context(Settings::read());
            $options['endpoint'] = ['available' => true,
                'command' => MachineInstallCommand::build($context, (int) $machine->id, (string) $machine->token)];
        } catch (\Throwable) {
            // Keep domain installation available and expose only a fixed readiness message.
        }
        $data['data']['install_options'] = $options;
        $response->setData($data);
        $response->headers->set('Cache-Control', 'no-store');
        return $response;
    }
}
