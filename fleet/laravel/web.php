<?php

use App\Http\Controllers\BenchController;
use Illuminate\Support\Facades\Route;

// Replaces the skeleton's routes/web.php: the hello contract through the
// full Laravel pipeline (session and cache stay array-backed, see the
// provisioned .env).
Route::get('/', BenchController::class);
