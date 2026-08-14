<?php
// rapira hello worker (worker mode). Vendored on purpose: the Makefile used to
// point straight at a core fixture, so a core-side API change silently
// redefined the measured workload (and did, when the plugin handler landed).
// Keep the body byte-identical to the other servers' hello workers.
$handler = static function (): void {
    header('Content-Type: text/plain');
    echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
};
while (\Rapira\handle_request($handler)) {
}
