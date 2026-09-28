// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package metrics provides thread-safe metrics collection for traffic flows.
// Metrics are tracked with atomic operations and snapshot deltas to compute
// instantaneous throughput without locking the data path.
package metrics

import (
	"sync"
	"sync/atomic"
	"time"
)

// Snapshot is a point-in-time metrics sample.
type Snapshot struct {
	Timestamp      time.Time `json:"timestamp"`
	BytesSent      int64     `json:"bytes_sent"`
	BytesReceived  int64     `json:"bytes_received"`

	// ThroughputBps is the total bidirectional throughput (sent + received bits
	// per second) since the previous Snapshot call.  For unidirectional
	// accounting use ThroughputSentBps (source role) or ThroughputRecvBps
	// (destination role) instead.
	ThroughputBps     float64 `json:"throughput_bps"`
	ThroughputSentBps float64 `json:"throughput_sent_bps"` // outbound bits/s only
	ThroughputRecvBps float64 `json:"throughput_recv_bps"` // inbound bits/s only

	LatencyMs      float64   `json:"latency_ms"`
	PacketLossPct  float64   `json:"packet_loss_pct"`
	PacketsSent    int64     `json:"packets_sent"`
	PacketsReceived int64    `json:"packets_received"`
	PacketsDropped int64     `json:"packets_dropped"`
	JitterMs       float64   `json:"jitter_ms"`
	ActiveSessions int32     `json:"active_sessions"`
}

// Collector accumulates per-flow metrics using lock-free atomic counters.
// ThroughputBps is calculated on demand as a delta from the previous Snapshot.
type Collector struct {
	bytesSent     atomic.Int64
	bytesReceived atomic.Int64
	activeSessions atomic.Int32

	// For latency / jitter tracking (UDP only currently)
	latencySumMs  atomic.Int64 // sum in microseconds (divide by samples for mean)
	latencySamples atomic.Int64
	jitterSumMs   atomic.Int64
	jitterSamples atomic.Int64

	// For packet loss tracking (UDP sequence numbers)
	packetsSent    atomic.Int64
	packetsReceived atomic.Int64

	mu            sync.Mutex
	lastSnapshot  Snapshot
}

// AddBytesSent records n bytes sent from this agent.
func (c *Collector) AddBytesSent(n int64) {
	c.bytesSent.Add(n)
}

// AddBytesReceived records n bytes received by this agent.
func (c *Collector) AddBytesReceived(n int64) {
	c.bytesReceived.Add(n)
}

// AddLatencySample records a latency observation in milliseconds.
func (c *Collector) AddLatencySample(ms float64) {
	// Store as microseconds to avoid float atomics
	c.latencySumMs.Add(int64(ms * 1000))
	c.latencySamples.Add(1)
}

// AddJitterSample records a jitter (inter-arrival variance) observation in ms.
func (c *Collector) AddJitterSample(ms float64) {
	c.jitterSumMs.Add(int64(ms * 1000))
	c.jitterSamples.Add(1)
}

// AddPacketSent records one packet sent (for loss calculation).
func (c *Collector) AddPacketSent() {
	c.packetsSent.Add(1)
}

// AddPacketReceived records one packet received (for loss calculation).
func (c *Collector) AddPacketReceived() {
	c.packetsReceived.Add(1)
}

// Counters returns the current raw cumulative counters as a non-destructive
// read — it does NOT update lastSnapshot and therefore does NOT affect
// subsequent throughput calculations in Snapshot().
//
// Use this in periodic log helpers (logStats, logDestinationStats) so that
// only the Prometheus scraper updates lastSnapshot.  If logStats called
// Snapshot() every 10 s and Prometheus scraped 1 ms later, the throughput
// delta window would be 1 ms → effectively 0 bps even under heavy load.
func (c *Collector) Counters() (bytesSent, bytesReceived int64, activeSessions int32) {
	return c.bytesSent.Load(), c.bytesReceived.Load(), c.activeSessions.Load()
}

// AddPacketsSent records n packets as "expected" in bulk.
// Used by the UDP receiver to account for both received packets and
// detected sequence-number gaps so the Collector's loss formula gives the
// correct result without needing cross-device coordination.
func (c *Collector) AddPacketsSent(n int64) {
	c.packetsSent.Add(n)
}

