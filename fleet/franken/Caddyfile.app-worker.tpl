# FrankenPHP framework leg, worker mode: the app boots once per worker and
# the worker file (which must live under the docroot) handles every request
# that php_server resolves to it. fleet-leg.sh renders the placeholders at
# start time; num_threads must be strictly greater than the worker num, and
# the env placeholder line expands to per-framework env lines (Octane reads
# a few). Placeholder tokens must not appear in these comments: the renderer
# substitutes every occurrence.
{
	auto_https off
	admin off

	frankenphp {
		num_threads @@THREADS@@

		worker {
			file @@WORKER@@
			num @@PROCS@@
@@WORKER_ENV@@
		}
	}
}

:8080 {
	root * @@DOCROOT@@

	php_server {
		index @@INDEXFILE@@
		try_files {path} @@INDEXFILE@@
	}
}
