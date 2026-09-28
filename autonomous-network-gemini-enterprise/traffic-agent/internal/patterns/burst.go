// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package patterns

import "time"

// BurstPattern alternates between a high "burst" rate and a low "idle" rate
// on a fixed cycle.  The cycle is always measured from test start (elapsed).
//
// A burst occupies the first BurstDuration seconds of each BurstInterval
// cycle; the remaining (BurstInterval - BurstDuration) seconds use IdleRate.
//
// Unlike the old Python implementation which stopped and restarted iperf3 at
// each phase boundary, this pattern simply adjusts the token-bucket fill rate
// in real time — keeping all connections alive throughout the test.
type BurstPattern struct {
	BurstDuration int   // seconds of high traffic per cycle
	BurstInterval int   // total cycle length in seconds
	BurstRateBps  int64 // bits per second during burst
	IdleRateBps   int64 // bits per second during idle

	startTime time.Time
}

func (p *BurstPattern) SetStartTime(t time.Time) { p.startTime = t }

// BandwidthAt returns BurstRateBps if we are in the burst phase of the
// current cycle, or IdleRateBps otherwise.
func (p *BurstPattern) BandwidthAt(t time.Time) int64 {
	if p.startTime.IsZero() {
		p.startTime = t
	}
	elapsed := t.Sub(p.startTime).Seconds()

	interval := float64(p.BurstInterval)
	if interval <= 0 {
		return p.BurstRateBps
	}

	// Position within the current cycle (0 … interval)
	cyclePos := elapsed - float64(int(elapsed/interval))*interval

	if cyclePos < float64(p.BurstDuration) {
		return p.BurstRateBps
	}
	return p.IdleRateBps
}
