// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package bandwidth provides helpers for parsing and formatting network
// bandwidth values expressed as strings (e.g. "100Mbps", "1Gbps").
package bandwidth

import (
	"fmt"
	"strconv"
	"strings"
)

const (
	Kbps = int64(1_000)
	Mbps = int64(1_000_000)
	Gbps = int64(1_000_000_000)
)

// Parse converts a human-readable bandwidth string to bits per second.
// Recognised suffixes: Gbps, Mbps, Kbps (case-sensitive).
// A bare integer is treated as bits per second.
func Parse(s string) (int64, error) {
	s = strings.TrimSpace(s)
	if s == "" || s == "0" || s == "0Kbps" || s == "0Mbps" || s == "0Gbps" {
		return 0, nil
	}

	switch {
	case strings.HasSuffix(s, "Gbps"):
		v, err := strconv.ParseFloat(s[:len(s)-4], 64)
		if err != nil {
			return 0, fmt.Errorf("invalid bandwidth %q: %w", s, err)
		}
		return int64(v * float64(Gbps)), nil

	case strings.HasSuffix(s, "Mbps"):
		v, err := strconv.ParseFloat(s[:len(s)-4], 64)
		if err != nil {
			return 0, fmt.Errorf("invalid bandwidth %q: %w", s, err)
		}
		return int64(v * float64(Mbps)), nil

	case strings.HasSuffix(s, "Kbps"):
		v, err := strconv.ParseFloat(s[:len(s)-4], 64)
		if err != nil {
			return 0, fmt.Errorf("invalid bandwidth %q: %w", s, err)
		}
		return int64(v * float64(Kbps)), nil

	default:
		v, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid bandwidth %q (no unit suffix)", s)
		}
		return v, nil
	}
}

// MustParse is like Parse but panics on error. Use for compile-time constants.
func MustParse(s string) int64 {
	v, err := Parse(s)
	if err != nil {
		panic(err)
	}
	return v
}

// Format converts bits per second to a human-readable string.
func Format(bps int64) string {
	switch {
	case bps >= Gbps:
		return fmt.Sprintf("%.2fGbps", float64(bps)/float64(Gbps))
	case bps >= Mbps:
		return fmt.Sprintf("%.2fMbps", float64(bps)/float64(Mbps))
	case bps >= Kbps:
		return fmt.Sprintf("%.2fKbps", float64(bps)/float64(Kbps))
	default:
		return fmt.Sprintf("%dbps", bps)
	}
}
