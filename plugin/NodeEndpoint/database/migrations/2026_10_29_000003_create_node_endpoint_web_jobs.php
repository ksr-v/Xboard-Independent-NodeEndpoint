<?php
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        if (!Schema::hasTable('node_endpoint_web_jobs')) {
            Schema::create('node_endpoint_web_jobs', function (Blueprint $table): void {
                $table->id();
                $table->string('job_id', 128)->unique();
                $table->unsignedBigInteger('admin_id')->index();
                $table->string('action', 16);
                $table->string('state', 16);
                $table->timestamps();
            });
        }
    }

    public function down(): void
    {
        // Keep ownership of durable Root jobs across plugin disable, rollback and reinstall.
    }
};
