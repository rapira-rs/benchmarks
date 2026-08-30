<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Http\Response;

// Bench route: same body as the hello workload, so every leg and the k6
// checks share one contract. Content-Length is explicit because the
// framework never adds it and nginx+fpm would chunk the response, changing
// the wire framing against the other legs.
class BenchController extends Controller
{
    public function __invoke(Request $request): Response
    {
        $name = $request->query('name', 'anonymous');
        $body = "Hello from worker, {$name}!\n";

        return response($body, 200)
            ->header('Content-Type', 'text/plain')
            ->header('Content-Length', (string) strlen($body));
    }
}
