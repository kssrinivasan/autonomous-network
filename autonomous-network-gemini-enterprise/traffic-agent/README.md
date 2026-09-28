# traffic-agent

A lightweight, self-contained network traffic generator and receiver written in Go.
A single static binary is baked into every device container image — no external
tools or runtime dependencies are required on the network VM.

## Overview

```
traffic-agent
├── daemon  — long-lived HTTP agent; operator POSTs flows to /v1/flows
├── run     — one-shot source: reads a JSON config, generates traffic, exits
└── serve   — one-shot destination: receives traffic on a port for N seconds
```

In normal operation the binary runs as a **daemon** inside each device container.
The operator manages traffic tests by calling the REST API; no container re-starts
or Ansible playbooks are needed per-test.

The binary is built as a fully static Linux/amd64 executable (CGO disabled) and
published to Artifact Registry via Cloud Build. The device Dockerfile pulls the
binary at build time using a multi-stage `COPY --from=<carrier-image>` so no Go
toolchain is needed on the network VM.

## Architecture

```
Artifact Registry
  └── traffic-agent:<version>  (scratch carrier image — binary only)
        ↓  docker build --build-arg AGENT_IMAGE=...
  device container image
        ├── /opt/device/bin/traffic-agent
        └── entrypoint: exec "$@" (CMD defaults to sleep infinity; daemon started by operator)

Operator (Python/kopf)
  └── HTTP → device management IP
        ├── POST :9090/v1/flows   (destination role — start listener)
        ├── POST :9090/v1/flows   (source role     — start sender)
        └── GET  :9091/metrics    (Ops Agent Prometheus scrape)
```

## Bidirectional Traffic

To simulate realistic application traffic (streaming, web browsing, VoIP) set
`bidirectional: true` on the `TrafficTest` CRD.  The operator starts **two
flows per source device** simultaneously:

| Flow ID | Source agent | Destination agent | Purpose |
|---------|-------------|-------------------|---------|
| `{name}_{source}` | `role=source` on dev1 | `role=destination` on dev2 | forward (client upload / request) |
| `{name}_{source}_rev` | `role=destination` on dev1 | `role=source` on dev2 | reverse (server download / response) |

The reverse flow uses its own independently allocated port and has its own
`bandwidth`, `pattern_type`, and `pattern_config` — set via `reverse_bandwidth`,
`reverse_pattern_type`, and `reverse_pattern_config` in the CRD spec.

### Example — video streaming

`bandwidth` is the source→destination direction (client→server = small control channel).
`reverse_bandwidth` is the destination→source direction (server→client = large video stream).

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: video-stream
spec:
  source_devices: [client]
  destination_device: server
  protocol: UDP
  duration: 3600
  # ── client → server (small control/keepalive channel) ────────────────
  bandwidth: "100Kbps"
  pattern_type: constant
  # ── server → client (large video stream) ─────────────────────────────
  bidirectional: true
  reverse_bandwidth: "5Mbps"
  reverse_pattern_type: constant
```

### Example — web browsing

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: web-browsing
spec:
  source_devices: [client]
  destination_device: server
  protocol: TCP
  duration: 1800
  # ── client → server (small bursty HTTP requests) ─────────────────────
  bandwidth: "1Mbps"
  pattern_type: burst
  pattern_config:
    burst_rate: "1Mbps"
    idle_rate: "10Kbps"
    burst_duration: 1
    burst_interval: 5
  # ── server → client (large bursty HTTP responses) ─────────────────────
  bidirectional: true
  reverse_bandwidth: "20Mbps"
  reverse_pattern_type: burst
  reverse_pattern_config:
    burst_rate: "20Mbps"
    idle_rate: "500Kbps"
    burst_duration: 2
    burst_interval: 5
```

### Prometheus metrics for bidirectional flows

Each device publishes metrics for **both** its forward and reverse flow:

```
# dev1 (client) — forward sender, reverse receiver
traffic_agent_bytes_sent_total{flow_id="web-browsing_client",role="source",...}
traffic_agent_bytes_received_total{flow_id="web-browsing_client_rev",role="destination",...}

# dev2 (server) — forward receiver, reverse sender
traffic_agent_bytes_received_total{flow_id="web-browsing_client",role="destination",...}
traffic_agent_bytes_sent_total{flow_id="web-browsing_client_rev",role="source",...}
```

