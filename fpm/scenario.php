<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario adapter: routes via the shared app.php. $_GET/$_POST are ignored on
// purpose (app.php parses the raw strings itself — see its header comment); the
// FPM SAPI still pre-parses them in C, the same inherent cost rapira and
// FrankenPHP pay. REQUEST_URI is the origin-form path?query, same string the
// other SAPI servers see.
[$status, $headers, $body] = scenario_handle(
    $_SERVER['REQUEST_METHOD'],
    $_SERVER['REQUEST_URI'],
    (string) file_get_contents('php://input'), // cast, not ?:, so a body of "0" survives
);

http_response_code($status);
foreach ($headers as $name => $value) {
    header($name . ': ' . $value);
}
header('Content-Length: ' . strlen($body));
echo $body;
