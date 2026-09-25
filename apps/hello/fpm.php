<?php
// php-fpm hello: the wire output is the same as on the other targets.
$body = "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
header('Content-Type: text/plain');
// Explicit Content-Length: PHP and nginx would otherwise chunk the response, and
// the wire framing would differ from the other targets.
header('Content-Length: ' . strlen($body));
echo $body;
