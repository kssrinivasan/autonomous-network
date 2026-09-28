# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

BINARY     := traffic-agent
MODULE     := gitlab.com/tme4/gnnsandbox/traffic-agent
GO_FLAGS   := CGO_ENABLED=0 GOOS=linux GOARCH=amd64
BUILD_FLAGS := -trimpath -ldflags="-s -w"

.PHONY: all build test lint tidy docker clean

all: build

## build: compile the binary for Linux/amd64
build:
	$(GO_FLAGS) go build $(BUILD_FLAGS) -o $(BINARY) ./cmd/agent
	@echo "Built: $(BINARY)"

## build-local: compile for the current OS/arch (for local testing)
build-local:
	go build $(BUILD_FLAGS) -o $(BINARY) ./cmd/agent

## test: run unit tests
test:
	go test -v -race ./...

## lint: run golangci-lint (install separately)
lint:
	golangci-lint run ./...

## tidy: tidy and verify go modules
tidy:
	go mod tidy
	go mod verify

## docker: build the carrier Docker image locally
docker:
	docker build -t $(BINARY):latest .

## clean: remove build artefacts
clean:
	rm -f $(BINARY)

## help: list available targets
help:
	@grep -E '^## ' Makefile | sed 's/## //'
