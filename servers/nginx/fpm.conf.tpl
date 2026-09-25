# nginx in front of php-fpm for the php-fpm targets. box/servers/php-fpm.sh renders the
# placeholders. The main, events, and http settings are the same as in rapira.conf.tpl.
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

    server {
        listen @@LISTEN@@ backlog=65535;
        server_name _;
        root @@DOCROOT@@;

        location / {
            fastcgi_pass 127.0.0.1:9000;
            # The fastcgi_params are inline, because an include resolves against the nginx prefix.
            fastcgi_param SCRIPT_FILENAME  $document_root/@@INDEX@@;
            fastcgi_param SCRIPT_NAME      /@@INDEX@@;
            fastcgi_param QUERY_STRING     $query_string;
            fastcgi_param REQUEST_METHOD   $request_method;
            fastcgi_param CONTENT_TYPE     $content_type;
            fastcgi_param CONTENT_LENGTH   $content_length;
            fastcgi_param REQUEST_URI      $request_uri;
            fastcgi_param DOCUMENT_URI     $document_uri;
            fastcgi_param DOCUMENT_ROOT    $document_root;
            fastcgi_param SERVER_PROTOCOL  $server_protocol;
            fastcgi_param REQUEST_SCHEME   $scheme;
            fastcgi_param GATEWAY_INTERFACE CGI/1.1;
            fastcgi_param SERVER_SOFTWARE  nginx/$nginx_version;
            fastcgi_param REMOTE_ADDR      $remote_addr;
            fastcgi_param REMOTE_PORT      $remote_port;
            fastcgi_param SERVER_ADDR      $server_addr;
            fastcgi_param SERVER_PORT      $server_port;
            fastcgi_param SERVER_NAME      $server_name;
        }
    }
}
