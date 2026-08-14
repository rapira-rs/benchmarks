<?php
// rapira hello worker (dispatcher mode) — core's examples/dispatcher.php shape:
// ten fibers take turns on the receive loop, one unit per turn. Body stays
// byte-identical to the other servers' hello workers.

use Rapira\Exception\ClosedException;

$d = \Rapira\get_dispatcher();

$fibers = [];
for ($i = 0; $i < 10; $i++) {
    $fibers[$i] = new Fiber(static function () use ($d): void {
        while (true) {
            $ex = $d->receive();
            parse_str(parse_url($ex->getRequest()->target, PHP_URL_QUERY) ?: '', $q);
            $ex->writeHead(200, ['content-type' => ['text/plain']]);
            $ex->writeBody('Hello from worker, ' . ($q['name'] ?? 'anonymous') . "!\n");
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
