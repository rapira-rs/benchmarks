<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario worker (dispatcher mode, SYNCHRONOUS) — core's examples/dispatcher-sync.php
// shape: app.php routing over a blocking receive(), one request at a time. The request
// never touches superglobals.
//
// Replaced a ten-fiber round-robin loop on 2026-08-16; see dispatcher.php for why those
// fibers measured nothing. The fiber-per-request flavour is scenario-async-dispatcher.php.

use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;

$d = \Rapira\get_dispatcher();

while (true) {
    try {
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
        }
    } catch (ClosedException) {
        // Drained: no more work will ever arrive.
        break;
    } catch (RapiraThrowable) {
        // The host closed the exchange first — nothing to answer, take the next unit.
    }
}
