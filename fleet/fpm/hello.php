<?php
// fpm hello: wire output mirrors the other legs.
$body = "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
header('Content-Type: text/plain');
// Explicit Content-Length: PHP/nginx would otherwise chunk the response and
// the wire framing would differ from the other legs.
header('Content-Length: ' . strlen($body));
echo $body;
