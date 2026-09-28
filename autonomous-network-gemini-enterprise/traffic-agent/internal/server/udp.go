// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package server

import (
	"context"
	"encoding/binary"
	"fmt"
	"log/slog"
	"math"
	"net"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
)

// udpHeaderSize is the fixed header prepended by the sender to every UDP
// datagram.  The receiver uses it to compute one-way latency, jitter and
// sequence-number-based packet loss.
//
// Layout (all big-endian):
//
//	bytes 0–7:  uint64  per-flow sequence number (starts at 1)
//	bytes 8–15: uint64  send timestamp in nanoseconds since Unix epoch
const udpHeaderSize = 16

// maxSaneLatencyMs is the upper bound for a latency sample to be considered
// valid.  Values above this are most likely caused by clock skew between the
// sender and receiver VMs.  NTP-synchronised GCP VMs are typically within a
// few milliseconds of each other; 10 s is a conservative safety margin.
const maxSaneLatencyMs = 10_000.0

// UDPServer listens for incoming UDP datagrams, recording per-flow metrics.
//
// Every datagram is expected to carry a 16-byte header written by the sender
// (session/manager.go udpSendLoop).  If the datagram is shorter than the
// header the server falls back to counting raw bytes without measurements.
type UDPServer struct {
	port    int
	metrics *metrics.Collector
	logger  *slog.Logger
}

// NewUDP creates a UDPServer on the given port.
func NewUDP(port int, m *metrics.Collector, logger *slog.Logger) *UDPServer {
	if logger == nil {
		logger = slog.Default()
	}
	return &UDPServer{port: port, metrics: m, logger: logger}
}

// Serve receives UDP datagrams until ctx is cancelled.
//
// For datagrams with the 16-byte measurement header the server:
//  1. Computes one-way latency from the sender's embedded timestamp.
//  2. Computes inter-packet jitter as |OWD(i) − OWD(i−1)| (mean-absolute
//     deviation between consecutive one-way delays).
//  3. Tracks sequence numbers to detect gaps and estimate packet loss.
//
// Loss is computed entirely on the receiver side: each expected packet
// (received + gap) increments AddPacketsSent, each actually-received packet
// increments AddPacketReceived.  The Collector's loss formula then gives
// (expected − received) / expected × 100 % without any cross-device state.
func (s *UDPServer) Serve(ctx context.Context) error {
	addr := fmt.Sprintf(":%d", s.port)
	conn, err := net.ListenPacket("udp", addr)
	if err != nil {
		return fmt.Errorf("udp listen on %s: %w", addr, err)
	}
	s.logger.Info("UDP server listening", "addr", addr)

	go func() {
		<-ctx.Done()
		conn.Close()
	}()

	buf := make([]byte, 65536)

	// Per-flow state (all local to this goroutine — no locking needed).
	var (
		totalPackets int64
		totalBytes   int64

		// Sequence tracking for loss detection.
		seqStarted bool
		lastSeq    uint64

		// Previous one-way delay (ms) for jitter calculation.
		prevOWDms float64
		hasPrevOWD bool
	)

	for {
		n, src, err := conn.ReadFrom(buf)
		if err != nil {
			select {
			case <-ctx.Done():
				s.logger.Debug("UDP server stopped",
					"total_packets", totalPackets,
					"total_bytes", totalBytes,
				)
				return nil
			default:
				s.logger.Warn("udp read error", "err", err)
				continue
			}
		}
		if n <= 0 {
			continue
		}

		recvNs := time.Now().UnixNano()
		totalPackets++
		totalBytes += int64(n)

		s.metrics.AddBytesReceived(int64(n))

		if n < udpHeaderSize {
			// Legacy/short datagram — count it but skip measurements.
			s.metrics.AddPacketReceived()
			if totalPackets == 1 {
				s.logger.Debug("UDP first (short) packet received",
					"from", src.String(), "bytes", n)
			}
			continue
		}

		// ── Parse header ────────────────────────────────────────────────
		seq    := binary.BigEndian.Uint64(buf[0:8])
		sendNs := int64(binary.BigEndian.Uint64(buf[8:16]))

		// ── One-way latency ──────────────────────────────────────────────
		owdMs := float64(recvNs-sendNs) / 1e6
		if owdMs >= 0 && owdMs < maxSaneLatencyMs {
			s.metrics.AddLatencySample(owdMs)

			// Jitter: mean absolute deviation of consecutive OWD samples.
			if hasPrevOWD {
				jitterMs := math.Abs(owdMs - prevOWDms)
				s.metrics.AddJitterSample(jitterMs)
			}
			prevOWDms  = owdMs
			hasPrevOWD = true
		}

		// ── Sequence-number loss detection ───────────────────────────────
		// Strategy: account for every expected sequence slot (received + gap)
		// as AddPacketsSent so the Collector's loss formula works locally.
		if seqStarted {
			if seq > lastSeq {
				gap := int64(seq - lastSeq - 1) // datagrams lost in the gap
				if gap > 0 {
					s.metrics.AddPacketsSent(gap) // lost (expected but not received)
					s.logger.Debug("UDP sequence gap detected",
						"expected", lastSeq+1, "got", seq, "gap", gap)
				}
			}
			// Out-of-order or duplicate: still count as received below;
			// don't update lastSeq backwards to avoid inflating the gap.
		}
		s.metrics.AddPacketsSent(1) // this slot was expected
		s.metrics.AddPacketReceived()
		if !seqStarted || seq > lastSeq {
			lastSeq    = seq
			seqStarted = true
		}

		// Periodic progress logging.
		if totalPackets == 1 {
			s.logger.Debug("UDP first packet received",
				"from", src.String(), "bytes", n, "seq", seq, "owd_ms", owdMs)
		} else if totalPackets%10000 == 0 {
			snap := s.metrics.Snapshot()
			s.logger.Debug("UDP progress",
				"total_packets", totalPackets,
				"total_bytes", totalBytes,
				"last_src", src.String(),
				"owd_ms", fmt.Sprintf("%.3f", snap.LatencyMs),
				"jitter_ms", fmt.Sprintf("%.3f", snap.JitterMs),
				"loss_pct", fmt.Sprintf("%.2f%%", snap.PacketLossPct),
			)
		}
	}
}
