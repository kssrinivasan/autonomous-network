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
	"time"
)

// WaveType selects the waveform for PeriodicPattern.
type WaveType string

const (
	WaveSine     WaveType = "sine"
	WaveSquare   WaveType = "square"
	WaveSawtooth WaveType = "sawtooth"
)

// PeriodicPattern generates a single-component oscillating bandwidth using a
// configurable waveform.  It is kept for backward compatibility with the
// existing "periodic" pattern_type CRD field.
//
// Internally it is equivalent to a MultiSinePattern with one component and
// time_reference = "elapsed".
//
// Waveform behaviour (let φ = 2π·t/period):
//
//	sine:     rate = base ± amplitude × sin(φ)
//	square:   rate = base + amplitude × sign(sin(φ))
//	sawtooth: rate = base + amplitude × ((φ/π) mod 2 − 1)
type PeriodicPattern struct {
	WaveType     WaveType
	PeriodSec    float64
	BaseRateBps  int64
	AmplitudeBps int64

	startTime time.Time
}

func (p *PeriodicPattern) SetStartTime(t time.Time) { p.startTime = t }

func (p *PeriodicPattern) BandwidthAt(t time.Time) int64 {
	if p.startTime.IsZero() {
		p.startTime = t
	}
	if p.PeriodSec <= 0 {
		return p.BaseRateBps
	}

	elapsed := t.Sub(p.startTime).Seconds()
	phase := 2 * math.Pi * elapsed / p.PeriodSec

	var multiplier float64
	switch p.WaveType {
	case WaveSine:
		multiplier = math.Sin(phase)
	case WaveSquare:
		if math.Sin(phase) >= 0 {
			multiplier = 1
		} else {
			multiplier = -1
		}
	case WaveSawtooth:
		// Linear ramp 0 → 1 over each period, then reset.
		multiplier = 2*(elapsed/p.PeriodSec-math.Floor(elapsed/p.PeriodSec)) - 1
	default:
		multiplier = math.Sin(phase)
	}

	result := float64(p.BaseRateBps) + float64(p.AmplitudeBps)*multiplier
	if result < 0 {
		result = 0
	}
	return int64(result)
}
