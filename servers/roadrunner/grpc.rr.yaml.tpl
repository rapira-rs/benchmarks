# RoadRunner config of the RoadRunner gRPC target. box/servers/roadrunner.sh renders the placeholders.
# rr needs version "3". max_concurrent_streams 200 is the hyper default of the rapira gRPC listener.
# The PHP workers get PHPRC and GRPC_VENDOR from the environment of rr.
version: "3"
server:
  command: "php @@RIG@@/apps/grpc/php/rr-worker.php"
  relay: pipes
grpc:
  listen: "tcp://@@LISTEN@@"
  proto: ["@@RIG@@/apps/grpc/bench.proto"]
  max_concurrent_streams: 200
  pool:
    num_workers: @@PROCS@@
    max_jobs: 0
logs:
  level: error
  # The grpc and server channels log one ERROR line for each call that the client cancels. The
  # load tool cancels the open calls at the end of a stage, and these lines only grow the log.
  # https://docs.roadrunner.dev/docs/logging-and-observability/logger#channels
  channels:
    grpc:
      level: panic
    server:
      level: panic
