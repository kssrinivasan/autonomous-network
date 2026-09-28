# Copyright 2024-2025 Google LLC
# Licensed under the Apache License, Version 2.0

# Default target
.DEFAULT_GOAL := all

# Docker image configuration
DOCKER_IMAGE_OWNER = {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/networkagent
DOCKER_IMAGE_TAG = latest

# Agent image for traffic-agent binary
AGENT_IMAGE = {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/networkagent/traffic-agent:latest

# ============================================================================
# Base builder image (unchanged, reused by consolidated device image)
# ============================================================================
.PHONY: base
base:
	cd /opt/free5gc && docker build -t free5gc/base:latest -f ./base/Dockerfile .

# ============================================================================
# Consolidated device image (replaces all individual NF images)
# ============================================================================
.PHONY: device
device: base
	cd /opt/free5gc && docker build \
		--build-arg AGENT_IMAGE=$(AGENT_IMAGE) \
		-t device:$(DOCKER_IMAGE_TAG) \
		-f ./device/Dockerfile \
		.

# ============================================================================
# Build all images
# ============================================================================
.PHONY: all
all: device
	@echo "✓ Built consolidated device image (includes webui)"

# ============================================================================
# Legacy targets (for backwards compatibility during migration)
# These now build the consolidated device image
# ============================================================================
.PHONY: amf smf upf nrf udr udm ausf nssf pcf n3iwf ueransim
amf smf upf nrf udr udm ausf nssf pcf n3iwf ueransim: device
	@echo "⚠ Legacy target '$@' now builds consolidated 'device' image"
	@echo "  To run as $@: docker run -e NF_MODE=$@ $(DOCKER_IMAGE_OWNER)/device:$(DOCKER_IMAGE_TAG)"
