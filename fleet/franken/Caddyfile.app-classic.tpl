# FrankenPHP framework leg, classic mode: php_server executes the app's
# public/index.php per request, no worker block. num_threads is the execution
# pool here, so it is rendered to the PROCESSES knob for parity with the
# other classic legs. No encode directive: responses stay uncompressed.
{
	auto_https off
	admin off

	frankenphp {
		num_threads @@THREADS@@
	}
}

:8080 {
	root * @@DOCROOT@@
	php_server
}
