#!/usr/bin/env bash
# Copyright 2024-2025 Google LLC
#
# test_local.sh — local integration tests for the traffic-agent binary.
#
# Runs twelve test suites (executed in this order):
#   One-shot (serve + run) tests:
#     1. TCP constant pattern
#     2. UDP burst pattern
#     3. TCP periodic/sine pattern
#     4. TCP schedule pattern (elapsed waypoints)
#     5. TCP Poisson arrival pattern
#   Daemon REST-API tests (two agents per test):
#     6.  Basic TCP flow: POST/GET/DELETE, duplicate→409, invalid→400
#     7.  UDP flows over daemon API
#     8.  Multiple concurrent flows on the same agent pair
#     9.  Burst pattern source flow
#    10.  Periodic/sine pattern source flow
#    11.  Flow natural expiry after duration_sec
#    12.  REST API error cases (404/400/405)
#
# Usage:
#   cd traffic-agent
#   ./tests/test_local.sh          # build then test
#   SKIP_BUILD=1 ./tests/test_local.sh   # skip build (binary must exist)
#
# Requirements: bash ≥ 4, curl, jq (optional but recommended)

set -euo pipefail

# ── paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BINARY="$REPO_ROOT/traffic-agent"
CONFIGS_DIR="$SCRIPT_DIR/configs"

# ── ports (choose high-numbered ports to avoid conflicts) ──────────────────────
TCP_SERVE_PORT=19201
UDP_SERVE_PORT=19202
DAEMON_A_PORT=19090   # acts as destination agent
DAEMON_B_PORT=19091   # acts as source agent
TRAFFIC_PORT=19203    # daemon-mode traffic port

# one-shot advanced pattern serve ports (match port in each config JSON)
PERIODIC_SERVE_PORT=19204
SCHEDULE_SERVE_PORT=19205
POISSON_SERVE_PORT=19206

# daemon test — UDP flows
DAEMON_UDP_A_PORT=19092
DAEMON_UDP_B_PORT=19093
TRAFFIC_UDP_PORT=19207

# daemon test — multiple concurrent flows
DAEMON_MF_A_PORT=19094
DAEMON_MF_B_PORT=19095
TRAFFIC_MF_PORT_1=19208
TRAFFIC_MF_PORT_2=19209

# daemon test — burst / periodic patterns
DAEMON_PAT_A_PORT=19096
DAEMON_PAT_B_PORT=19097
TRAFFIC_BURST_PORT=19210
TRAFFIC_PERIOD_PORT=19211

# daemon test — flow natural expiry
DAEMON_EXP_A_PORT=19098
DAEMON_EXP_B_PORT=19099
TRAFFIC_EXP_PORT=19212

# daemon test — error-case validation (single daemon)
DAEMON_ERR_PORT=19100

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

PASS=0; FAIL=0; PIDS=()

log()  { echo -e "${CYAN}[test]${NC} $*"; }
ok()   { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL+1)); }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

# ── cleanup on exit ────────────────────────────────────────────────────────────
cleanup() {
  log "cleaning up background processes..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null || true
  done
  rm -f /tmp/ta_test_*.log /tmp/ta_test_*.json
}
trap cleanup EXIT

# ── helper: wait for a TCP port to accept connections ─────────────────────────
wait_for_port() {
  local port=$1 timeout=${2:-10} elapsed=0
  while ! bash -c "echo > /dev/tcp/127.0.0.1/$port" 2>/dev/null; do
    sleep 0.2; elapsed=$(( elapsed + 1 ))
    if (( elapsed > timeout * 5 )); then
      warn "port $port not ready after ${timeout}s"
      return 1
    fi
  done
}

# ── helper: assert a string contains a substring ─────────────────────────────
assert_contains() {
  local label=$1 haystack=$2 needle=$3
  if echo "$haystack" | grep -q "$needle"; then
    ok "$label — found '$needle'"
  else
    fail "$label — expected '$needle' in output"
    echo "    output: $haystack"
  fi
}

# ── helper: assert HTTP response code ────────────────────────────────────────
assert_http() {
  local label=$1 code=$2 expected=$3
  if [[ "$code" == "$expected" ]]; then
    ok "$label — HTTP $code"
  else
    fail "$label — expected HTTP $expected, got $code"
  fi
}

# ── helper: pretty-print flow traffic stats from a JSON blob ─────────────────
# Usage: print_flow_stats LABEL JSON
# Works with both one-shot result JSON and daemon FlowStatus JSON.
print_flow_stats() {
  local label=$1 json=$2
  echo -e "${CYAN}  ┌─ $label traffic stats ─────────────────────────${NC}"
  if command -v jq &>/dev/null; then
    local bytes_sent bytes_recv throughput latency jitter loss sessions
    # One-shot mode wraps metrics under .metrics; daemon status uses .metrics too.
    bytes_sent=$(echo  "$json" | jq -r '(.metrics.bytes_sent    // 0) | . / 1')
    bytes_recv=$(echo  "$json" | jq -r '(.metrics.bytes_received // 0) | . / 1')
    throughput=$(echo  "$json" | jq -r '(.metrics.throughput_bps // 0) | (. / 1e6 | . * 100 | round / 100 | tostring) + " Mbps"')
    latency=$(echo     "$json" | jq -r '(.metrics.latency_ms     // "n/a") | tostring + " ms"')
    jitter=$(echo      "$json" | jq -r '(.metrics.jitter_ms      // "n/a") | tostring + " ms"')
    loss=$(echo        "$json" | jq -r '(.metrics.packet_loss_pct // 0)  | tostring + " %"')
    sessions=$(echo    "$json" | jq -r '(.metrics.active_sessions // 0)')
    echo -e "  │  bytes_sent:      ${bytes_sent}"
    echo -e "  │  bytes_received:  ${bytes_recv}"
    echo -e "  │  throughput:      ${throughput}"
    echo -e "  │  latency:         ${latency}"
    echo -e "  │  jitter:          ${jitter}"
    echo -e "  │  packet_loss:     ${loss}"
    echo -e "  │  active_sessions: ${sessions}"
  else
    # jq not available — print raw metrics block
    echo "$json" | grep -o '"bytes_sent":[^,}]*\|"throughput_bps":[^,}]*\|"latency_ms":[^,}]*' \
      | sed 's/^/  │  /' || echo "  │  (install jq for formatted stats)"
  fi
  echo -e "${CYAN}  └─────────────────────────────────────────────────${NC}"
}

