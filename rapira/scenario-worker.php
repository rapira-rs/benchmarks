<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario worker (worker mode): same resident loop as the hello worker,
// routing via app.php.
$handler = static function (): void {
    [$status, $headers, $body] = scenario_handle(
        $_SERVER['REQUEST_METHOD'] ?? 'GET',
        $_SERVER['REQUEST_URI'] ?? '/',
        (string) file_get_contents('php://input'), // cast, not ?:, so a body of "0" survives
    );
    http_response_code($status);
    foreach ($headers as $name => $value) {
        header("{$name}: {$value}");
    }
    echo $body;
};
while (\Rapira\handle_request($handler)) {
}
