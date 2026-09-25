<?php
// FrankenPHP hello worker: same handler body and wire output as the rapira
// workloads.
$handler = static function (): void {
    header('Content-Type: text/plain');
    echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
};
while (\frankenphp_handle_request($handler)) {
}