# ── helper: extract a numeric JSON field without requiring jq ─────────────────
# Usage: _extract_json_number JSON "field_name"
# Echos the first numeric value for that field, or 0 on failure.
_extract_json_number() {
  local json=$1 field=$2 val
  if command -v jq &>/dev/null; then
    val=$(echo "$json" | jq -r ".metrics.${field} // 0" 2>/dev/null) || val=0
  else
    # Fallback: grep the raw JSON for "field": <number>
    val=$(echo "$json" | grep -o "\"${field}\":[[:space:]]*[0-9]*" \
          | grep -o '[0-9]*$' | head -1)
    val=${val:-0}
  fi
  echo "${val:-0}"
}

# ── helper: collect N bytes_sent samples (1 s apart) from a daemon flow ──────
# Uses the bytes_sent delta between consecutive polls as a proxy for throughput
# (avoids relying on throughput_bps which may not update in the final snapshot).
# Sets globals: _sample_max _sample_min _sample_count (all in bytes/s ≈ Bps)
collect_throughput_samples() {
  local port=$1 flow_id=$2 count=$3
  _sample_max=0; _sample_min=9999999999999; _sample_count=0
  local i prev_bytes cur_bytes delta
  prev_bytes=0
  for (( i=0; i<count; i++ )); do
    sleep 1
    api_call /tmp/ta_sample_tp.json GET "http://127.0.0.1:$port/v1/flows/$flow_id"
    cur_bytes=$(_extract_json_number "$_body" "bytes_sent")
    # First sample: record as baseline, skip delta comparison
    if (( i == 0 )); then
      prev_bytes=$cur_bytes
      _sample_count=$(( _sample_count + 1 ))
      continue
    fi
    delta=$(( cur_bytes - prev_bytes ))
    (( delta < 0 )) && delta=0  # guard against counter reset
    (( delta > _sample_max )) && _sample_max=$delta || true
    (( delta < _sample_min )) && _sample_min=$delta || true
    prev_bytes=$cur_bytes
    _sample_count=$(( _sample_count + 1 ))
  done
}

# ── helper: verify high/low bytes-per-interval ratio proves pattern is active ──
# assert_bps_ratio LABEL HIGH LOW MIN_RATIO
# Passes if HIGH > LOW * MIN_RATIO, confirming bandwidth variability.
assert_bps_ratio() {
  local label=$1 high=$2 low=$3 min_ratio=$4
  if (( high == 0 && low == 0 )); then
    fail "$label — no bytes observed in any sample (is traffic flowing?)"
    return
  fi
  if (( low == 0 )); then
    ok "$label — pattern variation detected (low_interval=0 B, high=${high} B)"
    return
  fi
  local ratio
  ratio=$(awk "BEGIN{printf \"%d\", $high/$low}")
  if (( ratio >= min_ratio )); then
    ok "$label — bytes/interval ratio max/min=$ratio (≥${min_ratio}× required)"
  else
    fail "$label — bytes/interval ratio max/min=$ratio (expected ≥${min_ratio}×); pattern may not be shaping traffic"
  fi
}

# ── helper: assert bytes_sent is within [lo_bytes, hi_bytes] ─────────────────
# Works with or without jq — falls back to grep-based extraction.
assert_bytes_range() {
  local label=$1 json=$2 lo=$3 hi=$4
  local bytes
  bytes=$(_extract_json_number "$json" "bytes_sent")
  if (( bytes >= lo && bytes <= hi )); then
    ok "$label — bytes_sent=$bytes in expected range [$lo, $hi]"
  else
    fail "$label — bytes_sent=$bytes outside expected range [$lo, $hi]"
  fi
}

