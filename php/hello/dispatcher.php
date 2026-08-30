<?php
// Frozen hello workload, dispatcher mode: one request at a time on a blocking
// receive(). Body stays byte-identical to the other workloads.

use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;

$d = \Rapira\get_dispatcher();

while (true) {
    try {
        $ex = $d->receive();
        parse_str(parse_url($ex->getRequest()->target, PHP_URL_QUERY) ?: '', $q);
        $ex->writeHead(200, ['content-type' => ['text/plain']]);
        $ex->writeBody('Hello from worker, ' . ($q['name'] ?? 'anonymous') . "!\n");
    } catch (ClosedException) {
        // Drained: no more work will arrive.
        break;
    } catch (RapiraThrowable) {
        // The client vanished mid-response, routine at wrk connection counts.
    }
}
