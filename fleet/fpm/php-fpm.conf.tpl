; php-fpm pool for the fpm leg. fleet-leg.sh renders @@PROCS@@ at start time.
; Inherits the system php.ini, the same config every other leg loads.

[global]
error_log = /proc/self/fd/2
daemonize = no

[bench]
; One fastcgi conn per request, no keep_conn: the classic deployment's
; per-request cost is part of what this leg measures.
listen = 127.0.0.1:9000
; The 511 default drops connects under high fan-out; the kernel still clamps
; to net.core.somaxconn, raised by provisioning.
listen.backlog = 65535
pm = static
pm.max_children = @@PROCS@@
catch_workers_output = yes
