# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ============================================================================
# Global ARG for traffic-agent image (must be before first FROM)
# ============================================================================
ARG AGENT_IMAGE=traffic-agent:latest

# ============================================================================
# Stage 1: Reuse existing free5gc builder base
# ============================================================================
FROM free5gc/base AS free5gc-base

# ============================================================================
# Stage 2: Compile ALL free5gc network functions
# ============================================================================
FROM free5gc-base AS free5gc-compiler

ENV DEBIAN_FRONTEND=noninteractive
ENV GOPATH=/go

# The base image (free5gc/base) already has Node.js 22 + yarn installed.
# webconsole Makefile calls `sudo corepack enable` but sudo isn't in the build container
# (we're already root), so shim it as a passthrough.
RUN corepack enable && \
    printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo && \
    chmod +x /usr/local/bin/sudo

# Copy free5gc source from host (provided by ansible)
COPY base/free5gc/ ${GOPATH}/src/free5gc/

# Compile all network functions in sequence
# Each make target produces binary in /go/src/free5gc/bin/{nf}
RUN cd ${GOPATH}/src/free5gc && \
    make amf && \
    make smf && \
    make upf && \
    make nrf && \
    make udr && \
    make udm && \
    make ausf && \
    make nssf && \
    make pcf && \
    make n3iwf && \
    make webconsole

# Build gtp5g-tunnel (required by UPF)
RUN git clone https://github.com/free5gc/go-gtp5gnl.git && \
    mkdir "go-gtp5gnl/bin" && \
    cd "go-gtp5gnl/cmd/gogtp5g-tunnel" && \
    go build -o "${GOPATH}/gtp5g-tunnel" . && \
    cd -

# ============================================================================
# Stage 3: Build UERANSIM (UE + gNB simulator)
# ============================================================================
FROM ubuntu:22.04 AS ueransim-builder

ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y \
    g++ \
    make \
    cmake \
    libsctp-dev \
    lksctp-tools \
    git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone and build UERANSIM
WORKDIR /UERANSIM
RUN git clone -b master https://github.com/aligungr/UERANSIM.git . && \
    git checkout v3.3.0 && \
    make

# Binaries produced:
# - /UERANSIM/build/nr-gnb (gNodeB simulator)
# - /UERANSIM/build/nr-ue (User Equipment simulator)
# - /UERANSIM/build/nr-cli (CLI tool)
# - /UERANSIM/build/nr-binder + libdevbnd.so

# ============================================================================
# Stage 4: Pull traffic-agent binary from carrier image
# ============================================================================
FROM ${AGENT_IMAGE} AS traffic-agent

# ============================================================================
# Stage 5: Final consolidated device runtime image
# ============================================================================
FROM ubuntu:22.04

LABEL description="Consolidated free5gc + UERANSIM + traffic-agent device image" \
      version="1.0.0"

ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
# - UPF needs: libmnl0, libyaml-0-2, iptables
# - UERANSIM needs: libsctp-dev, lksctp-tools, libstdc++6 (GLIBC 2.32+)
# - Networking tools: iproute2, iputils-ping, tcpdump, curl, netcat
RUN apt-get update && apt-get install -y \
    libmnl0 \
    libyaml-0-2 \
    iptables \
    iproute2 \
    iputils-ping \
    tcpdump \
    nmon \
    curl \
    netcat \
    libsctp-dev \
    lksctp-tools \
    libstdc++6 \
    ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/device

# ============================================================================
# Copy free5gc binaries from compiler stage
# ============================================================================
RUN mkdir -p bin/free5gc cert config

