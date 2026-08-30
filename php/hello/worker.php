<?php
// Frozen hello workload, worker mode. Both A/B refs run this same file; an
// API change that breaks it must fail the cell loudly, never bench silently.
$handler = static function (): void {
    header('Content-Type: text/plain');
    echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
};
while (\Rapira\handle_request($handler)) {
}