// IncrSessions increments the active session count.
func (c *Collector) IncrSessions() {
	c.activeSessions.Add(1)
}

// DecrSessions decrements the active session count.
func (c *Collector) DecrSessions() {
	c.activeSessions.Add(-1)
}

// Snapshot returns a current metrics snapshot, computing throughput as a
// delta since the previous call to Snapshot.
func (c *Collector) Snapshot() Snapshot {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	sent := c.bytesSent.Load()
	recv := c.bytesReceived.Load()

	// Compute instantaneous throughput — three gauges:
	//   ThroughputSentBps: outbound only (use for source-role flows)
	//   ThroughputRecvBps: inbound only  (use for destination-role flows)
	//   ThroughputBps:     bidirectional sum (kept for backward compatibility)
	elapsed := now.Sub(c.lastSnapshot.Timestamp).Seconds()
	var throughput, throughputSent, throughputRecv float64
	if elapsed > 0 {
		deltaSent := sent - c.lastSnapshot.BytesSent
		deltaRecv := recv - c.lastSnapshot.BytesReceived
		throughputSent = float64(deltaSent) * 8 / elapsed
		throughputRecv = float64(deltaRecv) * 8 / elapsed
		throughput = throughputSent + throughputRecv
	}

	// Compute mean latency
	var latencyMs float64
	if samples := c.latencySamples.Load(); samples > 0 {
		latencyMs = float64(c.latencySumMs.Load()) / float64(samples) / 1000.0
	}

	// Compute mean jitter
	var jitterMs float64
	if samples := c.jitterSamples.Load(); samples > 0 {
		jitterMs = float64(c.jitterSumMs.Load()) / float64(samples) / 1000.0
	}

	// Compute packet loss
	var lossPct float64
	pkts := c.packetsSent.Load()
	pktsRecv := c.packetsReceived.Load()
	var dropped int64
	if pkts > 0 {
		dropped = pkts - pktsRecv
		if dropped < 0 {
			dropped = 0
		}
		lossPct = float64(dropped) / float64(pkts) * 100.0
	}

	s := Snapshot{
		Timestamp:         now,
		BytesSent:         sent,
		BytesReceived:     recv,
		ThroughputBps:     throughput,
		ThroughputSentBps: throughputSent,
		ThroughputRecvBps: throughputRecv,
		LatencyMs:         latencyMs,
		PacketLossPct:     lossPct,
		PacketsSent:       pkts,
		PacketsReceived:   pktsRecv,
		PacketsDropped:    dropped,
		JitterMs:          jitterMs,
		ActiveSessions:    c.activeSessions.Load(),
	}

	// Reset per-window accumulators so the next Snapshot() (next Prometheus
	// scrape) sees only samples collected in the subsequent interval.
	// This gives latency, jitter, and packet-loss the same rolling-window
	// semantics as throughput_bps.
	//
	// bytesSent / bytesReceived are intentionally NOT reset — they are
	// cumulative counters used for bytes_*_total and for the lastSnapshot
	// throughput delta.
	//
	// Note: Add* methods use bare atomics without this mutex, so there is
	// a benign race between the Load above and the Store(0) below: any
	// sample arriving in that gap is simply counted in the next window
	// rather than the current one.
	c.latencySumMs.Store(0)
	c.latencySamples.Store(0)
	c.jitterSumMs.Store(0)
	c.jitterSamples.Store(0)
	c.packetsSent.Store(0)
	c.packetsReceived.Store(0)

	c.lastSnapshot = s
	return s
}

// Reset clears all counters (call at flow start).
func (c *Collector) Reset() {
	c.bytesSent.Store(0)
	c.bytesReceived.Store(0)
	c.activeSessions.Store(0)
	c.latencySumMs.Store(0)
	c.latencySamples.Store(0)
	c.jitterSumMs.Store(0)
	c.jitterSamples.Store(0)
	c.packetsSent.Store(0)
	c.packetsReceived.Store(0)

	c.mu.Lock()
	c.lastSnapshot = Snapshot{Timestamp: time.Now()}
	c.mu.Unlock()
}
