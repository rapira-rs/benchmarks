<?php

declare(strict_types=1);

// Mini-app shared verbatim by all five scenario workers (rapira, FrankenPHP,
// php-fpm, RoadRunner, Swoole).
// Adapters pass the raw method, request target (path + query), and raw body; ALL
// parsing happens here — Swoole and RoadRunner never populate $_GET/$_POST, so
// using superglobals only on the SAPI servers would make the runtimes execute
// different code for the same route.
// Returns [int $status, array<string,string> $headers, string $body].
function scenario_handle(string $method, string $target, string $rawBody): array
{
    $path = parse_url($target, PHP_URL_PATH) ?? '/';

    // Any GET greets — same wire output as the hello worker, and keeps the
    // Makefile readiness probe (GET /?name=you) working in scenario mode.
    if ($method === 'GET') {
        $params = [];
        $query = parse_url($target, PHP_URL_QUERY);
        if (is_string($query) && $query !== '') {
            parse_str($query, $params);
        }
        $name = is_string($params['name'] ?? null) ? $params['name'] : 'anonymous';
        return [200, ['Content-Type' => 'text/plain'], "Hello from worker, {$name}!\n"];
    }

    if ($method === 'POST' && $path === '/echo') {
        $data = json_decode($rawBody, true);
        if (!is_array($data)) {
            return [400, ['Content-Type' => 'application/json'], '{"error":"invalid json"}'];
        }
        // count() + re-encode force a real parse/serialize round-trip.
        return [200, ['Content-Type' => 'application/json'],
                json_encode(['echo' => $data, 'count' => count($data)], JSON_INVALID_UTF8_SUBSTITUTE)];
    }

    if ($method === 'POST' && $path === '/form') {
        $form = [];
        parse_str($rawBody, $form);
        // JSON_INVALID_UTF8_SUBSTITUTE keeps the encode total: a body like `k=%FF`
        // url-decodes to invalid UTF-8, and a bare json_encode would return false —
        // which each runtime would surface differently (parity break).
        return [200, ['Content-Type' => 'application/json'],
                json_encode(['form' => $form, 'count' => count($form)], JSON_INVALID_UTF8_SUBSTITUTE)];
    }

    if (preg_match('#^/resource/([^/]+)$#', $path, $m) === 1) {
        if ($method === 'PUT' || $method === 'DELETE') {
            // 200 + body on purpose: a 204 gets no Content-Length from rapira's
            // front (core crates/plugins/pingora/src/lib.rs no_body branch) —
            // different framing than the other routes would skew the keepalive
            // comparison.
            return [200, ['Content-Type' => 'application/json'],
                    json_encode(['method' => $method, 'id' => $m[1]], JSON_INVALID_UTF8_SUBSTITUTE)];
        }
        return [405, ['Content-Type' => 'text/plain'], "Method Not Allowed\n"];
    }

    return [404, ['Content-Type' => 'text/plain'], "Not Found\n"];
}
