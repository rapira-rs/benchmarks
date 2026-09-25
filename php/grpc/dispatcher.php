<?php
// gRPC echo workload, dispatcher mode: one call at a time on a blocking
// receive(). The reply is byte-identical to the other gRPC servers.

use Bench\V1\EchoRequest;
use Bench\V1\EchoResponse;
use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;

// The rapira worker defines no STDERR constant.
if (!extension_loaded('protobuf')) {
    file_put_contents('php://stderr', "php/grpc/dispatcher.php: ext-protobuf is not loaded\n");
    exit(1);
}

require __DIR__ . '/autoload.php';

$d = \Rapira\get_dispatcher();

while (true) {
    try {
        $call = $d->receive();
        $req = new EchoRequest();
        $req->mergeFromString($call->getMessage());
        $out = new EchoResponse();
        $out->setText('Hello from worker, ' . $req->getText() . '!');
        $call->respond($out->serializeToString());
    } catch (ClosedException) {
        // Drained: no more work will arrive.
        break;
    } catch (RapiraThrowable) {
        // The host already closed the call.
    }
}
