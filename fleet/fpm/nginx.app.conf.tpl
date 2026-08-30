# nginx front for the framework fpm legs. Same shape as nginx.conf, but the
# front controller is the app's public/index.php: every path executes it over
# fastcgi, the standard production deployment for Symfony and Laravel.
# fleet-leg.sh renders @@DOCROOT@@ at start time.

worker_processes auto;
worker_rlimit_nofile 65536;
pid run/nginx.pid;

events {
    worker_connections 16384;
}

http {
    access_log off;

    client_body_temp_path tmp/client_body;
    fastcgi_temp_path tmp/fastcgi;
    proxy_temp_path tmp/proxy;
    uwsgi_temp_path tmp/uwsgi;
    scgi_temp_path tmp/scgi;

    # The 1000 default recycles client conns mid-bench and floods TIME-WAIT.
    keepalive_requests 1000000;

    server {
        listen 8080 backlog=65535;
        server_name _;
        root @@DOCROOT@@;

        location / {
            fastcgi_pass 127.0.0.1:9000;
            # fastcgi_params inlined: an include would resolve against this
            # prefix, not /etc/nginx.
            fastcgi_param SCRIPT_FILENAME  $document_root/index.php;
            fastcgi_param SCRIPT_NAME      /index.php;
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