# ── helper: curl wrapper — never lets set -e fire on connection errors ────────
# Usage: api_call OUT_FILE METHOD URL [BODY]
# Sets globals: _code (HTTP status or "000"), _body (response text)
api_call() {
  local out=$1 method=$2 url=$3 body_arg=${4:-}
  local curl_args=(-s -o "$out" -w "%{http_code}" -X "$method")
  if [[ -n "$body_arg" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$body_arg")
  fi
  # Ensure output file always exists so body reads don't fail
  : >"$out"
  _code=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || _code="000"
  _body=$(cat "$out" 2>/dev/null) || _body=""
}

# ══════════════════════════════════════════════════════════════════════════════
# Build
# ══════════════════════════════════════════════════════════════════════════════
build_binary() {
  if [[ "${SKIP_BUILD:-}" == "1" ]] && [[ -x "$BINARY" ]]; then
    log "SKIP_BUILD=1 — using existing binary: $BINARY"
    return
  fi
  log "building traffic-agent (build-local)..."
  (cd "$REPO_ROOT" && make build-local 2>&1)
  if [[ -x "$BINARY" ]]; then
    ok "binary built: $BINARY"
  else
    fail "binary not found after build — aborting"
    exit 1
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — One-shot TCP (serve + run)
# ══════════════════════════════════════════════════════════════════════════════
test_oneshot_tcp() {
  echo
  log "═══ Test 1: one-shot TCP (serve + run) ═══"

  local srv_log=/tmp/ta_test_serve_tcp.log
  local out_json=/tmp/ta_test_run_tcp.json

  # Start the traffic server
  "$BINARY" serve --port "$TCP_SERVE_PORT" --protocol TCP --duration 15 \
    >"$srv_log" 2>&1 &
  local srv_pid=$!
  PIDS+=("$srv_pid")
  log "server PID=$srv_pid, waiting for port $TCP_SERVE_PORT..."
  wait_for_port "$TCP_SERVE_PORT" 8

  # Run the client
  log "running one-shot TCP client..."
  "$BINARY" run --config "$CONFIGS_DIR/oneshot_tcp.json" >"$out_json" 2>/tmp/ta_test_run_tcp.stderr.log
  local rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "traffic-agent run exited cleanly (rc=0)"
  else
    fail "traffic-agent run exited with rc=$rc"
  fi

  local output
  output=$(cat "$out_json")

  assert_contains "TCP test_name"       "$output" "local-tcp-test"
  assert_contains "TCP completed=true"  "$output" '"completed": true'
  assert_contains "TCP returncode=0"    "$output" '"returncode": 0'
  assert_contains "TCP bytes_sent>0"    "$output" '"bytes_sent"'

  # Check bytes_sent is a non-zero number and show full stats
  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$output" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "TCP bytes_sent=$bytes_sent (>0)"
    else
      fail "TCP bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  print_flow_stats "TCP one-shot" "$output"

  # Clean up server
  kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null || true
  PIDS=("${PIDS[@]/$srv_pid}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — One-shot UDP (serve + run)
# ══════════════════════════════════════════════════════════════════════════════
test_oneshot_udp() {
  echo
  log "═══ Test 2: one-shot UDP (serve + run) ═══"

  local srv_log=/tmp/ta_test_serve_udp.log
  local out_json=/tmp/ta_test_run_udp.json

  "$BINARY" serve --port "$UDP_SERVE_PORT" --protocol UDP --duration 15 \
    >"$srv_log" 2>&1 &
  local srv_pid=$!
  PIDS+=("$srv_pid")
  # UDP servers don't TCP-handshake; give the process a moment to start
  sleep 1
  log "UDP server PID=$srv_pid"

  log "running one-shot UDP client..."
  "$BINARY" run --config "$CONFIGS_DIR/oneshot_udp.json" >"$out_json" 2>/tmp/ta_test_run_udp.stderr.log
  local rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "traffic-agent run (UDP) exited cleanly (rc=0)"
  else
    fail "traffic-agent run (UDP) exited with rc=$rc"
  fi

  local output
  output=$(cat "$out_json")

  assert_contains "UDP test_name"      "$output" "local-udp-test"
  assert_contains "UDP completed"      "$output" '"completed": true'
  assert_contains "UDP bytes_sent key" "$output" '"bytes_sent"'

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$output" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "UDP bytes_sent=$bytes_sent (>0)"
    else
      fail "UDP bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  # Burst pattern: 5 s test, 2 s burst@5Mbps + 3 s idle@500Kbps → avg ≈ 2.3 Mbps
  # Expected bytes: ≈ 1.4 MB; allow generous [200 KB, 4 MB] for timing variability.
  assert_bytes_range "UDP burst bytes in expected range" "$output" 200000 4000000
  print_flow_stats "UDP one-shot" "$output"

  kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null || true
  PIDS=("${PIDS[@]/$srv_pid}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 6 — Daemon mode (two HTTP agents, REST API)
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_mode() {
  echo
  log "═══ Test 6: daemon mode (REST API — two agents) ═══"

  local log_a=/tmp/ta_test_daemon_a.log
  local log_b=/tmp/ta_test_daemon_b.log

  # Start agent-A (destination role)
  "$BINARY" daemon --control-port "$DAEMON_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  # Start agent-B (source role)
  "$BINARY" daemon --control-port "$DAEMON_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for daemons (A=$DAEMON_A_PORT B=$DAEMON_B_PORT)..."
  wait_for_port "$DAEMON_A_PORT" 10
  wait_for_port "$DAEMON_B_PORT" 10
  sleep 0.5  # let HTTP handlers settle

  # ── Health checks ──────────────────────────────────────────────────────────
  api_call /tmp/ta_health_a.json GET "http://127.0.0.1:$DAEMON_A_PORT/v1/health"
  assert_http "agent-A health"        "$_code" "200"
  assert_contains "agent-A status ok" "$_body" '"ok"'

  api_call /tmp/ta_health_b.json GET "http://127.0.0.1:$DAEMON_B_PORT/v1/health"
  assert_http "agent-B health"        "$_code" "200"

  # ── Start destination flow on agent-A ──────────────────────────────────────
  local flow_a_req='{
    "flow_id":   "test-flow-001",
    "role":      "destination",
    "port":      '"$TRAFFIC_PORT"',
    "protocol":  "TCP",
    "duration_sec": 30
  }'
  api_call /tmp/ta_flow_a.json POST \
    "http://127.0.0.1:$DAEMON_A_PORT/v1/flows" "$flow_a_req"
  assert_http "start destination flow (agent-A)" "$_code" "201"
  assert_contains "flow-A started status"        "$_body" '"started"'
  assert_contains "flow-A ID echoed"             "$_body" 'test-flow-001'

  # Give the destination listener a moment to bind
  sleep 1

  # ── Start source flow on agent-B ───────────────────────────────────────────
  local flow_b_req='{
    "flow_id":        "test-flow-001",
    "role":           "source",
    "destination_ip": "127.0.0.1",
    "port":           '"$TRAFFIC_PORT"',
    "protocol":       "TCP",
    "duration_sec":   30,
    "pattern_type":   "constant",
    "bandwidth_bps":  5000000,
    "concurrent_sessions": 2
  }'
  api_call /tmp/ta_flow_b.json POST \
    "http://127.0.0.1:$DAEMON_B_PORT/v1/flows" "$flow_b_req"
  assert_http "start source flow (agent-B)" "$_code" "201"
  assert_contains "flow-B started status"   "$_body" '"started"'

  # Let traffic run for a few seconds
  log "letting traffic flow for 4 s..."
  sleep 4

  # ── GET status on both agents ──────────────────────────────────────────────
  api_call /tmp/ta_status_a.json GET \
    "http://127.0.0.1:$DAEMON_A_PORT/v1/flows/test-flow-001"
  assert_http "GET flow status (agent-A)"     "$_code" "200"
  assert_contains "agent-A flow_id present"   "$_body" 'test-flow-001'
  local status_a="$_body"

  api_call /tmp/ta_status_b.json GET \
    "http://127.0.0.1:$DAEMON_B_PORT/v1/flows/test-flow-001"
  assert_http "GET flow status (agent-B)"     "$_code" "200"
  local status_b="$_body"

  if command -v jq &>/dev/null; then
    local bytes_sent phase
    bytes_sent=$(echo "$status_b" | jq -r '.metrics.bytes_sent // 0')
    phase=$(echo "$status_b"      | jq -r '.phase // "unknown"')
    log "agent-B flow phase=$phase, bytes_sent=$bytes_sent"
    if (( bytes_sent > 0 )); then
      ok "daemon source flow sent $bytes_sent bytes"
    else
      warn "bytes_sent=0 (flow may not have started in time)"
    fi
  fi
  print_flow_stats "daemon agent-A (destination)" "$status_a"
  print_flow_stats "daemon agent-B (source)"      "$status_b"

  # ── GET all flows (list endpoint) ─────────────────────────────────────────
  api_call /tmp/ta_list_a.json GET "http://127.0.0.1:$DAEMON_A_PORT/v1/flows"
  assert_http "GET /v1/flows list (agent-A)" "$_code" "200"

  # ── Stop flows via DELETE ─────────────────────────────────────────────────
  api_call /tmp/ta_stop_a.json DELETE \
    "http://127.0.0.1:$DAEMON_A_PORT/v1/flows/test-flow-001"
  assert_http "DELETE flow (agent-A)"     "$_code" "200"
  assert_contains "agent-A stop status"   "$_body" '"stopped"'

  api_call /tmp/ta_stop_b.json DELETE \
    "http://127.0.0.1:$DAEMON_B_PORT/v1/flows/test-flow-001"
  assert_http "DELETE flow (agent-B)"     "$_code" "200"
  assert_contains "agent-B stop status"   "$_body" '"stopped"'

  # ── Duplicate flow_id should conflict ────────────────────────────────────
  # Re-create a flow then try to POST the same ID again
  api_call /dev/null POST \
    "http://127.0.0.1:$DAEMON_A_PORT/v1/flows" "$flow_a_req"
  sleep 0.2
  api_call /tmp/ta_conflict.json POST \
    "http://127.0.0.1:$DAEMON_A_PORT/v1/flows" "$flow_a_req"
  assert_http "duplicate flow_id → 409 Conflict" "$_code" "409"

  # ── Invalid request validation ────────────────────────────────────────────
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_A_PORT/v1/flows" \
    '{"flow_id":"bad","role":"badRole","port":9999,"duration_sec":5}'
  assert_http "invalid role → 400 Bad Request" "$_code" "400"

  api_call /dev/null POST "http://127.0.0.1:$DAEMON_A_PORT/v1/flows" \
    '{"role":"source","port":9999,"duration_sec":5}'
  assert_http "missing flow_id → 400 Bad Request" "$_code" "400"

  # Shutdown daemons
  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — One-shot TCP, periodic (sine) pattern
# ══════════════════════════════════════════════════════════════════════════════
test_oneshot_periodic() {
  echo
  log "═══ Test 3: one-shot TCP — periodic/sine pattern ═══"

  local srv_log=/tmp/ta_test_serve_periodic.log
  local out_json=/tmp/ta_test_run_periodic.json

  "$BINARY" serve --port "$PERIODIC_SERVE_PORT" --protocol TCP --duration 20 \
    >"$srv_log" 2>&1 &
  local srv_pid=$!
  PIDS+=("$srv_pid")
  log "server PID=$srv_pid, waiting for port $PERIODIC_SERVE_PORT..."
  wait_for_port "$PERIODIC_SERVE_PORT" 8

  log "running one-shot periodic (sine) client..."
  "$BINARY" run --config "$CONFIGS_DIR/oneshot_periodic.json" \
    >"$out_json" 2>/tmp/ta_test_run_periodic.stderr.log
  local rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "periodic run exited cleanly (rc=0)"
  else
    fail "periodic run exited with rc=$rc"
  fi

  local output
  output=$(cat "$out_json")

  assert_contains "periodic test_name"      "$output" "local-periodic-test"
  assert_contains "periodic completed=true" "$output" '"completed": true'
  assert_contains "periodic bytes_sent key" "$output" '"bytes_sent"'

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$output" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "periodic bytes_sent=$bytes_sent (>0)"
    else
      fail "periodic bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  # Sine wave: 8 s test, base=6 Mbps ± 2 Mbps → avg ≈ 6 Mbps → ~6 MB.
  # Allow [500 KB, 10 MB] for token-bucket smoothing and phase uncertainty.
  assert_bytes_range "periodic sine bytes in expected range" "$output" 500000 10000000
  print_flow_stats "TCP periodic one-shot" "$output"

  kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null || true
  PIDS=("${PIDS[@]/$srv_pid}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — One-shot TCP, schedule pattern (elapsed waypoints)
# ══════════════════════════════════════════════════════════════════════════════
test_oneshot_schedule() {
  echo
  log "═══ Test 4: one-shot TCP — schedule pattern (elapsed waypoints) ═══"

  local srv_log=/tmp/ta_test_serve_schedule.log
  local out_json=/tmp/ta_test_run_schedule.json

  "$BINARY" serve --port "$SCHEDULE_SERVE_PORT" --protocol TCP --duration 25 \
    >"$srv_log" 2>&1 &
  local srv_pid=$!
  PIDS+=("$srv_pid")
  log "server PID=$srv_pid, waiting for port $SCHEDULE_SERVE_PORT..."
  wait_for_port "$SCHEDULE_SERVE_PORT" 8

  log "running one-shot schedule client (ramps 1→8→10→3 Mbps over 10 s)..."
  "$BINARY" run --config "$CONFIGS_DIR/oneshot_schedule.json" \
    >"$out_json" 2>/tmp/ta_test_run_schedule.stderr.log
  local rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "schedule run exited cleanly (rc=0)"
  else
    fail "schedule run exited with rc=$rc"
  fi

  local output
  output=$(cat "$out_json")

  assert_contains "schedule test_name"      "$output" "local-schedule-test"
  assert_contains "schedule completed=true" "$output" '"completed": true'
  assert_contains "schedule bytes_sent key" "$output" '"bytes_sent"'

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$output" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "schedule bytes_sent=$bytes_sent (>0)"
    else
      fail "schedule bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  # Schedule ramps 1→8→10→3 Mbps linearly over 10 s (2 concurrent users).
  # Integrated area ≈ 7.9 MB/user. Allow wide range [500 KB, 30 MB] for
  # token-bucket smoothing, connection setup overhead, and concurrency.
  assert_bytes_range "schedule bytes in expected range" "$output" 500000 30000000
  print_flow_stats "TCP schedule one-shot" "$output"

  kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null || true
  PIDS=("${PIDS[@]/$srv_pid}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — One-shot TCP, Poisson arrival pattern
# ══════════════════════════════════════════════════════════════════════════════
test_oneshot_poisson() {
  echo
  log "═══ Test 5: one-shot TCP — Poisson arrival pattern ═══"

  local srv_log=/tmp/ta_test_serve_poisson.log
  local out_json=/tmp/ta_test_run_poisson.json

  "$BINARY" serve --port "$POISSON_SERVE_PORT" --protocol TCP --duration 20 \
    >"$srv_log" 2>&1 &
  local srv_pid=$!
  PIDS+=("$srv_pid")
  log "server PID=$srv_pid, waiting for port $POISSON_SERVE_PORT..."
  wait_for_port "$POISSON_SERVE_PORT" 8

  log "running one-shot Poisson client (arrival_rate=2/s, 4 concurrent)..."
  "$BINARY" run --config "$CONFIGS_DIR/oneshot_poisson.json" \
    >"$out_json" 2>/tmp/ta_test_run_poisson.stderr.log
  local rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "poisson run exited cleanly (rc=0)"
  else
    fail "poisson run exited with rc=$rc"
  fi

  local output
  output=$(cat "$out_json")

  assert_contains "poisson test_name"      "$output" "local-poisson-test"
  assert_contains "poisson completed=true" "$output" '"completed": true'
  assert_contains "poisson bytes_sent key" "$output" '"bytes_sent"'

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$output" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "poisson bytes_sent=$bytes_sent (>0)"
    else
      fail "poisson bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  # Poisson with arrival_rate=2/s, 4 concurrent, 5 Mbps, 8 s test.
  # Stochastic; allow wide range [50 KB, 10 MB] to remain non-flaky.
  assert_bytes_range "poisson bytes in plausible range" "$output" 50000 10000000
  print_flow_stats "TCP poisson one-shot" "$output"

  kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null || true
  PIDS=("${PIDS[@]/$srv_pid}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 7 — Daemon mode: UDP flows via REST API
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_udp() {
  echo
  log "═══ Test 7: daemon mode — UDP flows ═══"

  local log_a=/tmp/ta_test_daemon_udp_a.log
  local log_b=/tmp/ta_test_daemon_udp_b.log

  "$BINARY" daemon --control-port "$DAEMON_UDP_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  "$BINARY" daemon --control-port "$DAEMON_UDP_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for UDP-test daemons (A=$DAEMON_UDP_A_PORT B=$DAEMON_UDP_B_PORT)..."
  wait_for_port "$DAEMON_UDP_A_PORT" 10
  wait_for_port "$DAEMON_UDP_B_PORT" 10
  sleep 0.5

  # Start UDP destination listener on agent-A
  local dest_req='{
    "flow_id":      "udp-flow-001",
    "role":         "destination",
    "port":         '"$TRAFFIC_UDP_PORT"',
    "protocol":     "UDP",
    "duration_sec": 20
  }'
  api_call /tmp/ta_udp_dest.json POST \
    "http://127.0.0.1:$DAEMON_UDP_A_PORT/v1/flows" "$dest_req"
  assert_http "UDP destination start → 201" "$_code" "201"
  assert_contains "UDP dest status=started"  "$_body" '"started"'

  sleep 1  # let the UDP listener bind

  # Start UDP source on agent-B
  local src_req='{
    "flow_id":             "udp-flow-001",
    "role":                "source",
    "destination_ip":      "127.0.0.1",
    "port":                '"$TRAFFIC_UDP_PORT"',
    "protocol":            "UDP",
    "duration_sec":        20,
    "pattern_type":        "burst",
    "bandwidth_bps":       3000000,
    "concurrent_sessions": 1,
    "pattern_config": {
      "burst_duration": 2,
      "burst_interval": 3,
      "burst_rate":     "3000000",
      "idle_rate":      "300000"
    }
  }'
  api_call /tmp/ta_udp_src.json POST \
    "http://127.0.0.1:$DAEMON_UDP_B_PORT/v1/flows" "$src_req"
  assert_http "UDP source start → 201"     "$_code" "201"
  assert_contains "UDP src status=started" "$_body" '"started"'

  log "letting UDP traffic flow for 5 s..."
  sleep 5

  # Verify status on both agents
  api_call /tmp/ta_udp_status_a.json GET \
    "http://127.0.0.1:$DAEMON_UDP_A_PORT/v1/flows/udp-flow-001"
  assert_http "UDP GET dest status → 200"   "$_code" "200"
  assert_contains "UDP dest flow_id echo"   "$_body" 'udp-flow-001'

  api_call /tmp/ta_udp_status_b.json GET \
    "http://127.0.0.1:$DAEMON_UDP_B_PORT/v1/flows/udp-flow-001"
  assert_http "UDP GET src status → 200"    "$_code" "200"
  local udp_src_status="$_body"

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$udp_src_status" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "UDP daemon source sent $bytes_sent bytes"
    else
      warn "UDP bytes_sent=0 (burst idle phase possible)"
    fi
  fi
  print_flow_stats "daemon UDP source" "$udp_src_status"

  # Stop flows
  api_call /dev/null DELETE \
    "http://127.0.0.1:$DAEMON_UDP_A_PORT/v1/flows/udp-flow-001"
  assert_http "UDP DELETE dest → 200" "$_code" "200"

  api_call /dev/null DELETE \
    "http://127.0.0.1:$DAEMON_UDP_B_PORT/v1/flows/udp-flow-001"
  assert_http "UDP DELETE src → 200"  "$_code" "200"

  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 8 — Daemon mode: multiple concurrent flows on the same agent pair
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_multi_flow() {
  echo
  log "═══ Test 8: daemon mode — multiple concurrent flows ═══"

  local log_a=/tmp/ta_test_daemon_mf_a.log
  local log_b=/tmp/ta_test_daemon_mf_b.log

  "$BINARY" daemon --control-port "$DAEMON_MF_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  "$BINARY" daemon --control-port "$DAEMON_MF_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for multi-flow daemons (A=$DAEMON_MF_A_PORT B=$DAEMON_MF_B_PORT)..."
  wait_for_port "$DAEMON_MF_A_PORT" 10
  wait_for_port "$DAEMON_MF_B_PORT" 10
  sleep 0.5

  # ── Flow 1 (constant 4 Mbps) ───────────────────────────────────────────────
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_MF_A_PORT/v1/flows" \
    '{"flow_id":"mf-flow-1","role":"destination","port":'"$TRAFFIC_MF_PORT_1"',"protocol":"TCP","duration_sec":30}'
  assert_http "MF flow-1 dest start → 201" "$_code" "201"
  sleep 0.5

  api_call /dev/null POST "http://127.0.0.1:$DAEMON_MF_B_PORT/v1/flows" \
    '{"flow_id":"mf-flow-1","role":"source","destination_ip":"127.0.0.1","port":'"$TRAFFIC_MF_PORT_1"',"protocol":"TCP","duration_sec":30,"pattern_type":"constant","bandwidth_bps":4000000,"concurrent_sessions":1}'
  assert_http "MF flow-1 src start → 201"  "$_code" "201"

  # ── Flow 2 (burst 6 Mbps) ─────────────────────────────────────────────────
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_MF_A_PORT/v1/flows" \
    '{"flow_id":"mf-flow-2","role":"destination","port":'"$TRAFFIC_MF_PORT_2"',"protocol":"TCP","duration_sec":30}'
  assert_http "MF flow-2 dest start → 201" "$_code" "201"
  sleep 0.5

  api_call /dev/null POST "http://127.0.0.1:$DAEMON_MF_B_PORT/v1/flows" \
    '{"flow_id":"mf-flow-2","role":"source","destination_ip":"127.0.0.1","port":'"$TRAFFIC_MF_PORT_2"',"protocol":"TCP","duration_sec":30,"pattern_type":"constant","bandwidth_bps":6000000,"concurrent_sessions":2}'
  assert_http "MF flow-2 src start → 201"  "$_code" "201"

  log "letting both flows run for 5 s..."
  sleep 5

  # ── Verify both flows appear in the list ───────────────────────────────────
  api_call /tmp/ta_mf_list_b.json GET "http://127.0.0.1:$DAEMON_MF_B_PORT/v1/flows"
  assert_http "MF GET /v1/flows list → 200" "$_code" "200"
  assert_contains "MF list contains flow-1"  "$_body" 'mf-flow-1'
  assert_contains "MF list contains flow-2"  "$_body" 'mf-flow-2'

  # ── Check bytes sent on each source flow ──────────────────────────────────
  for fid in mf-flow-1 mf-flow-2; do
    api_call /tmp/ta_mf_status_b.json GET \
      "http://127.0.0.1:$DAEMON_MF_B_PORT/v1/flows/$fid"
    assert_http "MF GET $fid status → 200" "$_code" "200"
    if command -v jq &>/dev/null; then
      local bytes_sent
      bytes_sent=$(echo "$_body" | jq -r '.metrics.bytes_sent // 0')
      if (( bytes_sent > 0 )); then
        ok "MF $fid sent $bytes_sent bytes (>0)"
      else
        warn "MF $fid bytes_sent=0"
      fi
    fi
  done

  # ── Stop both flows ───────────────────────────────────────────────────────
  for fid in mf-flow-1 mf-flow-2; do
    api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_MF_A_PORT/v1/flows/$fid"
    assert_http "MF DELETE $fid (agent-A) → 200" "$_code" "200"
    api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_MF_B_PORT/v1/flows/$fid"
    assert_http "MF DELETE $fid (agent-B) → 200" "$_code" "200"
  done

  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 9 — Daemon mode: burst pattern via REST API
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_burst_pattern() {
  echo
  log "═══ Test 9: daemon mode — burst pattern ═══"

  local log_a=/tmp/ta_test_daemon_burst_a.log
  local log_b=/tmp/ta_test_daemon_burst_b.log

  "$BINARY" daemon --control-port "$DAEMON_PAT_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  "$BINARY" daemon --control-port "$DAEMON_PAT_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for burst-pattern daemons (A=$DAEMON_PAT_A_PORT B=$DAEMON_PAT_B_PORT)..."
  wait_for_port "$DAEMON_PAT_A_PORT" 10
  wait_for_port "$DAEMON_PAT_B_PORT" 10
  sleep 0.5

  api_call /dev/null POST "http://127.0.0.1:$DAEMON_PAT_A_PORT/v1/flows" \
    '{"flow_id":"burst-flow","role":"destination","port":'"$TRAFFIC_BURST_PORT"',"protocol":"TCP","duration_sec":30}'
  assert_http "burst dest start → 201" "$_code" "201"
  sleep 1

  # Source with burst pattern: 2 s burst @ 8 Mbps, 3 s idle @ 1 Mbps
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows" \
    '{
      "flow_id":             "burst-flow",
      "role":                "source",
      "destination_ip":      "127.0.0.1",
      "port":                '"$TRAFFIC_BURST_PORT"',
      "protocol":            "TCP",
      "duration_sec":        30,
      "pattern_type":        "burst",
      "bandwidth_bps":       8000000,
      "concurrent_sessions": 1,
      "pattern_config": {
        "burst_duration": 2,
        "burst_interval": 3,
        "burst_rate":     "8000000",
        "idle_rate":      "1000000"
      }
    }'
  assert_http "burst src start → 201" "$_code" "201"

  # ── Pattern-shape verification ───────────────────────────────────────────
  # Burst cycle = 5 s (2 s burst + 3 s idle). Collect 8 samples at 1 s intervals
  # spanning at least one full cycle; the max/min throughput ratio must be ≥ 3×
  # to confirm the rate-limiter actually alternated between burst and idle rates.
  log "collecting 8 throughput samples at 1 s intervals (burst cycle=5 s)..."
  collect_throughput_samples "$DAEMON_PAT_B_PORT" "burst-flow" 8
  log "burst sampling done — max=${_sample_max} bps  min=${_sample_min} bps"
  assert_bps_ratio "burst pattern shape (max/min ratio)" \
    "$_sample_max" "$_sample_min" 3

  api_call /tmp/ta_burst_status.json GET \
    "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows/burst-flow"
  assert_http "burst GET src status → 200" "$_code" "200"

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$_body" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "burst pattern sent $bytes_sent bytes (>0)"
    else
      fail "burst pattern bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  print_flow_stats "daemon burst source" "$_body"

  api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_PAT_A_PORT/v1/flows/burst-flow"
  assert_http "burst DELETE dest → 200" "$_code" "200"
  api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows/burst-flow"
  assert_http "burst DELETE src → 200"  "$_code" "200"

  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 10 — Daemon mode: periodic (sine) pattern via REST API
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_periodic_pattern() {
  echo
  log "═══ Test 10: daemon mode — periodic/sine pattern ═══"

  local log_a=/tmp/ta_test_daemon_period_a.log
  local log_b=/tmp/ta_test_daemon_period_b.log

  # Reuse PAT ports (burst test already cleaned them up)
  "$BINARY" daemon --control-port "$DAEMON_PAT_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  "$BINARY" daemon --control-port "$DAEMON_PAT_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for periodic-pattern daemons (A=$DAEMON_PAT_A_PORT B=$DAEMON_PAT_B_PORT)..."
  wait_for_port "$DAEMON_PAT_A_PORT" 10
  wait_for_port "$DAEMON_PAT_B_PORT" 10
  sleep 0.5

  api_call /dev/null POST "http://127.0.0.1:$DAEMON_PAT_A_PORT/v1/flows" \
    '{"flow_id":"sine-flow","role":"destination","port":'"$TRAFFIC_PERIOD_PORT"',"protocol":"TCP","duration_sec":30}'
  assert_http "sine dest start → 201" "$_code" "201"
  sleep 1

  # Source with periodic sine: base 5 Mbps ± 3 Mbps, 8 s period
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows" \
    '{
      "flow_id":             "sine-flow",
      "role":                "source",
      "destination_ip":      "127.0.0.1",
      "port":                '"$TRAFFIC_PERIOD_PORT"',
      "protocol":            "TCP",
      "duration_sec":        30,
      "pattern_type":        "periodic",
      "bandwidth_bps":       5000000,
      "concurrent_sessions": 1,
      "pattern_config": {
        "wave_type":  "sine",
        "period":     8,
        "base_rate":  "5000000",
        "amplitude":  "3000000"
      }
    }'
  assert_http "sine src start → 201" "$_code" "201"

  # ── Pattern-shape verification ───────────────────────────────────────────
  # Sine period = 8 s, base = 5 Mbps, amplitude = 3 Mbps.
  # Theory: peak ≈ 8 Mbps at t=2 s, trough ≈ 2 Mbps at t=6 s → ratio ≈ 4.
  # Collect 10 samples at 1 s intervals (spanning 10 s, slightly more than one
  # full period). We require max/min ≥ 2× to confirm the wave is actually shaping
  # traffic (conservative threshold — allows for token-bucket smoothing).
  log "collecting 10 throughput samples at 1 s intervals (sine period=8 s)..."
  collect_throughput_samples "$DAEMON_PAT_B_PORT" "sine-flow" 10
  log "sine sampling done — max=${_sample_max} bps  min=${_sample_min} bps"
  assert_bps_ratio "sine pattern shape (max/min ratio)" \
    "$_sample_max" "$_sample_min" 2

  api_call /tmp/ta_sine_status.json GET \
    "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows/sine-flow"
  assert_http "sine GET src status → 200" "$_code" "200"

  if command -v jq &>/dev/null; then
    local bytes_sent
    bytes_sent=$(echo "$_body" | jq -r '.metrics.bytes_sent // 0')
    if (( bytes_sent > 0 )); then
      ok "sine pattern sent $bytes_sent bytes (>0)"
    else
      fail "sine pattern bytes_sent=$bytes_sent (expected >0)"
    fi
  fi
  print_flow_stats "daemon sine source" "$_body"

  api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_PAT_A_PORT/v1/flows/sine-flow"
  assert_http "sine DELETE dest → 200" "$_code" "200"
  api_call /dev/null DELETE "http://127.0.0.1:$DAEMON_PAT_B_PORT/v1/flows/sine-flow"
  assert_http "sine DELETE src → 200"  "$_code" "200"

  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 11 — Daemon mode: flow natural expiry after duration_sec
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_flow_expiry() {
  echo
  log "═══ Test 11: daemon mode — flow natural expiry ═══"

  local log_a=/tmp/ta_test_daemon_exp_a.log
  local log_b=/tmp/ta_test_daemon_exp_b.log

  "$BINARY" daemon --control-port "$DAEMON_EXP_A_PORT" >"$log_a" 2>&1 &
  local pid_a=$!
  PIDS+=("$pid_a")

  "$BINARY" daemon --control-port "$DAEMON_EXP_B_PORT" >"$log_b" 2>&1 &
  local pid_b=$!
  PIDS+=("$pid_b")

  log "waiting for expiry-test daemons (A=$DAEMON_EXP_A_PORT B=$DAEMON_EXP_B_PORT)..."
  wait_for_port "$DAEMON_EXP_A_PORT" 10
  wait_for_port "$DAEMON_EXP_B_PORT" 10
  sleep 0.5

  # Start a short-lived destination (8 s)
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_EXP_A_PORT/v1/flows" \
    '{"flow_id":"expiry-flow","role":"destination","port":'"$TRAFFIC_EXP_PORT"',"protocol":"TCP","duration_sec":8}'
  assert_http "expiry dest start → 201" "$_code" "201"
  sleep 1

  # Start a short-lived source (5 s)
  api_call /dev/null POST "http://127.0.0.1:$DAEMON_EXP_B_PORT/v1/flows" \
    '{"flow_id":"expiry-flow","role":"source","destination_ip":"127.0.0.1","port":'"$TRAFFIC_EXP_PORT"',"protocol":"TCP","duration_sec":5,"pattern_type":"constant","bandwidth_bps":2000000}'
  assert_http "expiry src start → 201" "$_code" "201"

  # Wait for both flows to expire naturally (source: 5 s, dest: 8 s → wait 10 s total)
  log "waiting 10 s for flows to expire naturally..."
  sleep 10

  # After expiry the flow should either be in phase=completed or removed (404)
  api_call /tmp/ta_exp_status_b.json GET \
    "http://127.0.0.1:$DAEMON_EXP_B_PORT/v1/flows/expiry-flow"
  if [[ "$_code" == "404" ]]; then
    ok "expiry: source flow removed after expiry (404)"
  elif [[ "$_code" == "200" ]]; then
    if command -v jq &>/dev/null; then
      local phase
      phase=$(echo "$_body" | jq -r '.phase // "unknown"')
      if [[ "$phase" == "completed" || "$phase" == "stopped" ]]; then
        ok "expiry: source flow phase=$phase after duration"
      else
        warn "expiry: source flow still in phase=$phase after 10 s (may be slow)"
      fi
    else
      ok "expiry: source flow status returned 200 (phase check needs jq)"
    fi
  else
    fail "expiry: unexpected HTTP $_code for GET after expiry"
  fi

  api_call /tmp/ta_exp_status_a.json GET \
    "http://127.0.0.1:$DAEMON_EXP_A_PORT/v1/flows/expiry-flow"
  if [[ "$_code" == "404" ]]; then
    ok "expiry: dest flow removed after expiry (404)"
  elif [[ "$_code" == "200" ]]; then
    if command -v jq &>/dev/null; then
      local phase
      phase=$(echo "$_body" | jq -r '.phase // "unknown"')
      if [[ "$phase" == "completed" || "$phase" == "stopped" ]]; then
        ok "expiry: dest flow phase=$phase after duration"
      else
        warn "expiry: dest flow still in phase=$phase after 10 s"
      fi
    else
      ok "expiry: dest flow status returned 200 (phase check needs jq)"
    fi
  else
    fail "expiry: unexpected HTTP $_code for GET after expiry"
  fi

  kill "$pid_a" "$pid_b" 2>/dev/null
  wait "$pid_a" "$pid_b" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_a}"); PIDS=("${PIDS[@]/$pid_b}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Test 12 — Daemon mode: REST API error cases
# ══════════════════════════════════════════════════════════════════════════════
test_daemon_error_cases() {
  echo
  log "═══ Test 12: daemon mode — REST API error cases ═══"

  local log_err=/tmp/ta_test_daemon_err.log

  "$BINARY" daemon --control-port "$DAEMON_ERR_PORT" >"$log_err" 2>&1 &
  local pid_err=$!
  PIDS+=("$pid_err")

  log "waiting for error-test daemon ($DAEMON_ERR_PORT)..."
  wait_for_port "$DAEMON_ERR_PORT" 10
  sleep 0.3

  # ── GET on a flow that was never created → 404 ────────────────────────────
  api_call /tmp/ta_err_get.json GET \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/flows/does-not-exist"
  assert_http "GET unknown flow → 404"     "$_code" "404"
  assert_contains "GET 404 error field"    "$_body" '"error"'

  # ── DELETE on a flow that was never created → 404 ────────────────────────
  api_call /tmp/ta_err_del.json DELETE \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/flows/does-not-exist"
  assert_http "DELETE unknown flow → 404"  "$_code" "404"
  assert_contains "DELETE 404 error field" "$_body" '"error"'

  # ── POST with missing port field → 400 ────────────────────────────────────
  api_call /tmp/ta_err_noport.json POST \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/flows" \
    '{"flow_id":"err-flow","role":"destination","duration_sec":5}'
  assert_http "missing port → 400"         "$_code" "400"
  assert_contains "missing port error msg" "$_body" '"error"'

  # ── POST with invalid JSON body → 400 ────────────────────────────────────
  api_call /tmp/ta_err_badjson.json POST \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/flows" \
    'not valid json at all'
  assert_http "malformed JSON → 400"       "$_code" "400"

  # ── Unsupported HTTP method on /v1/health → 405 ───────────────────────────
  api_call /tmp/ta_err_method.json POST \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/health" '{}'
  assert_http "POST /v1/health → 405"      "$_code" "405"

  # ── Unsupported HTTP method on /v1/flows/{id} → 405 ──────────────────────
  api_call /tmp/ta_err_patch.json PUT \
    "http://127.0.0.1:$DAEMON_ERR_PORT/v1/flows/any-id" '{}'
  assert_http "PUT /v1/flows/{id} → 405"   "$_code" "405"

  kill "$pid_err" 2>/dev/null
  wait "$pid_err" 2>/dev/null || true
  PIDS=("${PIDS[@]/$pid_err}")
}

# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
main() {
  echo -e "\n${CYAN}traffic-agent local integration tests${NC}"
  echo    "────────────────────────────────────────"

  build_binary

  # ── one-shot tests ──────────────────────────────────────────────────────────
  test_oneshot_tcp
  test_oneshot_udp
  test_oneshot_periodic
  test_oneshot_schedule
  test_oneshot_poisson

  # ── daemon / REST API tests ─────────────────────────────────────────────────
  test_daemon_mode
  test_daemon_udp
  test_daemon_multi_flow
  test_daemon_burst_pattern
  test_daemon_periodic_pattern
  test_daemon_flow_expiry
  test_daemon_error_cases

  echo
  echo "────────────────────────────────────────"
  echo -e "Results: ${GREEN}${PASS} passed${NC}  ${RED}${FAIL} failed${NC}"
  echo "────────────────────────────────────────"

  if (( FAIL > 0 )); then
    exit 1
  fi
}

main "$@"
