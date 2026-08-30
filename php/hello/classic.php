<?php
// Frozen hello workload, classic mode: re-included every request, no resident
// worker. That per-request execution is the thing being measured.
header('Content-Type: text/plain');
echo "Hello from worker, " . ($_GET['name'] ?? 'anonymous') . "!\n";
