// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package config defines the configuration structures for the traffic agent.
// The JSON field names are kept identical to the existing config_PORT.json
// format so that the operator can pass configs with zero changes in Phase 1.
package config

import (
	"encoding/json"
	"fmt"
	"os"
)

// OneShotConfig is the JSON format read by "traffic-agent run --config FILE".
// Field names match the existing Ansible-generated config exactly.
type OneShotConfig struct {
	TestName        string                 `json:"test_name"`
	SourceDevice    string                 `json:"source_device"`
	SourceIP        string                 `json:"source_ip"`
	DestDevice      string                 `json:"destination_device"`
	DestIP          string                 `json:"destination_ip"`
	Protocol        string                 `json:"protocol"`
	Port            int                    `json:"port"`
	Duration        int                    `json:"duration"`
	Bandwidth       string                 `json:"bandwidth"`
	PatternType     string                 `json:"pattern_type"`
	PatternConfig   map[string]interface{} `json:"pattern_config"`
	ConcurrentUsers int                    `json:"concurrent_users"`
	SessionDuration *int                   `json:"session_duration"`
	ThinkTime       int                    `json:"think_time"`
	MetricsEnabled  bool                   `json:"metrics_enabled"`
	MetricsInterval int                    `json:"metrics_interval"`
}

// FlowRequest is the HTTP/JSON body for POST /v1/flows (daemon mode).
// This is the canonical flow definition used between the operator and agent.
type FlowRequest struct {
	FlowID             string          `json:"flow_id"`
	Role               string          `json:"role"`               // "source" or "destination"
	DestinationIP      string          `json:"destination_ip"`
	Port               int             `json:"port"`
	Protocol           string          `json:"protocol"`           // "TCP" or "UDP"
	DurationSec        int             `json:"duration_sec"`
	PatternType        string          `json:"pattern_type"`
	PatternConfig      json.RawMessage `json:"pattern_config"`
	ConcurrentSessions int             `json:"concurrent_sessions"`
	BandwidthBps       int64           `json:"bandwidth_bps"`
	// SourceIP, when set, binds the source socket to this address so packets
	// egress via the interface that owns the IP (e.g. a UERANSIM uesimtun).
	SourceIP           string          `json:"source_ip,omitempty"`
}

// PatternConfigs — typed configuration structs for each pattern type.

// BurstPatternConfig configures the burst/idle pattern.
type BurstPatternConfig struct {
	BurstDuration int    `json:"burst_duration"` // seconds
	BurstInterval int    `json:"burst_interval"` // seconds between burst starts
	BurstRate     string `json:"burst_rate"`     // e.g. "100Mbps"
	IdleRate      string `json:"idle_rate"`      // e.g. "1Mbps"
}

// PeriodicPatternConfig configures a single-component wave pattern.
// Kept for backward compatibility; maps to MultiSinePatternConfig internally.
type PeriodicPatternConfig struct {
	WaveType  string `json:"wave_type"`  // "sine", "square", "sawtooth"
	Period    int    `json:"period"`     // seconds per full cycle
	BaseRate  string `json:"base_rate"`  // centre/minimum rate
	Amplitude string `json:"amplitude"`  // peak deviation from base
}

// MultiSineComponent is a single sinusoidal term in a composite wave.
type MultiSineComponent struct {
	// PeriodSec is the full cycle duration in seconds.
	// Common values: 86400 (daily), 604800 (weekly), 31536000 (annual).
	PeriodSec float64 `json:"period"`

	// Amplitude is the peak deviation from base_rate (in bandwidth string format).
	Amplitude string `json:"amplitude"`

	// PhaseOffsetSec shifts the wave in time.
	// For wall_clock mode use this to align the peak to a specific time of day:
	//   offset = period/4 - peak_time_of_day_in_seconds
	// e.g. to peak at 14:00 UTC with period=86400: offset = 21600 - 50400 = -28800
	PhaseOffsetSec float64 `json:"phase_offset"`
}

// MultiSinePatternConfig drives the composite sinusoidal traffic model.
// This is the primary pattern for modelling time-of-day and seasonal variation.
type MultiSinePatternConfig struct {
	// BaseRate is the traffic floor (centre of oscillation).
	BaseRate string `json:"base_rate"`

	// TimeReference controls how t is interpreted in BandwidthAt:
	//   "wall_clock" — t is actual UTC Unix timestamp (anchors to real time)
	//   "elapsed"    — t is seconds since test started (default)
	TimeReference string `json:"time_reference"`

	// Components is the list of sinusoidal terms to superimpose.
	Components []MultiSineComponent `json:"components"`

	// NoiseStddevPct adds Gaussian noise as a percentage of the current rate
	// (e.g. 3.0 means ±3% random variation). Useful for realistic jitter.
	NoiseStddevPct float64 `json:"noise_stddev_pct"`

	// MinRate and MaxRate clamp the output to prevent negative/excessive rates.
	MinRate string `json:"min_rate"`
	MaxRate string `json:"max_rate"`
}

// Waypoint is a single point in a Schedule pattern.
type Waypoint struct {
	// Time is either "HH:MM" (UTC, for wall_clock) or an integer string (seconds
	// from test start, for elapsed).
	Time string `json:"time"`

	// Rate is the target bandwidth at this waypoint (e.g. "50Mbps").
	Rate string `json:"rate"`
}

// SchedulePatternConfig defines a piecewise traffic profile.
// The agent interpolates between waypoints to produce smooth or step-wise curves
// that align to real clock time — perfect for business-hours simulation.
type SchedulePatternConfig struct {
	// TimeReference: "wall_clock" or "elapsed" (see MultiSinePatternConfig).
	TimeReference string `json:"time_reference"`

	// Interpolation controls how values change between waypoints:
	//   "linear" — smooth linear ramp (default)
	//   "step"   — hold previous value until next waypoint (staircase)
	Interpolation string `json:"interpolation"`

	// Repeat controls cycle behaviour once the last waypoint is passed:
	//   "daily"   — repeat every 24 hours
	//   "weekly"  — repeat every 7 days
	//   "none"    — hold the last rate indefinitely
	Repeat string `json:"repeat"`

	Waypoints []Waypoint `json:"waypoints"`
}

// PoissonPatternConfig configures stochastic user arrival simulation.
type PoissonPatternConfig struct {
	// ArrivalRate is the average number of new sessions per second (λ).
	ArrivalRate float64 `json:"arrival_rate"`
}

// LoadOneShotConfig reads and parses a OneShotConfig from a JSON file.
func LoadOneShotConfig(path string) (*OneShotConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading config file %q: %w", path, err)
	}
	var cfg OneShotConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing config file %q: %w", path, err)
	}
	// Apply defaults
	if cfg.Protocol == "" {
		cfg.Protocol = "TCP"
	}
	if cfg.ConcurrentUsers < 1 {
		cfg.ConcurrentUsers = 1
	}
	if cfg.Duration < 1 {
		cfg.Duration = 60
	}
	if cfg.Bandwidth == "" {
		cfg.Bandwidth = "10Mbps"
	}
	if cfg.PatternType == "" {
		cfg.PatternType = "constant"
	}
	if cfg.MetricsInterval < 1 {
		cfg.MetricsInterval = 5
	}
	return &cfg, nil
}
