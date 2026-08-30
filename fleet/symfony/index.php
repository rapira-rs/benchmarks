<?php

use App\Kernel;
use Symfony\Component\Dotenv\Dotenv;
use Symfony\Component\HttpFoundation\Request;

// Bench front controller, installed as public/index.php on every server so
// the classic legs execute one identical file. Same per-request shape as
// symfony/runtime's HttpKernelRunner, minus send()'s flush tail: rapira
// v0.7.0 seals a response as lengthless when PHP calls flush() after output
// and the connection dies with it (fixed on main).
require_once dirname(__DIR__).'/vendor/autoload.php';

(new Dotenv())->bootEnv(dirname(__DIR__).'/.env');

$kernel = new Kernel($_SERVER['APP_ENV'], (bool) $_SERVER['APP_DEBUG']);
$request = Request::createFromGlobals();
$response = $kernel->handle($request);
$response->send(false);
$kernel->terminate($request, $response);
