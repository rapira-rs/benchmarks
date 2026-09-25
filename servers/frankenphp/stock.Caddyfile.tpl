# FrankenPHP stock worker shape. box/servers/frankenphp.sh renders the placeholders.
# The global worker block and a plain php_server are the shape of the FrankenPHP docs. php_server
# keeps its default file server and try_files, so every request stats the document root before
# the worker answers. This shape shows the cost of the default next to the worker shape, and the
# static targets use it for the asset hit.
# The worker file is the index file of the document root. index and try_files repeat the
# php_server default with that name, so a worker file that is not index.php keeps the default routing.
# num_threads is the worker num plus one, because FrankenPHP needs more threads than workers.
# grace_period 2s limits the stop after TERM to 2 s. No encode directive: the responses stay
# uncompressed.
{
	auto_https off
	admin off
	grace_period 2s

	frankenphp {
		num_threads @@THREADS@@
		worker {
			file @@DOCROOT@@/@@INDEX@@
			num @@PROCS@@
@@ENV@@
		}
	}
}

@@LISTEN@@ {
	root * @@DOCROOT@@

	php_server {
		index @@INDEX@@
		try_files {path} {path}/@@INDEX@@ @@INDEX@@
	}
}
