<?php
// gRPC echo workload for the RoadRunner gRPC plugin. The reply is
// byte-identical to php/grpc/dispatcher.php.

use Bench\V1\EchoRequest;
use Bench\V1\EchoResponse;
use Bench\V1\EchoServiceInterface;
use Spiral\RoadRunner\GRPC\ContextInterface;
use Spiral\RoadRunner\GRPC\Server;
use Spiral\RoadRunner\Worker;

if (!extension_loaded('protobuf')) {
    fwrite(STDERR, "php/grpc/rr-worker.php: ext-protobuf is not loaded\n");
    exit(1);
}

// The rig installs fleet/roadrunner/composer.lock into this directory.
$vendor = '/opt/bench/fleet/roadrunner-grpc/vendor/autoload.php';
if (!is_file($vendor)) {
    fwrite(STDERR, "php/grpc/rr-worker.php: $vendor is missing; provision the RoadRunner gRPC leg\n");
    exit(1);
}
require $vendor;
require __DIR__ . '/autoload.php';

final class EchoService implements EchoServiceInterface
{
    public function Echo(ContextInterface $ctx, EchoRequest $in): EchoResponse
    {
        $out = new EchoResponse();
        $out->setText('Hello from worker, ' . $in->getText() . '!');
        return $out;
    }
}

$server = new Server();
$server->registerService(EchoServiceInterface::class, new EchoService());
$server->serve(Worker::create());
