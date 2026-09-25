-- wrk2 reporter for one stage.
-- The init phase sets the request shape from the environment: WRK_METHOD,
-- WRK_BODY_FILE (a path, or "-" for no body), and WRK_HEADERS (one
-- "name: value" per line). box/load.sh sets these variables.
-- The done phase writes one machine-readable line: RESULT {json}.
-- It uses only the wrk Lua API documented in SCRIPTING.
-- Latency values come from the corrected histogram, in microseconds.
-- wrk2 has no dropped counter, so the dropped key is always 0.

init = function(args)
  local method = os.getenv("WRK_METHOD")
  if method and method ~= "" then
    wrk.method = method
  end

  local path = os.getenv("WRK_BODY_FILE")
  if path and path ~= "" and path ~= "-" then
    local f = assert(io.open(path, "rb"))
    wrk.body = f:read("*a")
    f:close()
  end

  local headers = os.getenv("WRK_HEADERS") or ""
  for line in headers:gmatch("[^\n]+") do
    local name, value = line:match("^([^:]+):%s*(.*)$")
    if name then
      wrk.headers[name] = value
    end
  end
end

-- hdr_mean divides by total_count without a zero guard, so an empty
-- histogram yields NaN. JSON has no NaN, so map it to 0.
local function finite(x)
  if x ~= x or x == math.huge or x == -math.huge then
    return 0
  end
  return x
end

local function int(x)
  return string.format("%.0f", finite(x))
end

done = function(summary, latency, requests)
  local duration_us = summary.duration
  local duration_s = duration_us / 1000000.0
  local rps = 0
  if duration_s > 0 then
    rps = summary.requests / duration_s
  end

  local out = string.format(
    'RESULT {"duration_us":%s,"requests":%s,"bytes":%s,' ..
    '"errors":{"connect":%s,"read":%s,"write":%s,"status":%s,"timeout":%s,"dropped":0},' ..
    '"latency_us":{"mean":%.3f,"p50":%s,"p90":%s,"p95":%s,"p99":%s,' ..
    '"p999":%s,"max":%s},"requests_per_sec":%.3f}',
    int(duration_us), int(summary.requests), int(summary.bytes),
    int(summary.errors.connect), int(summary.errors.read),
    int(summary.errors.write), int(summary.errors.status),
    int(summary.errors.timeout),
    finite(latency.mean),
    int(latency:percentile(50.0)), int(latency:percentile(90.0)),
    int(latency:percentile(95.0)), int(latency:percentile(99.0)),
    int(latency:percentile(99.9)), int(latency.max),
    finite(rps))

  io.write(out, "\n")
end