To query aggregate throughput in both directions for a single test:
```promql
# Total bits/sec in each direction
traffic_agent_throughput_bps{flow_id=~"web-browsing_.*",role="source"}
```

---

## Flows vs Concurrent Sessions

Understanding the two levels of parallelism is important:

| Concept | What it is | Scope |
|---------|-----------|-------|
| **Flow** | One `FlowRequest` registered in the daemon — one `flow_id` | Per source device per TrafficTest |
| **Concurrent sessions** (`concurrent_users` in CRD) | Goroutines spawned *inside* a single flow | Multiple TCP connections / UDP send loops sharing the flow's bandwidth envelope |

**Example:** a TrafficTest with `source_devices: [dev1]` and `concurrent_users: 10`
creates **1 flow** on the agent with **10 parallel sessions** inside it.  All
session metrics are aggregated into that one flow's `metrics.Collector`, so
Prometheus sees one set of labels (`flow_id=mytest_dev1`).

To get 10 independently-labelled flows, list 10 different devices in `source_devices`.

## Traffic Patterns

| Pattern | Description | Key config fields |
|---------|-------------|-------------------|
| `constant` | Steady fixed-rate traffic | `bandwidth` |
| `periodic` | Single sine / square / sawtooth wave | `wave_type`, `period`, `base_rate`, `amplitude` |
| `burst` | High/low alternating bursts | `burst_rate`, `idle_rate`, `burst_duration`, `burst_interval` |
| `poisson` | Stochastic Poisson user arrivals | `arrival_rate`, `concurrent_users`, `session_duration` |
| `multi_sine` | N superimposed sine waves with optional wall-clock anchor | `base_rate`, `components[]`, `noise_stddev_pct`, `time_reference` |
| `schedule` | Piecewise-linear (or step) bandwidth profile with HH:MM waypoints | `waypoints[]`, `interpolation`, `repeat`, `time_reference` |

### `multi_sine` — time-of-day modelling

Superposes multiple independent sine waves, each with its own `period`,
`amplitude`, and `phase_offset`. When `time_reference: wall_clock` is set the
phase anchor is UTC epoch, so a 24-hour cycle naturally peaks at the same clock
hour every day across all devices — ideal for GNN training data generation.

```yaml
pattern_type: multi_sine
pattern_config:
  base_rate: 50Mbps
  time_reference: wall_clock     # anchor to UTC, not test-start
  noise_stddev_pct: 3.0          # ±3% Gaussian noise
  min_rate: 5Mbps
  max_rate: 95Mbps
  components:
    - period: 86400              # 24-hour daily cycle
      amplitude: 30Mbps
      phase_offset: -28800       # peak at 14:00 UTC
    - period: 604800             # 7-day weekly cycle
      amplitude: 10Mbps
      phase_offset: 0
```

### `schedule` — business-hours simulation

Interpolates (linearly or as steps) between named bandwidth waypoints tied to
UTC wall-clock time.

```yaml
pattern_type: schedule
pattern_config:
  time_reference: wall_clock
  interpolation: linear          # or "step"
  repeat: daily
  waypoints:
    - time: "00:00"
      rate: "5Mbps"
    - time: "09:00"
      rate: "80Mbps"
    - time: "12:30"
      rate: "60Mbps"
    - time: "14:00"
      rate: "90Mbps"
    - time: "18:00"
      rate: "30Mbps"
    - time: "22:00"
      rate: "10Mbps"
```

## Modes

### `daemon` — HTTP control API + Prometheus metrics

Long-lived process that exposes a REST API for flow management on
`--control-port` (default **9090**) and a Prometheus metrics endpoint on
`--metrics-port` (default **9091**).

```
GET  /v1/health             liveness probe
POST /v1/flows              start a new flow (source or destination role)
GET  /v1/flows              list all active/completed flows
GET  /v1/flows/{id}         get status + metrics for one flow
DELETE /v1/flows/{id}       stop a running flow
```

