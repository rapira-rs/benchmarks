<?php
// gRPC echo workload for the RoadRunner gRPC plugin. The reply is
// byte-identical to dispatcher.php.

use Bench\V1\EchoRequest;
use Bench\V1\EchoResponse;
use Bench\V1\EchoServiceInterface;
use Spiral\RoadRunner\GRPC\ContextInterface;
use Spiral\RoadRunner\GRPC\Server;
use Spiral\RoadRunner\Worker;

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
