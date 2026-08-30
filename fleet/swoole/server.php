<?php

declare(strict_types=1);

use Swoole\Http\Request;
use Swoole\Http\Response;
use Swoole\Http\Server;

// Swoole hello leg. SWOOLE_BASE: each worker accepts on :8080 itself over
// SO_REUSEPORT, no dispatcher IPC hop. Worker count comes from the
// SWOOLE_WORKERS env, rendered from the PROCESSES knob by fleet-leg.sh.
// http_compression off for parity with the other legs.
$server = new Server('0.0.0.0', 8080, SWOOLE_BASE);
$server->set([
    'worker_num' => (int) getenv('SWOOLE_WORKERS'),
    'enable_reuse_port' => true,
    'http_compression' => false,
]);

$server->on('request', static function (Request $request, Response $response): void {
    $name = $request->get['name'] ?? 'anonymous';
    $response->header('Content-Type', 'text/plain');
    $response->end("Hello from worker, {$name}!\n");
});

$server->start();
