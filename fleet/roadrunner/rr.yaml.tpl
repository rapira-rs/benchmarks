# RoadRunner hello leg. fleet-leg.sh renders @@PROCS@@ at start time.
version: '3'

server:
  command: "php worker.php"
  relay: pipes

http:
  address: 0.0.0.0:8080
  pool:
    num_workers: @@PROCS@@

logs:
  level: error
  mode: production