```
GET  :9091/metrics          Prometheus text exposition (scraped by Ops Agent)
```

**Start a destination listener:**
```bash
curl -s -X POST http://DEVICE_IP:9090/v1/flows \
  -H 'Content-Type: application/json' \
  -d '{"flow_id":"test-1","role":"destination","port":5300,"protocol":"TCP","duration_sec":120}'
```

**Start a source sender:**
```bash
curl -s -X POST http://DEVICE_IP:9090/v1/flows \
  -H 'Content-Type: application/json' \
  -d '{
    "flow_id":             "test-1",
    "role":                "source",
    "destination_ip":      "10.0.1.2",
    "port":                5300,
    "protocol":            "TCP",
    "duration_sec":        120,
    "bandwidth_bps":       100000000,
    "pattern_type":        "multi_sine",
    "concurrent_sessions": 4,
    "pattern_config": {
      "base_rate": "80Mbps",
      "time_reference": "wall_clock",
      "components": [
        {"period": 86400, "amplitude": "20Mbps", "phase_offset": -28800}
      ]
    }
  }'
```

### `run` — one-shot source

Reads a JSON config file, generates traffic for the configured duration, then
writes a results JSON to stdout and exits.

```bash
traffic-agent run --config /tmp/config.json
```

### `serve` — one-shot destination

Listens for inbound traffic on the given port for the specified duration, then
exits.

```bash
traffic-agent serve --port 5300 --protocol TCP --duration 150
```

## Prometheus Metrics

The daemon exposes the following metrics on `:9091/metrics`, scraped by the
Google Cloud Ops Agent:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `traffic_agent_bytes_sent_total` | Counter | `flow_id`, `role`, `protocol` | Total bytes sent by this flow |
| `traffic_agent_bytes_received_total` | Counter | `flow_id`, `role`, `protocol` | Total bytes received by this flow |
| `traffic_agent_throughput_bps` | Gauge | `flow_id`, `role`, `protocol` | Instantaneous throughput in bits/sec (delta since last scrape) |
| `traffic_agent_latency_ms` | Gauge | `flow_id`, `role`, `protocol` | Mean latency in milliseconds (UDP flows only) |
| `traffic_agent_jitter_ms` | Gauge | `flow_id`, `role`, `protocol` | Mean jitter in milliseconds (UDP flows only) |
| `traffic_agent_packet_loss_pct` | Gauge | `flow_id`, `role`, `protocol` | Packet loss percentage (UDP flows only) |
| `traffic_agent_active_sessions` | Gauge | `flow_id`, `role`, `protocol` | Number of concurrent sessions within this flow |
| `traffic_agent_flow_running` | Gauge | `flow_id`, `role`, `protocol` | 1 if phase is `running`, 0 for all other phases |

### Labels

Every metric carries three labels:

| Label | Values | Description |
|-------|--------|-------------|
| `flow_id` | e.g. `mytest_dev1` | Identifies the flow — set by the operator in the `POST /v1/flows` request |
| `role` | `source` \| `destination` | Which end of the flow this agent is running |
| `protocol` | `TCP` \| `UDP` | Transport protocol |

### Source vs Destination: which metrics are meaningful

Both the **source** agent (dev1) and the **destination** agent (dev2) publish
metrics for the **same `flow_id`**.  The `role` label is what differentiates
them.  Not every metric is populated on both sides:

| Metric | `role=source` | `role=destination` |
|--------|:---:|:---:|
| `bytes_sent_total` | ✅ populated | — (always 0) |
| `bytes_received_total` | — (always 0) | ✅ populated |
| `throughput_bps` | ✅ based on sent bytes | ✅ based on received bytes |
| `active_sessions` | ✅ | — (always 0 for TCP/UDP servers) |
| `latency_ms` | ✅ UDP only | — |
| `jitter_ms` | ✅ UDP only | — |
| `packet_loss_pct` | ✅ UDP only | — |

**Example Prometheus queries:**

```promql
# Sent throughput on dev1
traffic_agent_throughput_bps{flow_id="mytest_dev1", role="source"}

# Received throughput on dev2 for the same flow
traffic_agent_throughput_bps{flow_id="mytest_dev1", role="destination"}

# All destination flows currently running
traffic_agent_flow_running{role="destination"} == 1
```

