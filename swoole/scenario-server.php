<?php

declare(strict_types=1);

require __DIR__ . '/../app/app.php';

use Swoole\Http\Request;
use Swoole\Http\Response;
use Swoole\Http\Server;

// Scenario server: same SWOOLE_BASE setup as server.php, routing via the shared
// app.php. Swoole splits the request target ($request->server['request_uri'] is
// the path only), so the adapter reassembles path?query — the string
// $_SERVER['REQUEST_URI'] carries on the SAPI servers.
$server = new Server('0.0.0.0', 8080, SWOOLE_BASE);
$server->set([
    'worker_num' => 32,
    'enable_reuse_port' => true,
    'http_compression' => false,
    // Parity with Folk/RoadRunner (rr pins raw_body: true for the same reason):
    // don't let the C layer url-decode form bodies into $request->post that this
    // adapter never reads — app.php parses the raw string itself.
    'http_parse_post' => false,
    'http_parse_cookie' => false,
]);

$server->on('request', static function (Request $request, Response $response): void {
    $query = $request->server['query_string'] ?? '';
    [$status, $headers, $body] = scenario_handle(
        $request->server['request_method'] ?? 'GET',
        ($request->server['request_uri'] ?? '/') . ($query === '' ? '' : "?{$query}"),
        (string) $request->rawContent(), // cast, not ?:, so a body of "0" survives
    );
    $response->status($status);
    foreach ($headers as $name => $value) {
        $response->header($name, $value);
    }
    $response->end($body);
});

$server->start();
