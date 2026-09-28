// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package patterns

import (
	"math"
	"math/rand"
	"time"
)

// PoissonPattern models stochastic user arrivals using a Poisson process.
// It returns a bandwidth level derived from how many sessions are currently
// active; the session manager uses this as the per-session rate cap.
//
// Sessions arrive at rate ArrivalRate (λ, users/sec) with inter-arrival times
// drawn from an exponential distribution (mean = 1/λ).  Each session lasts
// SessionDurationSec seconds on average.  At steady state, concurrent sessions
// ≈ λ × SessionDurationSec (Little's Law).
//
// NOTE: The actual Poisson session scheduling is managed by the session.Manager
// for the "poisson" pattern type.  This struct provides the BandwidthAt hook
// that the PatternController calls; it returns the per-session bandwidth
// divided by the current session count (so aggregate output stays near Target).
type PoissonPattern struct {
	// ArrivalRate is λ (sessions per second).
	ArrivalRate float64

	// SessionDurationSec is the mean session length.
	SessionDurationSec float64

	// TargetBps is the aggregate bandwidth goal across all sessions.
	TargetBps int64

	// MaxSessions caps the goroutine count.
	MaxSessions int

	// activeSessions is updated externally by the session.Manager.
	activeSessions *int32

	rng *rand.Rand
}

// NewPoisson creates a PoissonPattern.
// activeSessions must be a pointer to the session manager's live counter.
func NewPoisson(arrivalRate float64, sessionDurationSec float64, targetBps int64, maxSessions int, activeSessions *int32) *PoissonPattern {
	return &PoissonPattern{
		ArrivalRate:        arrivalRate,
		SessionDurationSec: sessionDurationSec,
		TargetBps:          targetBps,
		MaxSessions:        maxSessions,
		activeSessions:     activeSessions,
		rng:                rand.New(rand.NewSource(time.Now().UnixNano())), //nolint:gosec
	}
}

func (p *PoissonPattern) SetStartTime(_ time.Time) {}

// BandwidthAt returns the per-session rate needed to hit TargetBps in total,
// divided across however many sessions are currently active.
func (p *PoissonPattern) BandwidthAt(_ time.Time) int64 {
	active := int32(1)
	if p.activeSessions != nil {
		active = *p.activeSessions
		if active <= 0 {
			active = 1
		}
	}
	return p.TargetBps / int64(active)
}

// NextInterArrivalSec samples the next inter-arrival time from Exp(1/λ).
func (p *PoissonPattern) NextInterArrivalSec() float64 {
	if p.ArrivalRate <= 0 {
		return 1.0
	}
	return -math.Log(1-p.rng.Float64()) / p.ArrivalRate
}

// SessionDuration returns the mean session duration.
func (p *PoissonPattern) SessionDuration() time.Duration {
	return time.Duration(p.SessionDurationSec * float64(time.Second))
}
