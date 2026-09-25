; php-fpm pool of the php-fpm targets. box/servers/php-fpm.sh renders the placeholders.
; php-fpm loads servers/php.ini through -c.

[global]
error_log = /proc/self/fd/2
daemonize = no

[bench]
; One FastCGI connection per request without keep_conn: the classic deployment pays this cost,
; and the target measures it.
listen = 127.0.0.1:9000
; The default of 511 drops connections at a high fan-out. The kernel limits the value to
; net.core.somaxconn, which provisioning raises.
listen.backlog = 65535
pm = static
pm.max_children = @@PROCS@@
catch_workers_output = yes
