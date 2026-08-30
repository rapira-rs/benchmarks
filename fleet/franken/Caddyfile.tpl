# FrankenPHP hello leg. fleet-leg.sh renders @@PROCS@@/@@THREADS@@ from the
# PROCESSES knob at start time: an unpinned pool auto-sizes and breaks parity.
# num_threads must be strictly greater than the worker num.
# No encode directive: responses stay uncompressed like every other leg.
{
	auto_https off
	admin off

	frankenphp {
		num_threads @@THREADS@@

		worker {
			file ./index.php
			num @@PROCS@@
		}
	}
}

:8080 {
	root * .
	php_server
}
