# FrankenPHP classic shape. box/servers/frankenphp.sh renders the placeholders.
# php_server runs the index file on a thread of the pool for each request, as php-fpm does. The
# file server is off and try_files names only the index file, so a request stats one path.
# https://frankenphp.dev/docs/performance/#try_files
# num_threads is the pool size: the same value as the php-fpm children and the rapira processes.
# grace_period 2s limits the stop after TERM to 2 s. No encode directive: the responses stay
# uncompressed.
{
	auto_https off
	admin off
	grace_period 2s

	frankenphp {
		num_threads @@THREADS@@
	}
}

@@LISTEN@@ {
	root * @@DOCROOT@@

	php_server {
		file_server off
		try_files {path} @@INDEX@@
	}
}
