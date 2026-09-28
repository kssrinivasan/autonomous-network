// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package flowmanager orchestrates active traffic flows.
// A Flow can run in "source" (sender) or "destination" (receiver) role.
// The manager maintains a registry of flows indexed by ID and exposes
// Start / Stop / Status operations used by the HTTP API.
package flowmanager

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/config"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/patterns"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/server"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/session"
)

// Phase represents the lifecycle state of a flow.
type Phase string

const (
	PhaseStarting  Phase = "starting"
	PhaseRunning   Phase = "running"
	PhaseCompleted Phase = "completed"
	PhaseFailed    Phase = "failed"
	PhaseStopped   Phase = "stopped"
)

// FlowStatus is a snapshot of a flow's current state and metrics.
type FlowStatus struct {
	FlowID      string          `json:"flow_id"`
	Role        string          `json:"role"`
	Phase       Phase           `json:"phase"`
	StartTime   time.Time       `json:"start_time"`
	DurationSec int             `json:"duration_sec"`
	ElapsedSec  float64         `json:"elapsed_sec"`
	Metrics     metrics.Snapshot `json:"metrics"`
	Error       string          `json:"error,omitempty"`
}

// flow is the internal representation of a running or completed traffic flow.
type flow struct {
	id          string
	role        string
	protocol    string
	durationSec int
	startTime   time.Time
	phase       Phase
	errMsg      string
	metrics     *metrics.Collector
	cancel      context.CancelFunc
}

// Manager is a thread-safe registry of traffic flows.
type Manager struct {
	mu     sync.RWMutex
	flows  map[string]*flow
	logger *slog.Logger
}

// New creates an empty Manager.
func New(logger *slog.Logger) *Manager {
	if logger == nil {
		logger = slog.Default()
	}
	return &Manager{
		flows:  make(map[string]*flow),
		logger: logger,
	}
}

// StartFlow launches a new flow in the background.  Returns an error if a
// flow with the same ID is already running.
func (m *Manager) StartFlow(req *config.FlowRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	if f, exists := m.flows[req.FlowID]; exists && (f.phase == PhaseRunning || f.phase == PhaseStarting) {
		return fmt.Errorf("flow %q is already %s", req.FlowID, f.phase)
	}

	m.logger.Info("registering flow",
		"flow_id", req.FlowID,
		"role", req.Role,
		"protocol", req.Protocol,
		"port", req.Port,
		"destination", req.DestinationIP,
		"pattern", req.PatternType,
		"duration_sec", req.DurationSec,
		"concurrent_sessions", req.ConcurrentSessions,
		"bandwidth_bps", req.BandwidthBps,
	)

	col := &metrics.Collector{}
	col.Reset()

	ctx, cancel := context.WithCancel(context.Background())
	f := &flow{
		id:          req.FlowID,
		role:        req.Role,
		protocol:    req.Protocol,
		durationSec: req.DurationSec,
		startTime:   time.Now(),
		phase:       PhaseStarting,
		metrics:     col,
		cancel:      cancel,
	}
	m.flows[req.FlowID] = f

	go m.runFlow(ctx, f, req)
	return nil
}

// StopFlow cancels a running flow by ID.
func (m *Manager) StopFlow(flowID string) error {
	m.mu.RLock()
	f, ok := m.flows[flowID]
	m.mu.RUnlock()

	if !ok {
		return fmt.Errorf("flow %q not found", flowID)
	}
	m.logger.Info("stopping flow", "flow_id", flowID, "current_phase", f.phase)
	f.cancel()

	m.mu.Lock()
	f.phase = PhaseStopped
	m.mu.Unlock()
	return nil
}

// FlowSnapshots returns the data needed by the Prometheus handler for all
// flows currently known to the manager.
func (m *Manager) FlowSnapshots() []metrics.FlowSnapshot {
	m.mu.RLock()
	defer m.mu.RUnlock()

	result := make([]metrics.FlowSnapshot, 0, len(m.flows))
	for _, f := range m.flows {
		result = append(result, metrics.FlowSnapshot{
			FlowID:   f.id,
			Role:     f.role,
			Protocol: f.protocol,
			Phase:    string(f.phase),
			Metrics:  f.metrics.Snapshot(),
		})
	}
	return result
}

// Status returns a snapshot for one flow (or all flows if flowID is empty).
func (m *Manager) Status(flowID string) []FlowStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()

	if flowID != "" {
		f, ok := m.flows[flowID]
		if !ok {
			return nil
		}
		return []FlowStatus{toStatus(f)}
	}

	result := make([]FlowStatus, 0, len(m.flows))
	for _, f := range m.flows {
		result = append(result, toStatus(f))
	}
	return result
}

// --- internal ---

