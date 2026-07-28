<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Mirror of ../rapira/scenario-worker.php — the per-request handler is identical; only the
// runtime plumbing differs: rapira builds an HTTP plugin handler once at boot and loops on
// $http->handleRequest().
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
while (\frankenphp_handle_request($handler)) {
}
