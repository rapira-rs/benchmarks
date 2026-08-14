<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario worker (dispatcher mode): app.php routing over the ten-fiber
// receive loop; the request never touches superglobals.

use Rapira\Exception\ClosedException;

$d = \Rapira\get_dispatcher();

$fibers = [];
for ($i = 0; $i < 10; $i++) {
    $fibers[$i] = new Fiber(static function () use ($d): void {
        while (true) {
            $ex = $d->receive();
            $req = $ex->getRequest();
            [$status, $headers, $body] = scenario_handle(
                $req->method,
                $req->target,
                is_string($req->body) ? $req->body : '',
            );
            $h = [];
            foreach ($headers as $name => $value) {
                $h[strtolower($name)] = [$value];
            }
            $ex->writeHead($status, $h);
            $ex->writeBody($body);
            Fiber::suspend();
        }
    });
}

try {
    for ($i = 0; true; $i = ($i + 1) % 10) {
        $fibers[$i]->isStarted() ? $fibers[$i]->resume() : $fibers[$i]->start();
    }
} catch (ClosedException) {
}
