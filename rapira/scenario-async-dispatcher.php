<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario worker (dispatcher mode, ASYNCHRONOUS) — core's examples/dispatcher-async.php
// shape: a fiber per request, tryReceive() between resumes while requests are in flight, a
// blocking receive() once none are left.
//
// Same caveat as async-dispatcher.php: scenario_handle() never calls Fiber::suspend(), so
// each fiber runs to completion inside start() and is never queued — $fibers stays empty and
// the match() always takes the blocking receive() branch. This row measures the cost of the
// fiber-per-request scaffolding, NOT request overlap.

use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;
use Rapira\Http\Exchange;

$d = \Rapira\get_dispatcher();

$serve = static function (Exchange $ex): void {
    try {
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
    } catch (RapiraThrowable) {
        // The host closed the exchange first — nothing to answer.
    }
};

/** @var array<int, Fiber> $fibers */
$fibers = [];
$max = 100;

try {
    while (true) {
        $ex = match (count($fibers)) {
            $max => null,
            0 => $d->receive(),
            default => $d->tryReceive(),
        };

        if ($ex !== null) {
            $fiber = new Fiber($serve);
            $fiber->start($ex);
            $fiber->isTerminated() or $fibers[] = $fiber;
        }

        foreach ($fibers as $i => $fiber) {
            $fiber->resume();
            if ($fiber->isTerminated()) {
                unset($fibers[$i]);
            }
        }
    }
} catch (ClosedException) {
    do {
        foreach ($fibers as $i => $fiber) {
            $fiber->resume();
            if ($fiber->isTerminated()) {
                unset($fibers[$i]);
            }
        }
    } while (count($fibers) > 0);
}
