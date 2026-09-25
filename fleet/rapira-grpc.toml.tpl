# rapira.toml for the rapira gRPC legs. leg.sh fills the listen, rig and
# processes placeholders. The rig placeholder is the staged rig directory
# ($HOME/bench-rig).
# The paths are absolute: the rendered file is in /opt/bench/run, and rapira
# resolves a relative path against the directory of the file.
# rapira logs at the error level by default. The warn level is necessary: a
# lost call and a shed request log a WARN line, and the rig voids a cell on
# any WARN or ERROR line.
[grpc]
listen = "@@LISTEN@@"
descriptor_set = "@@RIG@@/grpc/bench.binpb"
services = ["bench.v1.EchoService"]

[grpc.pool]
entrypoint = "@@RIG@@/php/grpc/dispatcher.php"
mode = "dispatcher"
processes = @@PROCS@@

[log]
level = "warn"
