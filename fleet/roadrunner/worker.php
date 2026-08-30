<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use Nyholm\Psr7\Factory\Psr17Factory;
use Nyholm\Psr7\Response;
use Spiral\RoadRunner\Http\PSR7Worker;
use Spiral\RoadRunner\Worker;

// Canonical RoadRunner PSR-7 worker; hello body mirrors the other legs.
// Needs ext-protobuf: without it the payload decode falls back to pure PHP
// and runs about half as fast, silently. Provisioning builds it.
$factory = new Psr17Factory();
$psr7 = new PSR7Worker(Worker::create(), $factory, $factory, $factory);

while (true) {
    try {
        $request = $psr7->waitRequest();
        if ($request === null) { // graceful stop
            break;
        }
    } catch (\Throwable) {
        $psr7->respond(new Response(400));
        continue;
    }

    try {
        $name = $request->getQueryParams()['name'] ?? 'anonymous';
        $body = "Hello from worker, {$name}!\n";
        // Explicit Content-Length: rr streams chunked otherwise and the wire
        // framing would differ from the other legs.
        $psr7->respond(new Response(
            200,
            ['Content-Type' => 'text/plain', 'Content-Length' => (string) strlen($body)],
            $body,
        ));
    } catch (\Throwable) {
        $psr7->respond(new Response(500, [], 'Something Went Wrong!'));
    }
}
