// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package patterns defines traffic-shaping patterns for the agent.
//
// Each pattern implements the Pattern interface, which exposes a single method:
//
//	BandwidthAt(t time.Time) int64
//
// The returned value is the target bandwidth in bits per second at time t.
// The PatternController in the ratelimit package calls this every 100 ms and
// adjusts the token-bucket fill rate accordingly — all without reconnecting.
//
// Patterns fall into two time-reference modes:
//   - elapsed: t is interpreted relative to startTime (behaviour of the old
//     traffic_generator.py — patterns repeat from t=0 on test start).
//   - wall_clock: t is the actual UTC time, so a daily sine wave naturally
//     peaks at the configured hour regardless of when the test started.
package patterns

import "time"

// Pattern is the single interface all traffic patterns must implement.
type Pattern interface {
	// BandwidthAt returns the target bandwidth in bits per second at time t.
	// t is always the current wall-clock time (time.Now()); patterns that use
	// "elapsed" mode compute phase from t.Sub(startTime) internally.
	BandwidthAt(t time.Time) int64

	// SetStartTime is called exactly once, when the flow begins.
	// Elapsed-mode patterns use this as the t=0 reference.
	// Wall-clock patterns ignore it.
	SetStartTime(t time.Time)
}
