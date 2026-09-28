// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package patterns

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/config"
)

// ScheduleWaypoint is a (time-offset, rate) pair on the schedule curve.
type ScheduleWaypoint struct {
	OffsetSec float64 // seconds from midnight (wall_clock) or test start (elapsed)
	RateBps   int64
}

// SchedulePattern describes a piecewise traffic profile defined by time/rate
// waypoints.  The agent interpolates between waypoints to produce either a
// smooth linear ramp or a step-wise staircase.
//
// With time_reference = "wall_clock" and repeat = "daily" this produces a
// realistic business-hours traffic shape that restarts every day at midnight.
//
// Example YAML (business hours):
//
//	pattern_type: schedule
//	pattern_config:
//	  time_reference: wall_clock
//	  interpolation: linear
//	  repeat: daily
//	  waypoints:
//	    - time: "00:00"   rate: 5Mbps
//	    - time: "09:00"   rate: 80Mbps
//	    - time: "12:00"   rate: 60Mbps
//	    - time: "14:00"   rate: 90Mbps
//	    - time: "18:00"   rate: 30Mbps
//	    - time: "22:00"   rate: 10Mbps
type SchedulePattern struct {
	Waypoints     []ScheduleWaypoint
	Interpolation string // "linear" or "step"
	RepeatSec     float64 // 0 = no repeat; 86400 = daily; 604800 = weekly
	TimeReference string  // "wall_clock" or "elapsed"

	startTime time.Time
}

func (p *SchedulePattern) SetStartTime(t time.Time) { p.startTime = t }

// BandwidthAt interpolates the schedule at time t.
func (p *SchedulePattern) BandwidthAt(t time.Time) int64 {
	if p.startTime.IsZero() {
		p.startTime = t
	}
	if len(p.Waypoints) == 0 {
		return 0
	}
	if len(p.Waypoints) == 1 {
		return p.Waypoints[0].RateBps
	}

	var tSec float64
	if p.TimeReference == "wall_clock" {
		// Seconds since midnight UTC
		h, m, s := t.UTC().Clock()
		tSec = float64(h*3600 + m*60 + s)
	} else {
		tSec = t.Sub(p.startTime).Seconds()
	}

	// Apply repeat wrapping
	if p.RepeatSec > 0 {
		tSec = tSec - float64(int(tSec/p.RepeatSec))*p.RepeatSec
	}

	// Find surrounding waypoints
	wp := p.Waypoints
	if tSec <= wp[0].OffsetSec {
		return wp[0].RateBps
	}
	if tSec >= wp[len(wp)-1].OffsetSec {
		return wp[len(wp)-1].RateBps
	}

	// Binary search for the segment containing tSec
	idx := sort.Search(len(wp)-1, func(i int) bool {
		return wp[i+1].OffsetSec >= tSec
	})

	prev := wp[idx]
	next := wp[idx+1]

	if p.Interpolation == "step" {
		return prev.RateBps
	}

	// Linear interpolation
	frac := (tSec - prev.OffsetSec) / (next.OffsetSec - prev.OffsetSec)
	rate := float64(prev.RateBps) + frac*float64(next.RateBps-prev.RateBps)
	if rate < 0 {
		rate = 0
	}
	return int64(rate)
}

// ParseWaypoints converts the raw config waypoint list into ScheduleWaypoints.
// Time values are parsed as:
//   - "HH:MM"   for wall_clock mode  → offset in seconds from midnight
//   - integer   for elapsed mode     → offset in seconds from test start
func ParseWaypoints(raw []config.Waypoint, parseRate func(string) (int64, error)) ([]ScheduleWaypoint, error) {
	result := make([]ScheduleWaypoint, 0, len(raw))
	for _, w := range raw {
		rateBps, err := parseRate(w.Rate)
		if err != nil {
			return nil, fmt.Errorf("waypoint rate %q: %w", w.Rate, err)
		}
		offsetSec, err := parseTime(w.Time)
		if err != nil {
			return nil, fmt.Errorf("waypoint time %q: %w", w.Time, err)
		}
		result = append(result, ScheduleWaypoint{OffsetSec: offsetSec, RateBps: rateBps})
	}
	// Sort by offset ascending so binary search works correctly.
	sort.Slice(result, func(i, j int) bool {
		return result[i].OffsetSec < result[j].OffsetSec
	})
	return result, nil
}

// parseTime parses "HH:MM" or a plain integer string into seconds.
func parseTime(s string) (float64, error) {
	s = strings.TrimSpace(s)
	if strings.Contains(s, ":") {
		parts := strings.SplitN(s, ":", 2)
		h, err := strconv.Atoi(parts[0])
		if err != nil {
			return 0, fmt.Errorf("invalid hour in %q", s)
		}
		m, err := strconv.Atoi(parts[1])
		if err != nil {
			return 0, fmt.Errorf("invalid minute in %q", s)
		}
		return float64(h*3600 + m*60), nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid time %q", s)
	}
	return v, nil
}

// RepeatSeconds converts a repeat string to a cycle period in seconds.
func RepeatSeconds(repeat string) float64 {
	switch strings.ToLower(repeat) {
	case "daily":
		return 86400
	case "weekly":
		return 604800
	default:
		return 0 // no repeat
	}
}
