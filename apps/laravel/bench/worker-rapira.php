<?php
// Laravel worker for rapira, driven by Octane's own Worker and
// FrankenPhpClient. The client uses only standard PHP APIs
// (Request::capture(), Response::send()), so the same Octane machinery and
// per-request state resets run here as on the FrankenPHP target; only the loop
// primitive differs. Mirrors octane's bin/frankenphp-worker.php.

use Laravel\Octane\ApplicationFactory;
use Laravel\Octane\FrankenPhp\FrankenPhpClient;
use Laravel\Octane\RequestContext;
use Laravel\Octane\Stream;
use Laravel\Octane\Worker;
use Symfony\Component\HttpFoundation\Response;

$basePath = dirname(__DIR__);

require $basePath.'/vendor/autoload.php';

// A client hang-up mid-request must not tear down the resident app.
ignore_user_abort(true);

$_ENV['APP_RUNNING_IN_CONSOLE'] = false;

$client = new FrankenPhpClient();

$worker = new Worker(new ApplicationFactory($basePath), $client);
$worker->boot();

$handler = static function () use ($worker, $client): void {
    try {
        // laravel/framework branches on this key (octane's servers set it in
        // the worker env); $_SERVER is rebuilt per request, so set it here.
        $_SERVER['LARAVEL_OCTANE'] = '1';

        [$request, $context] = $client->marshalRequest(new RequestContext());

        $worker->handle($request, $context);
    } catch (Throwable $e) {
        report($e);

        (new Response('Internal Server Error', 500, [
            'Status' => '500 Internal Server Error',
            'Content-Type' => 'text/plain',
        ]))->send();

        Stream::shutdown($e);
    }
};

try {
    while (\Rapira\handle_request($handler)) {
    }
} finally {
    $worker->terminate();

    gc_collect_cycles();
}
