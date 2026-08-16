<?php
// rapira hello worker (dispatcher mode, SYNCHRONOUS) — core's examples/dispatcher-sync.php
// shape: one request at a time on a blocking receive(). Body stays byte-identical to the
// other servers' hello workers.
//
// This replaced a ten-fiber round-robin loop on 2026-08-16. Those fibers were vestigial:
// receive() blocks the thread, so a fiber parked in it stalled the whole round-robin until
// a request arrived — exactly one request in flight per worker, same as this loop, plus the
// fiber machinery. The measured workload is unchanged; only the misleading shape is gone.
// The fiber-per-request flavour now lives in async-dispatcher.php as its own bench leg.

use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;

$d = \Rapira\get_dispatcher();

while (true) {
    try {
        while (true) {
            $ex = $d->receive();
            parse_str(parse_url($ex->getRequest()->target, PHP_URL_QUERY) ?: '', $q);
            $ex->writeHead(200, ['content-type' => ['text/plain']]);
            $ex->writeBody('Hello from worker, ' . ($q['name'] ?? 'anonymous') . "!\n");
        }
    } catch (ClosedException) {
        // Drained: no more work will ever arrive.
        break;
    } catch (RapiraThrowable) {
        // The host closed the exchange first (client vanished mid-response — routine at
        // wrk's connection counts). Nothing to answer; take the next unit.
    }
}
