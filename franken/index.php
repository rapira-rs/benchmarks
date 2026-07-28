<?php
// FrankenPHP worker — a mirror of rapira's crates/integration_tests/fixtures/worker.php.
// Handler body and wire output are identical; only the resident loop differs: FrankenPHP
// calls \frankenphp_handle_request($handler), rapira takes a plugin handler once at boot
// (\Rapira\create_plugin_handler) and loops on $http->handleRequest($handler).
// Named index.php so the server routes `/` to it.
$handler = static function (): void {
    header('Content-Type: text/plain');
    echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
};
while (\frankenphp_handle_request($handler)) {
}
