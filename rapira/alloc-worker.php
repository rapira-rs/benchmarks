<?php
$handler = static function (): void {
    if (($_GET['size'] ?? '') === '16k') {
        echo str_repeat('x', 16384);
        return;
    }
    $name = $_GET['name'] ?? 'world';
    echo "Hello from worker, {$name}!";
};
while (\Rapira\handle_request($handler)) {
}
