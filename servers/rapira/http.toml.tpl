# rapira.toml of the rapira HTTP targets. box/servers/rapira.sh renders the placeholders.
# The paths are absolute because rapira resolves a relative path against the directory of this file.
# The warn level is necessary: the rig voids a rapira cell on a WARN or ERROR line.
[http]
listen = "@@LISTEN@@"

[http.pool]
entrypoint = "@@ENTRY@@"
mode = "@@MODE@@"
processes = @@PROCS@@

[log]
level = "warn"
