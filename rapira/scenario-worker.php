<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

// Scenario worker: same resident loop as the hello worker, routing via app.php.
// The handler comes from the HTTP plugin and is created once, at boot — the loop
// is per-request, the plugin handler is not.
$http = \Rapira\create_plugin_handler(new \Rapira\Plugin\Http\HttpHandlerConfig());

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
while ($http->handleRequest($handler)) {
}
