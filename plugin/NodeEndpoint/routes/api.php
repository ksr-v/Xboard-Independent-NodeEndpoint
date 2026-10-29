<?php
use Illuminate\Support\Facades\Route;
use Plugin\NodeEndpoint\Controllers\SettingsController;
use Plugin\NodeEndpoint\Controllers\WebManagerController;
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
// A running task is polled every 1.5 seconds; keep its budget separate from settings and mutations.
Route::prefix('api/v1/node-endpoint/root')->middleware(['admin', Enabled::class, 'throttle:90,1,ne-root:'])->group(function () {
    Route::get('status', [WebManagerController::class, 'status']);
    Route::post('plan', [WebManagerController::class, 'plan'])->middleware('throttle:10,1,ne-root-plan:');
    Route::post('execute', [WebManagerController::class, 'execute'])->middleware('throttle:5,1,ne-root-execute:');
    Route::get('jobs/{id}', [WebManagerController::class, 'job']);
    Route::post('sync-ca', [WebManagerController::class, 'syncCa'])->middleware('throttle:5,1,ne-root-ca:');
});
