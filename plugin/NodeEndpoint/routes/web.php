<?php
use Illuminate\Support\Facades\Route;
use Plugin\NodeEndpoint\Middleware\Enabled;
Route::get('node-endpoint', function () {
    return response()->file(__DIR__ . '/../resources/views/index.html', [
        'Content-Type' => 'text/html; charset=UTF-8', 'Cache-Control' => 'no-store',
        'Content-Security-Policy' => "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        'Referrer-Policy' => 'no-referrer', 'X-Content-Type-Options' => 'nosniff',
    ]);
})->middleware(Enabled::class);

foreach (['app' => ['app.js', 'text/javascript'], 'style' => ['style.css', 'text/css']] as $asset => [$file, $type]) {
    Route::get('node-endpoint/assets/' . $asset, function () use ($file, $type) {
        return response()->file(__DIR__ . '/../resources/assets/' . $file,
            ['Content-Type' => $type . '; charset=UTF-8', 'Cache-Control' => 'no-cache', 'X-Content-Type-Options' => 'nosniff']);
    })->middleware(Enabled::class);
}
