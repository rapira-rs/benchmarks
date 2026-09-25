<?php
// PSR-4 loader for the generated classes: the Bench\ and GPBMetadata\ prefixes map to gen/.

spl_autoload_register(static function (string $class): void {
    $file = __DIR__ . '/gen/' . str_replace('\\', '/', $class) . '.php';
    if ((str_starts_with($class, 'Bench\\') || str_starts_with($class, 'GPBMetadata\\')) && is_file($file)) {
        require $file;
    }
});
