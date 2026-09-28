// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package patterns

import "time"

// ConstantPattern generates a fixed, unwavering bandwidth for the duration
// of the test.  It is the simplest pattern and the default when no
// pattern_type is specified.
type ConstantPattern struct {
	rateBps int64
}

// NewConstant creates a ConstantPattern at the given bits-per-second rate.
func NewConstant(rateBps int64) *ConstantPattern {
	return &ConstantPattern{rateBps: rateBps}
}

func (p *ConstantPattern) BandwidthAt(_ time.Time) int64 { return p.rateBps }
func (p *ConstantPattern) SetStartTime(_ time.Time)      {}
