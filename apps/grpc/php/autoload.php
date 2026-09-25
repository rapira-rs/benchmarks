<?php
// Loads the protobuf runtime and the generated classes.
// GRPC_VENDOR is the directory where provisioning installs servers/roadrunner/composer.lock. Its
// google/protobuf package is the pure-PHP runtime. PHP uses ext-protobuf when the extension is loaded.
// The rapira worker defines no STDERR constant, so the error goes to php://stderr.

$vendor = getenv('GRPC_VENDOR') . '/autoload.php';
if (!is_file($vendor)) {
    file_put_contents('php://stderr', "apps/grpc/php/autoload.php: $vendor is missing; provision the gRPC targets\n");
    exit(1);
}
require $vendor;

// PSR-4 loader for the generated classes: the Bench\ and GPBMetadata\ prefixes map to gen/.
spl_autoload_register(static function (string $class): void {
    $file = __DIR__ . '/gen/' . str_replace('\\', '/', $class) . '.php';
    if ((str_starts_with($class, 'Bench\\') || str_starts_with($class, 'GPBMetadata\\')) && is_file($file)) {
        require $file;
    }
});
