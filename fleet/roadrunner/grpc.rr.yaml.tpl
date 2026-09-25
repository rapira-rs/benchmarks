# RoadRunner config for the RoadRunner gRPC leg. fleet-leg.sh fills the rig
# and processes placeholders. The rig placeholder is the staged rig directory
# ($HOME/bench-rig).
# rr refuses a config without version "3". max_concurrent_streams 200 is the
# hyper default on the rapira side.
version: "3"
server:
  command: "php -d opcache.enable_cli=1 @@RIG@@/php/grpc/rr-worker.php"
  relay: pipes
grpc:
  listen: "tcp://0.0.0.0:8080"
  proto: ["@@RIG@@/grpc/bench.proto"]
  max_concurrent_streams: 200
  pool:
    num_workers: @@PROCS@@
    max_jobs: 0
logs:
  level: error
  # The grpc and server channels log one ERROR line each for a call that the
  # client cancels. h2load cancels the queued calls at the end of each pass,
  # and the rig voids a cell on any WARN or ERROR line. A failed call still
  # fails the h2load byte rule and the k6 checks.
  # https://docs.roadrunner.dev/docs/logging-and-observability/logger#channels
  channels:
    grpc:
      level: panic
    server:
      level: panic
