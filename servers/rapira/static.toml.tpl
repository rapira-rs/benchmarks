# rapira.toml of the rapira static targets. box/servers/rapira.sh renders the placeholders.
# The static middleware serves a file from the root. A miss goes to the PHP pool.
# The warn level is necessary: the rig voids a rapira cell on a WARN or ERROR line.
[http]
listen = "@@LISTEN@@"
middleware = ["static"]

[http.static]
root = "@@ROOT@@"

[http.pool]
entrypoint = "@@ENTRY@@"
mode = "@@MODE@@"
processes = @@PROCS@@

[log]
level = "warn"
