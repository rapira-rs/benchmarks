# RoadRunner config for the RoadRunner gRPC leg. fleet-leg.sh fills @@RIG@@
# (the staged rig directory, $HOME/bench-rig) and @@PROCS@@.
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
