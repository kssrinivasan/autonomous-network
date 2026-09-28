// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package session manages pools of concurrent traffic-sending goroutines.
// Each goroutine owns one TCP or UDP connection and paces its output via a
// shared rate limiter that the PatternController updates every 100 ms.
package session

import (
	"context"
	"encoding/binary"
	"fmt"
	"log/slog"
	"math/rand"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/patterns"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/ratelimit"
)

// udpPayloadBytes is the UDP payload size used when sending UDP datagrams.
// Set to 1300 bytes so the total datagram (16-byte header + 1284-byte payload)
// stays well within the effective MTU of most VPN-encapsulated links (~1400 B).
const udpPayloadBytes = 1300

// Manager runs a fixed pool of concurrent sessions against a single
// (destIP:port, protocol) target.  All sessions share one rate limiter whose
// fill rate is driven by the pattern's BandwidthAt function.
type Manager struct {
	destAddr string
	protocol string // "tcp" or "udp"
	sourceIP string // optional: bind source socket to this IP (e.g. a uesimtun address)
	sessions int
	pattern  patterns.Pattern
	metrics  *metrics.Collector
	logger   *slog.Logger

	// activeSessions is the live count read by the pattern controller.
	activeSessions atomic.Int32

	// seqCounter is a flow-wide monotonically increasing sequence number
	// embedded in every UDP datagram header for loss / jitter tracking.
	seqCounter atomic.Uint64
}

// New creates a Manager.  Call Run to start traffic.
// sourceIP is optional: when non-empty the source socket is bound to that
// address so packets egress via the interface that owns it (e.g. uesimtun).
func New(
	destIP string,
	port int,
	protocol string,
	numSessions int,
	pat patterns.Pattern,
	m *metrics.Collector,
	logger *slog.Logger,
	sourceIP string,
) *Manager {
	if logger == nil {
		logger = slog.Default()
	}
	return &Manager{
		destAddr: fmt.Sprintf("%s:%d", destIP, port),
		protocol: protocol,
		sourceIP: sourceIP,
		sessions: numSessions,
		pattern:  pat,
		metrics:  m,
		logger:   logger,
	}
}

// Run starts numSessions goroutines and blocks until ctx is cancelled or
// duration elapses.  Each goroutine independently connects and sends data.
func (m *Manager) Run(ctx context.Context, duration time.Duration) error {
	startTime := time.Now()
	m.pattern.SetStartTime(startTime)

	// Create a shared rate limiter — all sessions share the total budget.
	// The PatternController updates it every 100 ms based on BandwidthAt.
	// Sessions naturally divide the token bucket among themselves via
	// concurrent WaitBytes() calls — no manual per-session division needed.
	initialBps := m.pattern.BandwidthAt(startTime)
	lim := ratelimit.New(initialBps)

	// Start the pattern controller that updates lim on each tick.
	pc := ratelimit.NewPatternController(lim, 100*time.Millisecond, m.logger)
	ctxWithTimeout, cancel := context.WithTimeout(ctx, duration)
	defer cancel()

	go pc.Run(ctxWithTimeout, func(t time.Time) int64 {
		// Return the total target bandwidth; all concurrent sessions share
		// this single token bucket and naturally divide the rate among
		// themselves.  Dividing by activeSessions here would cause the
		// aggregate throughput to be total/N instead of total.
		return m.pattern.BandwidthAt(t)
	})

	// Periodic stats logger: log throughput every 10 s so progress is visible
	// in container logs without needing LOG_LEVEL=debug.
	go m.logStats(ctxWithTimeout, startTime)

	// Check if this is a Poisson pattern — those self-manage arrivals.
	if pp, ok := m.pattern.(*patterns.PoissonPattern); ok {
		return m.runPoisson(ctxWithTimeout, pp, lim)
	}

	// Standard pool: launch all sessions immediately.
	return m.runPool(ctxWithTimeout, lim)
}

