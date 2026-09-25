<?php
// Symfony worker for rapira: the kernel boots once per worker, the handler
// runs per request. Byte-identical to worker-franken.php except the loop
// primitive; both mirror runtime/frankenphp-symfony's Runner (env snapshot
// merge, send(), terminate outside the handler, gc per cycle), so the pair
// compares servers, not bridge code.

use App\Kernel;
use Symfony\Component\Dotenv\Dotenv;
use Symfony\Component\HttpFoundation\Request;

require dirname(__DIR__).'/vendor/autoload.php';

// A client hang-up mid-request must not tear down the resident kernel.
ignore_user_abort(true);

(new Dotenv())->bootEnv(dirname(__DIR__).'/.env');

$kernel = new Kernel($_SERVER['APP_ENV'], (bool) $_SERVER['APP_DEBUG']);
$kernel->boot();

// The server rebuilds $_SERVER for every request, dropping what Dotenv
// loaded at boot; re-merge the snapshot per request (request keys win).
$server = array_filter($_SERVER, static fn (string $key) => !str_starts_with($key, 'HTTP_'), ARRAY_FILTER_USE_KEY);

$sfRequest = null;
$sfResponse = null;

$handler = static function () use ($kernel, $server, &$sfRequest, &$sfResponse): void {
    $_SERVER += $server;
    // The entrypoint is this worker file; present the canonical front
    // controller to the framework's baseUrl/pathInfo detection.
    $_SERVER['SCRIPT_NAME'] = '/index.php';
    $_SERVER['PHP_SELF'] = '/index.php';

    $sfRequest = Request::createFromGlobals();
    $sfResponse = $kernel->handle($sfRequest);
    $sfResponse->send();
};

do {
    $ret = \Rapira\handle_request($handler);

    if ($sfRequest !== null && $sfResponse !== null) {
        $kernel->terminate($sfRequest, $sfResponse);
    }

    gc_collect_cycles();
} while ($ret);
