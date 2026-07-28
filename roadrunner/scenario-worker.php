<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';
require __DIR__ . '/../app/app.php';

use Nyholm\Psr7\Factory\Psr17Factory;
use Nyholm\Psr7\Response;
use Spiral\RoadRunner\Http\PSR7Worker;
use Spiral\RoadRunner\Worker;

// Scenario worker: same canonical PSR-7 loop as worker.php, routing via the
// shared app.php. getRequestTarget() is the origin-form target (path + query) —
// the same string $_SERVER['REQUEST_URI'] carries on the SAPI servers, so
// app.php parses identical input on all five runtimes.
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
        [$status, $headers, $body] = scenario_handle(
            $request->getMethod(),
            $request->getRequestTarget(),
            (string) $request->getBody(),
        );
        // Content-Length added here because the other adapters get it for free
        // from their server layer; rr streams chunked without it (see worker.php).
        $headers['Content-Length'] = (string) strlen($body);
        $psr7->respond(new Response($status, $headers, $body));
    } catch (\Throwable) {
        $psr7->respond(new Response(500, [], 'Something Went Wrong!'));
    }
}
