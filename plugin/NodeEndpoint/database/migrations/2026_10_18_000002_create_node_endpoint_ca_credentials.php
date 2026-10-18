<?php
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
return new class extends Migration {
    public function up(): void
    {
        if (!Schema::hasTable('node_endpoint_ca_credentials')) {
            Schema::create('node_endpoint_ca_credentials', function (Blueprint $table): void {
                $table->char('id', 32)->primary();
                $table->string('host', 253);
                $table->char('fingerprint', 64)->nullable()->unique();
                $table->text('ciphertext');
                $table->timestamps();
            });
        }
    }
    public function down(): void
    {
        // Disabling/uninstalling must not lose access to encrypted CA exports.
        // Credential deletion requires a separate, explicit purge operation.
    }
};