### Throughput calculation

`traffic_agent_throughput_bps` is a **delta-based gauge**, not a raw counter.
On each `/metrics` scrape the agent computes:

```
throughput_bps = (bytes_since_last_scrape) × 8 / elapsed_seconds
```

where `bytes_since_last_scrape = (bytes_sent + bytes_received) − previous_snapshot`.
This gives an instantaneous rate that aligns with the Ops Agent's 15-second
scrape interval.  Do not use `rate()` on this gauge — query it directly.

### Flow phase model

`traffic_agent_flow_running` reflects the flow's lifecycle phase:

| Phase | `flow_running` value |
|-------|---------------------|
| `starting` | `0` |
| `running` | `1` |
| `completed` | `0` |
| `failed` | `0` |
| `stopped` | `0` |

Completed and failed flows remain in the registry (and in `/metrics`) until the
daemon is restarted, so historical `flow_running=0` series remain queryable.

The Ops Agent is configured by the `VyosInfrastructure` operator to scrape
`:9091/metrics` from every device in the topology.

## Package layout

```
traffic-agent/
├── cmd/agent/main.go          Entry point — daemon / run / serve sub-commands
├── internal/
│   ├── api/handler.go         HTTP/JSON REST API (daemon mode)
│   ├── bandwidth/bandwidth.go "100Mbps" → int64 bps parser
│   ├── config/config.go       FlowRequest + OneShotConfig types
│   ├── flowmanager/manager.go Flow registry, lifecycle state machine
│   ├── metrics/
│   │   ├── collector.go       Thread-safe metrics (throughput, latency, loss, jitter)
│   │   └── prometheus.go      Prometheus handler — renders /metrics from FlowSnapshots
│   ├── patterns/
│   │   ├── pattern.go         Pattern interface
│   │   ├── constant.go        Constant rate
│   │   ├── periodic.go        Sine / square / sawtooth
│   │   ├── burst.go           Burst / idle alternation
│   │   ├── poisson.go         Poisson session arrivals
│   │   ├── multisine.go       Composite multi-sine (wall-clock aware)
│   │   ├── schedule.go        Piecewise waypoint schedule
│   │   └── factory.go         Build(patternType, config, ...) dispatcher
│   ├── ratelimit/limiter.go   Token-bucket rate limiter
│   ├── server/
│   │   ├── tcp.go             TCP traffic receiver
│   │   └── udp.go             UDP traffic receiver
│   └── session/manager.go     Concurrent session orchestrator
├── proto/agent.proto          gRPC service definition
├── Dockerfile                 Multi-stage: builder → scratch carrier image
├── Makefile                   build / test / lint / docker targets
└── cloudbuild.tpl.yaml        Cloud Build pipeline template (push to Artifact Registry)
```

## Build

**Local binary (current OS/arch):**
```bash
cd traffic-agent
make build-local
./traffic-agent version
```

**Linux/amd64 static binary:**
```bash
make build        # produces ./traffic-agent (linux/amd64, CGO_ENABLED=0)
```

**Docker carrier image:**
```bash
make docker       # builds traffic-agent:latest locally
```

**Cloud Build (pushes to Artifact Registry):**
```bash
envsubst < cloudbuild.tpl.yaml > cloudbuild.yaml
gcloud builds submit --config cloudbuild.yaml .
```

## Tests

```bash
make test         # go test -v -race ./...
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `info` | Set to `debug` for verbose structured JSON logging |

## Deployment

The device Ansible role (`controller/src/vyosvm/playbooks/roles/3gpp/`) builds the
device image, pulling the traffic-agent binary from Artifact Registry via a
`COPY --from=<carrier-image>` multi-stage build. The binary is installed at
`/opt/device/bin/traffic-agent` (added to `PATH`).

The operator's `lifecycle_tasks.py` calls `POST :9090/v1/flows` directly via
the network VM management IP to start and stop traffic tests.  The Ops Agent
scrapes `:9091/metrics` on a 15-second interval and ships the time-series data
to Cloud Monitoring for the GNN pipeline.
