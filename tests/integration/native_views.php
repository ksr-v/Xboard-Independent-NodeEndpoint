<?php
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';
use App\Services\Plugin\PluginManager;
use Plugin\NodeEndpoint\Services\AdminIntegration;
$checks = [];
function viewCheck(string $label, callable $body): void {
    global $checks; $body(); $checks[]=$label; echo 'PASS ' . $label . PHP_EOL;
}
$manager=app(PluginManager::class);
$manager->install('node_endpoint'); $manager->enable('node_endpoint');
viewCheck('repeated boot registers a single native Admin composer', function (): void {
    app(AdminIntegration::class)->register(); app(AdminIntegration::class)->register();
    eq(count(array_filter(app('events')->getRawListeners()['composing: admin'] ?? [],
        fn ($v) => $v === AdminIntegration::class . '@compose')),1);
});
viewCheck('dynamic native choice resources and minimal public status are available', function (): void {
    foreach (['native-script'=>'text/javascript','native-style'=>'text/css'] as $asset=>$mime) {
        $r=requestFixture('https://old.panel.example/node-endpoint/assets/'.$asset);
        eq($r->getStatusCode(),200);
        ok(str_starts_with($r->headers->get('Content-Type'),$mime),'Wrong asset content type.');
    }
    $r=requestFixture('https://old.panel.example/node-endpoint/status');
    eq(jsonResponse($r),['enabled'=>true]);
    ok(str_contains($r->headers->get('Cache-Control'),'no-store'),'Status cached.');
    eq(requestFixture('https://old.panel.example/node-endpoint/assets/unknown')->getStatusCode(),404);
});
viewCheck('native Admin template injects one dynamic asset pair without affecting independent UI', function (): void {
    $r=requestFixture('https://old.panel.example/fixture-admin');
    eq($r->getStatusCode(),200);
    eq(substr_count($r->getContent(),'data-ne-install-loader'),1);
    eq(substr_count($r->getContent(),'data-ne-install-style'),1);
    $page=requestFixture('https://old.panel.example/node-endpoint');
    ok(!str_contains(file_get_contents($page->getFile()->getPathname()),'data-ne-install-loader'),'Injected into independent page.');
});
viewCheck('disabled plugin restores original Admin HTML and gates dynamic choice assets', function () use ($manager): void {
    $manager->disable('node_endpoint');
    try {
        eq(jsonResponse(requestFixture('https://old.panel.example/node-endpoint/status')),['enabled'=>false]);
        ok(!str_contains(requestFixture('https://old.panel.example/fixture-admin')->getContent(),'data-ne-install-loader'),'Disabled injection persists.');
        foreach (['native-script','native-style'] as $asset) eq(requestFixture('https://old.panel.example/node-endpoint/assets/'.$asset)->getStatusCode(),404);
    } finally {$manager->enable('node_endpoint');}
});
foreach (['smart_expiry','qrcodeextend','flowscope'] as $code) {$manager->install($code);$manager->enable($code);}
$bundle=public_path('assets/admin/assets/index-CEIYH7i8.js');
$before=hash_file('sha256',$bundle);
foreach ([[AdminIntegration::class,Plugin\Flowscope\Services\AdminIntegration::class],
          [Plugin\Flowscope\Services\AdminIntegration::class,AdminIntegration::class]] as $index=>$order) {
    viewCheck('foreign Admin composers coexist in order '.($index+1), function () use ($order,$bundle,$before): void {
        app('events')->forget('composing: admin');
        foreach ($order as $class) app($class)->register();
        $html=requestFixture('https://old.panel.example/fixture-admin')->getContent();
        eq(substr_count($html,'data-ne-install-loader'),1);
        eq(substr_count($html,'data-flowscope-loader'),1);
        eq(hash_file('sha256',$bundle),$before);
        eq(file_get_contents(storage_path('app/node_endpoint/root-owned-evidence.txt')),'root-owned: preserve');
    });
}
echo json_encode(['result'=>'PASS','checks'=>count($checks),'scope'=>'Isolated native Admin HTML composers/resources/state; no production']) . PHP_EOL;
