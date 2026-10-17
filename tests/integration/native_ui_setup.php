<?php
/** Synthetic loopback browser fixture only; never bootstrap a real application. */
declare(strict_types=1);
$work=__DIR__.'/work';
if (!is_dir($work)) mkdir($work,0700,true);
$target=getenv('NE_FIXTURE_PATH') ?: $work.'/native-choice-ui';
$parent=realpath(dirname($target));
if (!$parent || strcasecmp($parent,(string)realpath($work))!==0 || !preg_match('/^[a-zA-Z0-9_-]+$/',basename($target))) {
    throw new RuntimeException('UI fixture must be a direct child of tests/integration/work.');
}
putenv('NE_FIXTURE_PATH='.$target);
require __DIR__.'/bootstrap.php';
$manager=app(App\Services\Plugin\PluginManager::class);
if (!App\Models\Plugin::where('code','node_endpoint')->exists()) $manager->install('node_endpoint');
$manager->enable('node_endpoint');
Plugin\NodeEndpoint\Services\Settings::write(array_replace(Plugin\NodeEndpoint\Services\Endpoint::DEFAULTS,
    ['enabled'=>true,'host'=>'192.0.2.10','port'=>8443,'https'=>true,'ws_mode'=>'rewrite']));
if (!App\Models\ServerMachine::where('name','Second choice fixture')->exists()) App\Models\ServerMachine::create([
    'name'=>'Second choice fixture','token'=>'fixture-second-machine-token','is_active'=>true]);
$config=storage_path('app/choice-ca.cnf');
file_put_contents($config,"[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=Choice fixture\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n");
$key=openssl_pkey_new(['private_key_type'=>OPENSSL_KEYTYPE_EC,'private_key_bits'=>2048,'curve_name'=>'prime256v1','config'=>$config]);
$csr=openssl_csr_new(['commonName'=>'Choice fixture CA'],$key,['config'=>$config,'digest_alg'=>'sha256']);
$cert=openssl_csr_sign($csr,null,$key,30,['config'=>$config,'x509_extensions'=>'v3_ca','digest_alg'=>'sha256']);
if (!$cert || !openssl_x509_export($cert,$pem)) throw new RuntimeException('Fixture certificate failed.');
file_put_contents(Plugin\NodeEndpoint\Services\Settings::caPath(),$pem);
echo json_encode(['prepared'=>true,'fixture'=>$fixture,'machines'=>App\Models\ServerMachine::count()]).PHP_EOL;
