# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

description="""
VyOS Chaos Agent — Controlled Network Fault Injection

Injects and manages reversible network failures in the VyOS-based MPLS/L3VPN topology for
testing GNN detection, RCA scenarios, and autonomous remediation workflows.

Supported failure types:
- MTU_MISMATCH: Silent packet drops due to MTU misconfiguration
- BGP_SESSION_DOWN: Tear down eBGP/iBGP sessions, causing VPN blackouts
- PROCESS_CRASH: Simulate BGP daemon crashes or loopback failures on route reflectors
- PACKET_CORRUPTION: Inject CRC errors/drops to simulate degrading hardware
- LINK_DOWN: Physical link failures (interface or bridge mode)
- OSPF_AREA_MISMATCH: OSPF adjacency failures due to area misconfiguration
- DUPLICATE_IP: IP address conflicts causing ARP flapping and routing instability
- TXQUEUE_STARVATION: Kernel-level queue exhaustion invisible to config management
- OSPF_COST_INFLATION: Legal-but-wrong OSPF costs forcing suboptimal routing
- VRF_RT_MISCONFIGURATION: Silent VPN isolation via route-target misconfiguration

All faults are fully reversible and tracked in Spanner via FaultEvent telemetry.
"""

tags=['chat', 'network-fault', 'chaos-engineering', 'fault-injection']

examples=[
    "Inject an MTU mismatch on pe1 eth1 with MTU 1400",
    "Bring down the BGP session between pe2 and ce1-hub in the BLUE_HUB VRF",
    "Crash the BGP process on route reflector rr1",
    "Simulate a failing SFP on p1 eth2 with 5% packet corruption",
    "Bring down the link between p1 and p2 using bridge mode",
    "Create an OSPF area mismatch on p2 eth2",
    "Inject a duplicate IP 172.16.30.1/24 on p3 eth3",
    "Shrink the tx queue on pe2 eth2 to 20 packets",
    "Set OSPF cost to 65535 on p2 eth1",
    "Misconfigure the route target on pe3 BLUE_SPOKE VRF",
    "List all active faults",
    "Remove the MTU fault on pe1",
    "What fault should I inject to test GNN detection of silent configuration errors?",
]
