# FrankenPHP worker shape. box/servers/frankenphp.sh renders the placeholders.
# This is the production shape of the FrankenPHP performance guide. The file server is off and the
# worker matches every path, so no request stats the document root.
# https://frankenphp.dev/docs/performance/#try_files
# num_threads is the worker num plus one, because FrankenPHP needs more threads than workers.
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

		worker {
			file @@ENTRY@@
			num @@PROCS@@
			match *
@@ENV@@
		}
	}
}
