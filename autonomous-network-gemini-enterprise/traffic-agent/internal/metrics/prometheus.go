// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package metrics provides thread-safe metrics collection for traffic flows
// and a Prometheus text-format exposition handler for the daemon's /metrics
// endpoint.
package metrics

import (
	"fmt"
	"net/http"
	"strings"
)

// FlowSnapshot is the subset of flow state needed to render Prometheus metrics.
// It is supplied by the flow manager via the StatusFunc callback so this
// package stays free of an import cycle.
type FlowSnapshot struct {
	FlowID   string
	Role     string
	Protocol string
	Phase    string
	Metrics  Snapshot
}

// StatusFunc is called on each scrape to obtain a current snapshot of all
// active and recently completed flows.
type StatusFunc func() []FlowSnapshot

// PrometheusHandler returns an http.Handler that renders all flow metrics in
// the Prometheus text exposition format (version 0.0.4).
//
// The handler is intentionally dependency-free — it writes the text format
// directly without importing github.com/prometheus/client_golang so that the
// traffic-agent module stays minimal.
func PrometheusHandler(statusFn StatusFunc) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		flows := statusFn()

		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		w.WriteHeader(http.StatusOK)

		b := &strings.Builder{}

		writeHelp(b, "traffic_agent_bytes_sent_total", "counter", "Total bytes sent by this flow.")
		writeHelp(b, "traffic_agent_bytes_received_total", "counter", "Total bytes received by this flow.")
		writeHelp(b, "traffic_agent_throughput_bps", "gauge", "Bidirectional throughput in bits per second (sent + received). Kept for backward compatibility; prefer throughput_sent_bps / throughput_recv_bps.")
		writeHelp(b, "traffic_agent_throughput_sent_bps", "gauge", "Outbound throughput in bits per second (bytes sent only). Use this for source-role flows to avoid double-counting bidirectional traffic.")
		writeHelp(b, "traffic_agent_throughput_recv_bps", "gauge", "Inbound throughput in bits per second (bytes received only). Use this for destination-role flows.")
		writeHelp(b, "traffic_agent_latency_ms", "gauge", "Mean latency in milliseconds (UDP flows only).")
		writeHelp(b, "traffic_agent_packet_loss_pct", "gauge", "Packet loss percentage (UDP flows only).")
		writeHelp(b, "traffic_agent_jitter_ms", "gauge", "Mean jitter in milliseconds (UDP flows only).")
		writeHelp(b, "traffic_agent_active_sessions", "gauge", "Number of currently active sessions in this flow.")
		writeHelp(b, "traffic_agent_flow_running", "gauge", "1 if the flow is in 'running' phase, 0 otherwise.")

		for _, f := range flows {
			labels := fmt.Sprintf(`flow_id=%q,role=%q,protocol=%q`,
				f.FlowID, f.Role, f.Protocol)

			running := 0.0
			if f.Phase == "running" {
				running = 1.0
			}

			writeSample(b, "traffic_agent_bytes_sent_total", labels, float64(f.Metrics.BytesSent))
			writeSample(b, "traffic_agent_bytes_received_total", labels, float64(f.Metrics.BytesReceived))
			writeSample(b, "traffic_agent_throughput_bps", labels, f.Metrics.ThroughputBps)
			writeSample(b, "traffic_agent_throughput_sent_bps", labels, f.Metrics.ThroughputSentBps)
			writeSample(b, "traffic_agent_throughput_recv_bps", labels, f.Metrics.ThroughputRecvBps)
			writeSample(b, "traffic_agent_latency_ms", labels, f.Metrics.LatencyMs)
			writeSample(b, "traffic_agent_packet_loss_pct", labels, f.Metrics.PacketLossPct)
			writeSample(b, "traffic_agent_jitter_ms", labels, f.Metrics.JitterMs)
			writeSample(b, "traffic_agent_active_sessions", labels, float64(f.Metrics.ActiveSessions))
			writeSample(b, "traffic_agent_flow_running", labels, running)
		}

		fmt.Fprint(w, b.String())
	})
}

func writeHelp(b *strings.Builder, name, metricType, help string) {
	fmt.Fprintf(b, "# HELP %s %s\n", name, help)
	fmt.Fprintf(b, "# TYPE %s %s\n", name, metricType)
}

func writeSample(b *strings.Builder, name, labels string, value float64) {
	fmt.Fprintf(b, "%s{%s} %g\n", name, labels, value)
}
