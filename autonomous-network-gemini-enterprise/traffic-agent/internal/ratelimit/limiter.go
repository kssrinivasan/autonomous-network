// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package ratelimit provides an adaptive token-bucket rate limiter for
// per-session bandwidth control.  The fill rate can be updated in real time
// (without reconnecting) to implement dynamic traffic patterns.
package ratelimit

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

const (
	// defaultChunkBytes is the number of bytes requested per Write call.
	// 8 KB chunks give good throughput while keeping the control loop latency low.
	defaultChunkBytes = 8192

	// minBurstMs is the minimum burst window expressed as a fraction of a second.
	// The token bucket burst size is set to (rate * minBurstMs / 1000) bytes,
	// which allows short sub-second bursts while still tracking the target rate.
	minBurstMs = 100
)

// Limiter wraps golang.org/x/time/rate.Limiter with helpers for byte-level
// bandwidth control and atomic rate updates.
type Limiter struct {
	mu      sync.RWMutex
	limiter *rate.Limiter
}

// New creates a Limiter targeting bandwidthBps bits per second.
// Pass bandwidthBps = 0 to create an unlimited limiter.
func New(bandwidthBps int64) *Limiter {
	l := &Limiter{}
	l.setRate(bandwidthBps)
	return l
}

// SetRate updates the token bucket fill rate to bandwidthBps bits per second.
// This is safe to call concurrently and takes effect immediately, allowing
// dynamic pattern changes without any connection teardown.
func (l *Limiter) SetRate(bandwidthBps int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.setRate(bandwidthBps)
}

// WaitBytes blocks until the limiter allows n bytes to be sent.
// Returns ctx.Err() if the context is cancelled before the tokens arrive.
func (l *Limiter) WaitBytes(ctx context.Context, n int) error {
	l.mu.RLock()
	lim := l.limiter
	l.mu.RUnlock()

	if lim == nil {
		// Unlimited — no wait needed.
		return ctx.Err()
	}
	return lim.WaitN(ctx, n)
}

// ChunkSize returns the recommended write chunk size in bytes.
func (l *Limiter) ChunkSize() int {
	return defaultChunkBytes
}

// --- internal ---

func (l *Limiter) setRate(bandwidthBps int64) {
	if bandwidthBps <= 0 {
		l.limiter = nil // unlimited
		return
	}
	// Convert bps → bytes/sec for token bucket (1 token = 1 byte).
	bytesPerSec := rate.Limit(float64(bandwidthBps) / 8.0)

	// Burst size = rate × burst window.
	// Minimum 1 chunk to avoid deadlocks on very low rates.
	burstBytes := int(float64(bandwidthBps) / 8.0 * float64(minBurstMs) / 1000.0)
	if burstBytes < defaultChunkBytes {
		burstBytes = defaultChunkBytes
	}

	if l.limiter == nil {
		l.limiter = rate.NewLimiter(bytesPerSec, burstBytes)
	} else {
		l.limiter.SetLimit(bytesPerSec)
		l.limiter.SetBurst(burstBytes)
	}
}

// PatternController drives a Limiter from a Pattern's BandwidthAt function.
// It runs a goroutine that calls BandwidthAt every tickInterval and updates
// the Limiter.  Stop it by cancelling the provided context.
type PatternController struct {
	limiter      *Limiter
	tickInterval time.Duration
	logger       *slog.Logger

	// rate-change debug state (not atomic — only accessed inside Run goroutine)
	lastLoggedBps int64
	lastLogTime   time.Time
}

// NewPatternController creates a controller that will update lim at tickInterval.
// logger is optional; pass nil to use the default slog logger.
func NewPatternController(lim *Limiter, tickInterval time.Duration, logger *slog.Logger) *PatternController {
	if tickInterval <= 0 {
		tickInterval = 100 * time.Millisecond
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &PatternController{limiter: lim, tickInterval: tickInterval, logger: logger}
}

// BandwidthFunc is the function signature used by PatternController.
// t is the current wall-clock time; the function returns bits per second.
type BandwidthFunc func(t time.Time) int64

// Run starts the control loop.  It returns when ctx is cancelled.
// At debug log level, rate changes ≥ 10% are logged at most once every 30 s
// so you can watch the pattern evolve without flooding the log.
func (pc *PatternController) Run(ctx context.Context, bwFn BandwidthFunc) {
	ticker := time.NewTicker(pc.tickInterval)
	defer ticker.Stop()

	const logMinInterval = 30 * time.Second
	const logChangePct = 10.0

	for {
		select {
		case <-ctx.Done():
			return
		case t := <-ticker.C:
			bps := bwFn(t)
			pc.limiter.SetRate(bps)

			// Emit a debug log when the rate has shifted significantly and
			// enough time has passed since the last log.
			if pc.logger.Enabled(ctx, slog.LevelDebug) {
				now := time.Now()
				if now.Sub(pc.lastLogTime) >= logMinInterval && rateChangePct(pc.lastLoggedBps, bps) >= logChangePct {
					pc.logger.Debug("pattern rate update",
						"bps", bps,
						"mbps", fmt.Sprintf("%.2f", float64(bps)/1e6),
					)
					pc.lastLoggedBps = bps
					pc.lastLogTime = now
				}
			}
		}
	}
}

// rateChangePct returns the absolute percentage change from old to new.
func rateChangePct(old, new int64) float64 {
	if old == 0 {
		return 100
	}
	diff := new - old
	if diff < 0 {
		diff = -diff
	}
	return float64(diff) / float64(old) * 100.0
}
