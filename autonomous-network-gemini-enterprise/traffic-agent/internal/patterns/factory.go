// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package patterns

import (
	"encoding/json"
	"fmt"
	"sync/atomic"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/bandwidth"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/config"
)

// Build constructs a Pattern from a patternType string and raw JSON config.
// bandwidthBps is the top-level bandwidth field (used by constant and poisson).
// activeSessions is only required for poisson; pass nil for all other patterns.
func Build(
	patternType string,
	patternConfigJSON json.RawMessage,
	bandwidthBps int64,
	concurrentSessions int,
	activeSessions *int32,
) (Pattern, error) {
	switch patternType {
	case "", "constant":
		return NewConstant(bandwidthBps), nil

	case "burst":
		var cfg config.BurstPatternConfig
		if err := unmarshal(patternConfigJSON, &cfg); err != nil {
			return nil, fmt.Errorf("burst pattern config: %w", err)
		}
		burstBps, err := bandwidth.Parse(cfg.BurstRate)
		if err != nil {
			return nil, fmt.Errorf("burst_rate: %w", err)
		}
		idleBps, err := bandwidth.Parse(cfg.IdleRate)
		if err != nil {
			return nil, fmt.Errorf("idle_rate: %w", err)
		}
		return &BurstPattern{
			BurstDuration: cfg.BurstDuration,
			BurstInterval: cfg.BurstInterval,
			BurstRateBps:  burstBps,
			IdleRateBps:   idleBps,
		}, nil

	case "periodic":
		var cfg config.PeriodicPatternConfig
		if err := unmarshal(patternConfigJSON, &cfg); err != nil {
			return nil, fmt.Errorf("periodic pattern config: %w", err)
		}
		baseRateBps, err := bandwidth.Parse(cfg.BaseRate)
		if err != nil {
			return nil, fmt.Errorf("base_rate: %w", err)
		}
		amplitudeBps, err := bandwidth.Parse(cfg.Amplitude)
		if err != nil {
			return nil, fmt.Errorf("amplitude: %w", err)
		}
		return &PeriodicPattern{
			WaveType:     WaveType(cfg.WaveType),
			PeriodSec:    float64(cfg.Period),
			BaseRateBps:  baseRateBps,
			AmplitudeBps: amplitudeBps,
		}, nil

	case "multi_sine":
		var cfg config.MultiSinePatternConfig
		if err := unmarshal(patternConfigJSON, &cfg); err != nil {
			return nil, fmt.Errorf("multi_sine pattern config: %w", err)
		}
		return buildMultiSine(cfg)

	case "schedule":
		var cfg config.SchedulePatternConfig
		if err := unmarshal(patternConfigJSON, &cfg); err != nil {
			return nil, fmt.Errorf("schedule pattern config: %w", err)
		}
		return buildSchedule(cfg)

	case "poisson":
		var cfg config.PoissonPatternConfig
		if err := unmarshal(patternConfigJSON, &cfg); err != nil {
			return nil, fmt.Errorf("poisson pattern config: %w", err)
		}
		sessionDuration := 60.0 // default 1 minute
		maxSessions := concurrentSessions
		if maxSessions < 1 {
			maxSessions = 100
		}
		// activeSessions counter: if not provided, use a local one.
		if activeSessions == nil {
			activeSessions = new(int32)
			atomic.StoreInt32(activeSessions, 1)
		}
		return NewPoisson(cfg.ArrivalRate, sessionDuration, bandwidthBps, maxSessions, activeSessions), nil

	default:
		return nil, fmt.Errorf("unknown pattern_type %q", patternType)
	}
}

// BuildFromOneShotConfig constructs a Pattern from a OneShotConfig's raw
// pattern_config map (the format produced by the existing Ansible playbook).
func BuildFromOneShotConfig(cfg *config.OneShotConfig, activeSessions *int32) (Pattern, error) {
	bps, err := bandwidth.Parse(cfg.Bandwidth)
	if err != nil {
		return nil, fmt.Errorf("bandwidth: %w", err)
	}

	// Re-encode the map[string]interface{} to JSON so Build can unmarshal it
	// into the typed config struct.
	rawJSON, err := json.Marshal(cfg.PatternConfig)
	if err != nil {
		return nil, fmt.Errorf("marshalling pattern_config: %w", err)
	}

	return Build(cfg.PatternType, rawJSON, bps, cfg.ConcurrentUsers, activeSessions)
}

// --- helpers ---

func unmarshal(raw json.RawMessage, dst interface{}) error {
	if len(raw) == 0 || string(raw) == "null" || string(raw) == "{}" {
		return nil // empty config is valid for constant / defaults
	}
	return json.Unmarshal(raw, dst)
}

func buildMultiSine(cfg config.MultiSinePatternConfig) (*MultiSinePattern, error) {
	baseRateBps, err := bandwidth.Parse(cfg.BaseRate)
	if err != nil {
		return nil, fmt.Errorf("base_rate: %w", err)
	}
	minRateBps, _ := bandwidth.Parse(cfg.MinRate) // zero on error → no floor
	maxRateBps, _ := bandwidth.Parse(cfg.MaxRate) // zero on error → no ceiling

	components := make([]MultiSineComponent, 0, len(cfg.Components))
	for i, c := range cfg.Components {
		ampBps, err := bandwidth.Parse(c.Amplitude)
		if err != nil {
			return nil, fmt.Errorf("component[%d] amplitude: %w", i, err)
		}
		components = append(components, MultiSineComponent{
			PeriodSec:      c.PeriodSec,
			AmplitudeBps:   ampBps,
			PhaseOffsetSec: c.PhaseOffsetSec,
		})
	}

	return NewMultiSine(baseRateBps, components, cfg.NoiseStddevPct, minRateBps, maxRateBps, cfg.TimeReference), nil
}

func buildSchedule(cfg config.SchedulePatternConfig) (*SchedulePattern, error) {
	wps, err := ParseWaypoints(cfg.Waypoints, bandwidth.Parse)
	if err != nil {
		return nil, err
	}

	return &SchedulePattern{
		Waypoints:     wps,
		Interpolation: cfg.Interpolation,
		RepeatSec:     RepeatSeconds(cfg.Repeat),
		TimeReference: cfg.TimeReference,
	}, nil
}