// logStats emits periodic throughput/session metrics to the structured log.
// It runs for the lifetime of ctxWithTimeout and also emits a final summary.
//
// Uses Counters() (read-only) for the periodic ticks so that lastSnapshot
// inside the Collector is not disturbed.  If Snapshot() were called here
// every 10 s, a Prometheus scrape happening 1 ms later would compute
// throughput over a 1 ms window → ~0 bps even under heavy load.
// Snapshot() is only called once at the very end for the final summary.
func (m *Manager) logStats(ctx context.Context, start time.Time) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	var lastBytesSent int64

	for {
		select {
		case <-ctx.Done():
			// Final summary — Snapshot() here is safe; no more Prometheus scrapes
			// will race with it after the flow context is cancelled.
			snap := m.metrics.Snapshot()
			m.logger.Info("traffic session final stats",
				"dest", m.destAddr,
				"protocol", m.protocol,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_sent_total", snap.BytesSent,
				"throughput_mbps", fmt.Sprintf("%.2f", snap.ThroughputBps/1e6),
				"active_sessions", snap.ActiveSessions,
			)
			return
		case <-ticker.C:
			// Use Counters() — does NOT update lastSnapshot, so throughput_bps
			// in subsequent Prometheus scrapes is computed over the correct window.
			sent, _, sessions := m.metrics.Counters()
			delta := sent - lastBytesSent
			lastBytesSent = sent
			m.logger.Info("traffic stats",
				"dest", m.destAddr,
				"protocol", m.protocol,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_sent_total", sent,
				"bytes_sent_interval", delta,
				"active_sessions", sessions,
			)
		}
	}
}

// runPool starts m.sessions goroutines concurrently and waits for all to finish.
func (m *Manager) runPool(ctx context.Context, lim *ratelimit.Limiter) error {
	m.logger.Info("starting session pool",
		"sessions", m.sessions,
		"dest", m.destAddr,
		"protocol", m.protocol,
	)
	var wg sync.WaitGroup
	for i := 0; i < m.sessions; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			m.activeSessions.Add(1)
			defer m.activeSessions.Add(-1)
			m.metrics.IncrSessions()
			defer m.metrics.DecrSessions()

			if err := m.runSession(ctx, id, lim); err != nil {
				m.logger.Warn("session ended", "id", id, "err", err)
			}
		}(i)
	}
	wg.Wait()
	return nil
}

// runPoisson starts sessions according to a Poisson arrival process.
// New sessions are spawned as goroutines; the function returns when ctx expires.
func (m *Manager) runPoisson(ctx context.Context, pp *patterns.PoissonPattern, lim *ratelimit.Limiter) error {
	m.logger.Info("starting poisson session manager",
		"max_sessions", pp.MaxSessions,
		"dest", m.destAddr,
		"protocol", m.protocol,
	)
	var wg sync.WaitGroup
	sessionID := 0

	for {
		// Sample next inter-arrival time.
		wait := time.Duration(pp.NextInterArrivalSec() * float64(time.Second))

		select {
		case <-ctx.Done():
			wg.Wait()
			return nil
		case <-time.After(wait):
			// Enforce max concurrency.
			if int(m.activeSessions.Load()) >= pp.MaxSessions {
				continue
			}
			id := sessionID
			sessionID++
			wg.Add(1)
			go func(id int) {
				defer wg.Done()
				m.activeSessions.Add(1)
				defer m.activeSessions.Add(-1)
				m.metrics.IncrSessions()
				defer m.metrics.DecrSessions()

				// Each Poisson session runs for SessionDuration, not the full test.
				sessionCtx, cancel := context.WithTimeout(ctx, pp.SessionDuration())
				defer cancel()

				if err := m.runSession(sessionCtx, id, lim); err != nil {
					m.logger.Debug("poisson session ended", "id", id, "err", err)
				}
			}(id)
		}
	}
}

