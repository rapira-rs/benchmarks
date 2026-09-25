# nginx in front of rapira for the nginx-rapira targets. box/servers/nginx-rapira.sh renders the
# placeholders. The main, events, and http settings are the same as in fpm.conf.tpl.
worker_processes @@PROCS@@;
worker_rlimit_nofile 65536;
pid run/nginx.pid;
error_log stderr warn;

events {
    worker_connections 65536;
}

http {
    access_log off;

    client_body_temp_path tmp/client_body;
    fastcgi_temp_path tmp/fastcgi;
    proxy_temp_path tmp/proxy;
    uwsgi_temp_path tmp/uwsgi;
    scgi_temp_path tmp/scgi;

    # The default of 1000 closes the client connections during a stage and fills TIME-WAIT.
    keepalive_requests 1000000;

    upstream rapira {
        server 127.0.0.1:8081;
        keepalive 256;
        keepalive_requests 1000000;
    }

    server {
        listen @@LISTEN@@ backlog=65535;
        server_name _;

        location / {
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $http_host;
            proxy_pass http://rapira;
        }
    }
}
