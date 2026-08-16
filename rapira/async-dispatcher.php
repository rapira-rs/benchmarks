<?php
// rapira hello worker (dispatcher mode, ASYNCHRONOUS) — core's examples/dispatcher-async.php
// shape: a fiber per request, tryReceive() between resumes while requests are in flight, a
// blocking receive() once none are left. Body byte-identical to the other hello workers.
//
// WHAT THIS LEG ACTUALLY MEASURES: the hello handler never calls Fiber::suspend(), so
// $fiber->start($ex) runs it to completion and isTerminated() is true immediately — the fiber
// is never queued, $fibers stays empty, and the match() therefore always takes the blocking
// receive() branch. So this is the sync loop plus one Fiber allocation and start() per
// request. The rapira-async row is the COST OF THE ASYNC SCAFFOLDING on a workload with
// nothing to overlap; it is NOT an async-beats-sync-under-load measurement. Overlap only pays
// when a handler suspends (core's example suspends inside its /stream generator), and no
// server in this suite does blocking work per request.

use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;
use Rapira\Http\Exchange;

$d = \Rapira\get_dispatcher();

$serve = static function (Exchange $ex): void {
    try {
        parse_str(parse_url($ex->getRequest()->target, PHP_URL_QUERY) ?: '', $q);
        $ex->writeHead(200, ['content-type' => ['text/plain']]);
        $ex->writeBody('Hello from worker, ' . ($q['name'] ?? 'anonymous') . "!\n");
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
