<?php

declare(strict_types=1);

use Swoole\Http\Request;
use Swoole\Http\Response;
use Swoole\Http\Server;

// Mirror of the other servers' "hello worker", the canonical Swoole way.
// SWOOLE_BASE: each of the 32 workers accepts on :8080 itself (SO_REUSEPORT,
// same model as Folk) — no dispatcher IPC hop like SWOOLE_PROCESS would add.
// http_compression off for parity (FrankenPHP runs without encode too).
// Port: $SWOOLE_PORT, defaulting to 8080 so the serial bench-* targets are unchanged;
// `make start_all` sets it so every server can run at once on its own port.
$server = new Server('0.0.0.0', (int) (getenv('SWOOLE_PORT') ?: 8080), SWOOLE_BASE);
$server->set([
    'worker_num' => 32,
    'enable_reuse_port' => true,
    'http_compression' => false,
]);

$server->on('request', static function (Request $request, Response $response): void {
    $name = $request->get['name'] ?? 'anonymous';
    $response->header('Content-Type', 'text/plain');
    $response->end("Hello from worker, {$name}!\n");
});

$server->start();
