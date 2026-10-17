<?php
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
return new class extends Migration {
    public function up(): void
    {
        if (!Schema::hasTable('node_endpoint_settings')) {
            Schema::create('node_endpoint_settings', function (Blueprint $table) {
                $table->unsignedTinyInteger('id')->primary();
                $table->text('settings');
                $table->timestamps();
            });
        }
    }
    public function down(): void
    {
        // Native rollback only selects the global last migration batch. Retain
        // recovery settings consistently; tools/plugin/purge.php is explicit.
    }
};
