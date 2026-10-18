<?php
use Illuminate\Support\Facades\Route;
use Plugin\NodeEndpoint\Controllers\SettingsController;
use Plugin\NodeEndpoint\Middleware\Enabled;
Route::prefix('api/v1/node-endpoint')->middleware(['admin', Enabled::class, 'throttle:30,1'])->group(function () {
    Route::get('settings', [SettingsController::class, 'show']);
    Route::post('settings', [SettingsController::class, 'save']);
    Route::post('ca', [SettingsController::class, 'importCa']);
    Route::get('ca', [SettingsController::class, 'exportCa']);
    Route::post('ca-passphrase/reveal', [SettingsController::class, 'revealCaPassphrase'])
        ->middleware('throttle:5,1,ne-ca-reveal:');
    Route::post('status-import', [SettingsController::class, 'importStatus']);
    Route::post('probe', [SettingsController::class, 'probe']);
    Route::post('detect-ip', [SettingsController::class, 'detectIp']);
    Route::post('command', [SettingsController::class, 'command']);
});