func (m *Manager) runFlow(ctx context.Context, f *flow, req *config.FlowRequest) {
	defer func() {
		if r := recover(); r != nil {
			errMsg := fmt.Sprintf("panic: %v", r)
			m.logger.Error("flow panicked",
				"flow_id", f.id,
				"role", f.role,
				"panic", fmt.Sprintf("%v", r),
			)
			m.setPhase(f, PhaseFailed, errMsg)
		}
	}()

	protocol := strings.ToLower(req.Protocol)
	duration := time.Duration(req.DurationSec) * time.Second

	m.logger.Debug("flow execution starting",
		"flow_id", f.id,
		"role", req.Role,
		"protocol", protocol,
		"duration_sec", req.DurationSec,
	)
	m.setPhase(f, PhaseRunning, "")

	var err error
	switch req.Role {
	case "destination":
		err = m.runDestination(ctx, f, protocol, req.Port, duration)
	case "source":
		err = m.runSource(ctx, f, req, protocol, duration)
	default:
		err = fmt.Errorf("unknown role %q (must be 'source' or 'destination')", req.Role)
	}

	if err != nil && ctx.Err() == nil {
		m.setPhase(f, PhaseFailed, err.Error())
		m.logger.Error("flow failed", "flow_id", f.id, "err", err)
	} else {
		m.setPhase(f, PhaseCompleted, "")
		m.logger.Info("flow completed", "flow_id", f.id, "role", f.role)
	}
}

func (m *Manager) runDestination(ctx context.Context, f *flow, protocol string, port int, duration time.Duration) error {
	// Run the traffic server for the test duration.
	ctx, cancel := context.WithTimeout(ctx, duration)
	defer cancel()

	// Log received bytes every 10s so it is immediately visible if traffic
	// is not arriving (bytes_received stays 0 = unreachable or dropped).
	go m.logDestinationStats(ctx, f, protocol, port)

	switch protocol {
	case "tcp":
		srv := server.NewTCP(port, f.metrics, m.logger)
		return srv.Serve(ctx)
	case "udp":
		srv := server.NewUDP(port, f.metrics, m.logger)
		return srv.Serve(ctx)
	default:
		return fmt.Errorf("unsupported protocol %q", protocol)
	}
}

// logDestinationStats periodically logs bytes received by a destination flow.
// A sustained bytes_received of 0 indicates traffic is not arriving.
//
// Uses Counters() (read-only) for the periodic ticks so that lastSnapshot
// inside the Collector is not disturbed — same reasoning as logStats in the
// session package: calling Snapshot() here every 10 s would zero-out the
// throughput_bps gauge seen by a Prometheus scrape 1 ms later.
func (m *Manager) logDestinationStats(ctx context.Context, f *flow, protocol string, port int) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	start := time.Now()
	var lastBytesReceived int64

	for {
		select {
		case <-ctx.Done():
			// Final summary — safe to call Snapshot() once the flow is done.
			snap := f.metrics.Snapshot()
			m.logger.Info("destination flow final stats",
				"flow_id", f.id,
				"protocol", protocol,
				"port", port,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_received_total", snap.BytesReceived,
				"packets_received", snap.PacketsReceived,
			)
			return
		case <-ticker.C:
			// Use Counters() — does NOT update lastSnapshot.
			_, recv, _ := f.metrics.Counters()
			delta := recv - lastBytesReceived
			lastBytesReceived = recv
			m.logger.Info("destination traffic stats",
				"flow_id", f.id,
				"protocol", protocol,
				"port", port,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_received_total", recv,
				"bytes_received_interval", delta,
			)
			if recv == 0 {
				m.logger.Warn("no traffic received on destination",
					"flow_id", f.id,
					"protocol", protocol,
					"port", port,
					"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				)
			}
		}
	}
}

func (m *Manager) runSource(ctx context.Context, f *flow, req *config.FlowRequest, protocol string, duration time.Duration) error {
	// Build the pattern.
	pat, err := patterns.Build(
		req.PatternType,
		req.PatternConfig,
		req.BandwidthBps,
		req.ConcurrentSessions,
		nil, // activeSessions pointer set inside session.Manager for Poisson
	)
	if err != nil {
		return fmt.Errorf("building pattern: %w", err)
	}

	numSessions := req.ConcurrentSessions
	if numSessions < 1 {
		numSessions = 1
	}

	mgr := session.New(
		req.DestinationIP,
		req.Port,
		protocol,
		numSessions,
		pat,
		f.metrics,
		m.logger,
		req.SourceIP,
	)

	return mgr.Run(ctx, duration)
}

func (m *Manager) setPhase(f *flow, phase Phase, errMsg string) {
	m.mu.Lock()
	oldPhase := f.phase
	f.phase = phase
	f.errMsg = errMsg
	m.mu.Unlock()

	if oldPhase != phase {
		m.logger.Debug("flow phase transition",
			"flow_id", f.id,
			"from", string(oldPhase),
			"to", string(phase),
		)
	}
}

func toStatus(f *flow) FlowStatus {
	elapsed := time.Since(f.startTime).Seconds()
	return FlowStatus{
		FlowID:      f.id,
		Role:        f.role,
		Phase:       f.phase,
		StartTime:   f.startTime,
		DurationSec: f.durationSec,
		ElapsedSec:  elapsed,
		Metrics:     f.metrics.Snapshot(),
		Error:       f.errMsg,
	}
}
