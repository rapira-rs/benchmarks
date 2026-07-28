<?php
// rapira hello worker — vendored copy of core's crates/integration_tests/fixtures/worker.php.
// Vendored on purpose: the Makefile used to point straight at that fixture, so a core-side
// API change silently redefined the measured workload (and did, when the plugin handler
// landed). Keep the body byte-identical to the other servers' hello workers.
$handler = static function (): void {
    header('Content-Type: text/plain');
    echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
};
$http = Rapira\create_plugin_handler(new Rapira\Plugin\Http\HttpHandlerConfig());
while ($http->handleRequest($handler)) {
}
