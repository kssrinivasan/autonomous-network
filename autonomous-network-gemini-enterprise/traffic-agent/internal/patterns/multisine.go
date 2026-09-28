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

// MultiSineComponent describes a single sinusoidal term.
type MultiSineComponent struct {
	// PeriodSec is the full-cycle duration in seconds.
	// Typical values: 86400 (daily), 604800 (weekly), 31536000 (annual).
	PeriodSec float64

	// AmplitudeBps is the peak deviation from BaseRateBps in bits per second.
	AmplitudeBps int64

	// PhaseOffsetSec shifts the sine peak in time.
	//
	// Wall-clock example — to peak at 14:00 UTC with a 24-hour period:
	//   peak_seconds_into_day = 14 * 3600 = 50400
	//   sine peaks when 2π(t + offset)/period = π/2
	//   → offset = period/4 − peak = 21600 − 50400 = −28800
	//
	// Elapsed example — to peak 30 minutes into a 2-hour test:
	//   offset = period/4 − 1800 = 1800 − 1800 = 0  (peaks at t=period/4 by default)
	PhaseOffsetSec float64
}

// MultiSinePattern superposes N sinusoidal components onto a base rate,
// optionally with Gaussian noise.  It is the primary pattern for realistic
// time-of-day and seasonal traffic modelling.
//
// Bandwidth formula:
//
//	bw(t) = clamp( base + Σᵢ Aᵢ·sin(2π(tᵢ + φᵢ)/Tᵢ) + noise, min, max )
//
// where tᵢ is either the UTC Unix timestamp (wall_clock) or seconds since
// test start (elapsed), depending on TimeReference.
type MultiSinePattern struct {
	BaseRateBps    int64
	Components     []MultiSineComponent
	NoiseStddevPct float64 // percentage of current rate, e.g. 3.0 = ±3%
	MinRateBps     int64   // hard floor (0 = no floor)
	MaxRateBps     int64   // hard ceiling (0 = no ceiling)

	// TimeReference: "wall_clock" or "elapsed" (default "elapsed").
	TimeReference string

	startTime time.Time
	rng       *rand.Rand
}

// NewMultiSine constructs a MultiSinePattern.
func NewMultiSine(
	baseRateBps int64,
	components []MultiSineComponent,
	noiseStddevPct float64,
	minRateBps, maxRateBps int64,
	timeRef string,
) *MultiSinePattern {
	return &MultiSinePattern{
		BaseRateBps:    baseRateBps,
		Components:     components,
		NoiseStddevPct: noiseStddevPct,
		MinRateBps:     minRateBps,
		MaxRateBps:     maxRateBps,
		TimeReference:  timeRef,
		rng:            rand.New(rand.NewSource(time.Now().UnixNano())), //nolint:gosec
	}
}

func (p *MultiSinePattern) SetStartTime(t time.Time) { p.startTime = t }

// BandwidthAt evaluates the composite waveform at time t.
func (p *MultiSinePattern) BandwidthAt(t time.Time) int64 {
	if p.startTime.IsZero() {
		p.startTime = t
	}

	result := float64(p.BaseRateBps)

	for _, c := range p.Components {
		if c.PeriodSec <= 0 {
			continue
		}

		var tSec float64
		if p.TimeReference == "wall_clock" {
			tSec = float64(t.UTC().Unix())
		} else {
			// elapsed mode
			tSec = t.Sub(p.startTime).Seconds()
		}

		phase := 2 * math.Pi * (tSec + c.PhaseOffsetSec) / c.PeriodSec
		result += float64(c.AmplitudeBps) * math.Sin(phase)
	}

	// Apply Gaussian noise if configured.
	if p.NoiseStddevPct > 0 && p.rng != nil {
		noise := p.rng.NormFloat64() * p.NoiseStddevPct / 100.0
		result *= (1.0 + noise)
	}

	// Clamp to configured limits.
	if p.MinRateBps > 0 && result < float64(p.MinRateBps) {
		result = float64(p.MinRateBps)
	}
	if p.MaxRateBps > 0 && result > float64(p.MaxRateBps) {
		result = float64(p.MaxRateBps)
	}
	if result < 0 {
		result = 0
	}

	return int64(result)
}