// runSession is the inner send loop for a single connection.
// It connects to destAddr and writes chunks of random data at the rate
// controlled by lim.  It reconnects automatically if the connection drops.
//
// For UDP flows the buffer includes a 16-byte header that is updated on every
// datagram: [seq uint64 BE][send_timestamp_ns uint64 BE][random payload...].
// The header enables the receiver to compute one-way latency, inter-packet
// jitter, and sequence-number-based packet loss without cross-device state.
func (m *Manager) runSession(ctx context.Context, id int, lim *ratelimit.Limiter) error {
	var buf []byte
	if m.protocol == "udp" {
		// Allocate once per session: 16-byte header + fixed-size payload.
		// The payload is filled with random bytes to prevent compression on
		// intermediate devices.  Only the header changes per datagram.
		buf = make([]byte, 16+udpPayloadBytes)
		rand.Read(buf[16:]) //nolint:gosec
	} else {
		buf = makePayload(lim.ChunkSize())
	}

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		conn, err := m.dial(ctx)
		if err != nil {
			// Could not connect — back off and retry.
			m.logger.Warn("dial failed, retrying", "session", id, "addr", m.destAddr, "err", err)
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(2 * time.Second):
			}
			continue
		}

		m.logger.Debug("session connected", "id", id, "addr", m.destAddr)
		var loopErr error
		if m.protocol == "udp" {
			loopErr = m.udpSendLoop(ctx, conn, buf, lim)
		} else {
			loopErr = m.sendLoop(ctx, conn, buf, lim)
		}
		if loopErr != nil {
			m.logger.Debug("session send loop ended", "id", id, "err", loopErr)
		}
		conn.Close()

		// If ctx is done, exit cleanly.
		select {
		case <-ctx.Done():
			return nil
		default:
			// Connection dropped unexpectedly — reconnect.
		}
	}
}

// sendLoop writes TCP data to conn at the rate allowed by lim until ctx is
// done or an error occurs.
func (m *Manager) sendLoop(ctx context.Context, conn net.Conn, buf []byte, lim *ratelimit.Limiter) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		// Wait for rate-limiter token bucket.
		if err := lim.WaitBytes(ctx, len(buf)); err != nil {
			return err
		}

		n, err := conn.Write(buf)
		if n > 0 {
			m.metrics.AddBytesSent(int64(n))
		}
		if err != nil {
			return fmt.Errorf("write: %w", err)
		}
	}
}

// udpSendLoop writes UDP datagrams with a 16-byte measurement header at the
// rate allowed by lim until ctx is done or an error occurs.
//
// Header layout (all big-endian):
//
//	bytes 0–7:  uint64  per-flow sequence number (starts at 1, never wraps in practice)
//	bytes 8–15: uint64  send timestamp in nanoseconds since Unix epoch
func (m *Manager) udpSendLoop(ctx context.Context, conn net.Conn, buf []byte, lim *ratelimit.Limiter) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		if err := lim.WaitBytes(ctx, len(buf)); err != nil {
			return err
		}

		// Stamp the header with current seq and send time (buf is per-goroutine).
		seq := m.seqCounter.Add(1)
		binary.BigEndian.PutUint64(buf[0:8], seq)
		binary.BigEndian.PutUint64(buf[8:16], uint64(time.Now().UnixNano()))

		n, err := conn.Write(buf)
		if n > 0 {
			m.metrics.AddBytesSent(int64(n))
			m.metrics.AddPacketSent()
		}
		if err != nil {
			return fmt.Errorf("write: %w", err)
		}
	}
}

func (m *Manager) dial(ctx context.Context) (net.Conn, error) {
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	if m.sourceIP != "" {
		ip := net.ParseIP(m.sourceIP)
		if m.protocol == "udp" {
			dialer.LocalAddr = &net.UDPAddr{IP: ip}
		} else {
			dialer.LocalAddr = &net.TCPAddr{IP: ip}
		}
	}
	return dialer.DialContext(ctx, m.protocol, m.destAddr)
}

// makePayload creates a random-looking byte slice of size n.
// Random content prevents compression on intermediate devices and provides a
// more realistic traffic signature than all-zero payloads.
func makePayload(n int) []byte {
	buf := make([]byte, n)
	rand.Read(buf) //nolint:gosec
	return buf
}
