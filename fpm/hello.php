<?php
// Classic per-request script: php-fpm re-executes it every request (opcache
// skips the recompile) — no resident worker; that difference IS the baseline
// being measured. Wire output mirrors the other hello workers.
$body = "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
header('Content-Type: text/plain');
// Explicit Content-Length: PHP/nginx would otherwise chunk the response —
// keepalive survives, framing parity with the other servers doesn't.
header('Content-Length: ' . strlen($body));
echo $body;
