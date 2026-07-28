<?php
$handler = static function (): void {
    if (($_GET['size'] ?? '') === '16k') {
        echo str_repeat('x', 16384);
        return;
    }
    $name = $_GET['name'] ?? 'world';
    echo "Hello from worker, {$name}!";
};
$http = \Rapira\create_plugin_handler(new \Rapira\Plugin\Http\HttpHandlerConfig());
while ($http->handleRequest($handler)) {
}