COPY --from=free5gc-compiler /go/src/free5gc/bin/amf ./bin/free5gc/amf
COPY --from=free5gc-compiler /go/src/free5gc/bin/smf ./bin/free5gc/smf
COPY --from=free5gc-compiler /go/src/free5gc/bin/upf ./bin/free5gc/upf
COPY --from=free5gc-compiler /go/src/free5gc/bin/nrf ./bin/free5gc/nrf
COPY --from=free5gc-compiler /go/src/free5gc/bin/udr ./bin/free5gc/udr
COPY --from=free5gc-compiler /go/src/free5gc/bin/udm ./bin/free5gc/udm
COPY --from=free5gc-compiler /go/src/free5gc/bin/ausf ./bin/free5gc/ausf
COPY --from=free5gc-compiler /go/src/free5gc/bin/nssf ./bin/free5gc/nssf
COPY --from=free5gc-compiler /go/src/free5gc/bin/pcf ./bin/free5gc/pcf
COPY --from=free5gc-compiler /go/src/free5gc/bin/n3iwf ./bin/free5gc/n3iwf
COPY --from=free5gc-compiler /go/gtp5g-tunnel ./bin/free5gc/gtp5g-tunnel

# Copy webui binary and static files
COPY --from=free5gc-compiler /go/src/free5gc/webconsole/bin/webconsole ./bin/free5gc/webui
COPY --from=free5gc-compiler /go/src/free5gc/webconsole/public ./public

# Copy certificates and configs
COPY --from=free5gc-compiler /go/src/free5gc/cert/* ./cert/
COPY --from=free5gc-compiler /go/src/free5gc/config/* ./config/

# ============================================================================
# Copy UERANSIM binaries
# ============================================================================
RUN mkdir -p bin/ueransim lib

COPY --from=ueransim-builder /UERANSIM/build/nr-gnb ./bin/ueransim/nr-gnb
COPY --from=ueransim-builder /UERANSIM/build/nr-ue ./bin/ueransim/nr-ue
COPY --from=ueransim-builder /UERANSIM/build/nr-cli ./bin/ueransim/nr-cli
COPY --from=ueransim-builder /UERANSIM/build/nr-binder ./bin/ueransim/nr-binder
COPY --from=ueransim-builder /UERANSIM/build/libdevbnd.so ./lib/libdevbnd.so

# ============================================================================
# Copy traffic-agent binary
# ============================================================================
COPY --from=traffic-agent /traffic-agent ./bin/traffic-agent

# Make all binaries executable
RUN chmod +x bin/free5gc/* bin/ueransim/* bin/traffic-agent

# Add binaries to PATH
ENV PATH="/opt/device/bin/free5gc:/opt/device/bin/ueransim:/opt/device/bin:${PATH}"
ENV LD_LIBRARY_PATH="/opt/device/lib"

# Copy entrypoint script
COPY device/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose common ports
# - AMF: 38412 (NGAP), 8000 (SBI)
# - SMF: 8000 (SBI)
# - UPF: 8805 (PFCP), 2152 (GTP-U)
# - NRF: 8000 (SBI)
# - WebUI: 5000 (HTTP)
# - Traffic-agent: 9090 (control), 5201 (traffic)
# - UERANSIM: 4997 (NGAP)
EXPOSE 38412 8000 8805 2152 5000 9090 5201 4997

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]

# Sample usage for later
# ======================
# # Start container (sleeps forever)
# docker run -d --name amf-device my-image

# # Copy your config
# docker cp ./amfcfg.yaml amf-device:/opt/device/config/

# # Execute the actual service
# docker exec -d amf-device /opt/device/bin/free5gc/amf -c /opt/device/config/amfcfg.yaml

# # Or override CMD at startup if config is already mounted
# docker run -d --name amf-device \
#   -v $(pwd)/config:/opt/device/config:ro \
#   my-image /opt/device/bin/free5gc/amf -c /opt/device/config/amfcfg.yaml

# # Run as webui
# docker run -v /configs:/opt/device/config \
#   europe-west1-docker.pkg.dev/agents-1234/networkagent/device:latest \
#   /opt/device/bin/free5gc/webui -c /opt/device/config/webuicfg.yaml
