# rapira.toml of the rapira gRPC targets. box/servers/rapira.sh renders the placeholders.
# The paths are absolute because rapira resolves a relative path against the directory of this file.
# The warn level is necessary: a lost call and a shed request log a WARN line, and the rig voids a
# rapira cell on a WARN or ERROR line.
[grpc]
listen = "@@LISTEN@@"
descriptor_set = "@@RIG@@/apps/grpc/bench.binpb"
services = ["bench.v1.EchoService"]

[grpc.pool]
entrypoint = "@@ENTRY@@"
mode = "dispatcher"
processes = @@PROCS@@

[log]
level = "warn"
