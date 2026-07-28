<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use Nyholm\Psr7\Factory\Psr17Factory;
use Nyholm\Psr7\Response;
use Spiral\RoadRunner\Http\PSR7Worker;
use Spiral\RoadRunner\Worker;

// Mirror of the rapira/FrankenPHP/Folk "hello worker", written the canonical
// RoadRunner way (docs.roadrunner.dev PSR-7 worker): rr's Go http plugin ships
// each request to this resident process over pipes, PSR7Worker decodes it.
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
        // Explicit Content-Length: rr's http layer otherwise streams the response
        // as Transfer-Encoding: chunked — keepalive survives (chunked self-delimits)
        // but the wire framing would differ from the other servers.
        $psr7->respond(new Response(
            200,
            ['Content-Type' => 'text/plain', 'Content-Length' => (string) strlen($body)],
            $body,
        ));
    } catch (\Throwable) {
        $psr7->respond(new Response(500, [], 'Something Went Wrong!'));
    }
}
