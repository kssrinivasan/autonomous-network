"""schema.py — node/edge types, feature layout, and row construction.

Single source of truth for the graph schema. Framework-free (numpy only) so it
can be shared by every backend.

Node types
──────────
  router     17 features:  13 continuous + 4 role one-hot
                            state, cpu, mem, ospf_num_routes, pfx_count_norm,
                            routes_fib, bfd_peers, ospf_neighbors, ospf_adj,
                            rt_import_signature, vrf_min_routes, vrf_count,
                            vrf_rt_import_mismatch,
                            role_P, role_PE, role_RR, role_CE
  interface  16 features:  state, rx_drops, tx_drops, mtu_norm, rx_errs_rate,
                            tx_errs_rate, rx_bytes_rate, tx_bytes_rate,
                            rx_packets_rate, tx_packets_rate, tx_queue_len,
                            carrier_changes, carrier_state,
                            rx_err_gradient, tx_util, rx_util
  vpn         3 features:  state, num_active_peers, total_prefixes
  flow        7 features:  throughput_sent_bps, throughput_recv_bps,
                            latency_ms, jitter_ms, packet_loss_pct,
                            is_running, is_bidirectional

Edge types (11)
───────────────
  Physical
    (router,    has_interface,  interface)
    (interface, interface_of,   router)        inverse
    (interface, connected_to,   interface)     symmetric

  Control plane
    (router,    peer_of,        router)        symmetric (OSPF + iBGP)

  Service layer
    (router,    member_of,      vpn)
    (vpn,       has_member,     router)        inverse

  Traffic
    (flow,      originates_from,interface)     src device → interface
    (flow,      terminates_at,  interface)     dst device → interface
    (interface, source_of,      flow)          inverse of originates_from
    (interface, destination_of, flow)          inverse of terminates_at
    (flow,      runs_over,      vpn)

Only the **continuous** features are scaled. For routers that is the leading
13-column slice; the trailing role one-hot columns pass through untouched.
Both the scaler-fitting path and the graph-building path derive their rows from
``continuous_row`` / ``feature_row`` here, so the two can no longer drift apart
(they used to be separate hand-written literals kept in sync by a comment).
"""

from typing import Dict, List, Sequence, Tuple

NODE_TYPES: List[str] = ["router", "interface", "vpn", "flow"]

EDGE_TYPES: List[Tuple[str, str, str]] = [
    # Physical
    ("router",      "has_interface",  "interface"),
    ("interface",   "interface_of",   "router"),      # inverse
    ("interface",   "connected_to",   "interface"),   # symmetric

    # Control plane
    ("router",      "peer_of",        "router"),      # symmetric (OSPF + iBGP)

    # Service layer
    ("router",      "member_of",      "vpn"),
    ("vpn",         "has_member",     "router"),      # inverse

    # Traffic
    ("flow",        "originates_from", "interface"),  # src device's CE-facing interface
    ("flow",        "terminates_at",  "interface"),   # dst device's CE-facing interface
    ("interface",   "source_of",      "flow"),        # inverse of originates_from
    ("interface",   "destination_of", "flow"),        # inverse of terminates_at
    ("flow",        "runs_over",      "vpn"),
]

# ── Feature keys per node type (order is the column order) ───────────────────

ROUTER_FEATURES = ["state", "cpu", "mem", "ospf_num_routes", "pfx_count_norm",
                   "routes_fib", "bfd_peers", "ospf_neighbors", "ospf_adj",
                   # VRF-aggregation features (computed per-router from VRF config + metrics):
                   # rt_import_signature — deterministic hash [0,1] of the sorted set of
                   #   rt_import RT strings across this router's VRFs. Flips when any VRF's
                   #   rt_import is misconfigured (e.g. VRF_RT_MISCONFIGURATION fault).
                   # vrf_min_routes — min route count across this router's VRFs. Drops when
                   #   any single VRF loses routes (downstream effect of RT misconfig, etc.).
                   # vrf_count — number of VRFs hosted by this router. Structural signal.
                   "rt_import_signature", "vrf_min_routes", "vrf_count",
                   "vrf_rt_import_mismatch",
                   "role_P", "role_PE", "role_RR", "role_CE"]
INTERFACE_FEATURES = ["state", "rx_drops", "tx_drops", "mtu_norm", "rx_errs_rate",
                      "tx_errs_rate", "rx_bytes_rate", "tx_bytes_rate",
                      "rx_packets_rate", "tx_packets_rate", "tx_queue_len",
                      "carrier_changes", "carrier_state",
                      "rx_err_gradient", "tx_util", "rx_util"]
VPN_FEATURES = ["state", "num_active_peers", "total_prefixes"]
FLOW_FEATURES = ["throughput_sent_bps", "throughput_recv_bps", "latency_ms",
                 "jitter_ms", "packet_loss_pct", "is_running", "is_bidirectional"]

FEATURE_NAMES: Dict[str, List[str]] = {
    "router":    ROUTER_FEATURES,
    "interface": INTERFACE_FEATURES,
    "vpn":       VPN_FEATURES,
    "flow":      FLOW_FEATURES,
}

FEATURE_DIMS: Dict[str, int] = {t: len(f) for t, f in FEATURE_NAMES.items()}

# Role one-hot order. Router rows append these four columns after the
# continuous block; they are excluded from scaling.
ROLE_ORDER = ["P", "PE", "RR", "CE"]

# Number of leading continuous columns per node type — the slice the scaler is
# fitted on and applied to. Everything after it is a one-hot / boolean column
# that must pass through unscaled.
NUM_CONTINUOUS: Dict[str, int] = {
    "router":    len(ROUTER_FEATURES) - len(ROLE_ORDER),   # 13
    "interface": len(INTERFACE_FEATURES),
    "vpn":       len(VPN_FEATURES),
    "flow":      len(FLOW_FEATURES),
}

CONTINUOUS_FEATURES: Dict[str, List[str]] = {
    t: FEATURE_NAMES[t][: NUM_CONTINUOUS[t]] for t in NODE_TYPES
}

# Per-type reconstruction-MSE thresholds. Only used on the legacy scoring path
# (model artefacts predating Mahalanobis cluster stats); see core.scoring.
ANOMALY_THRESHOLDS: Dict[str, float] = {
    "router":      0.15,
    "interface":   0.20,
    "vpn":         0.10,
    "flow":        0.20,
}

# Leading columns of a flow row that count as "traffic metrics". Flows whose
# traffic metrics are all zero are inactive/unmeasured and are excluded from
# scaler fitting so they don't pull the distribution toward zero.
FLOW_TRAFFIC_COLS = 5


def scaled_slice(node_type: str) -> slice:
    """Column slice the scaler for ``node_type`` is fitted on and applied to."""
    return slice(0, NUM_CONTINUOUS[node_type])


def role_onehot(role: str) -> List[float]:
    """Return a 4-element one-hot vector for a router role string."""
    role_u = (role or "").upper().strip()
    return [1.0 if role_u == r else 0.0 for r in ROLE_ORDER]


def _fv(value) -> float:
    """Coerce a possibly-None Spanner value to float, treating None as 0.0."""
    return float(value) if value is not None else 0.0


def continuous_row(node: Dict, node_type: str) -> List[float]:
    """Continuous-feature values for one node, in column order.

    This is the row the scaler sees — length ``NUM_CONTINUOUS[node_type]``.
    """
    return [_fv(node.get(name)) for name in CONTINUOUS_FEATURES[node_type]]


def feature_row(node: Dict, node_type: str) -> List[float]:
    """Full feature row for one node, in column order.

    Length is ``FEATURE_DIMS[node_type]``: the continuous block followed, for
    routers only, by the role one-hot block.
    """
    row = continuous_row(node, node_type)
    if node_type == "router":
        row += role_onehot(node.get("role", ""))
    return row


def metadata() -> Tuple[List[str], List[Tuple[str, str, str]]]:
    """PyG-style ``(node_types, edge_types)`` metadata tuple for the full schema."""
    return list(NODE_TYPES), list(EDGE_TYPES)


def validate_feature_names(names: Sequence[str], node_type: str) -> None:
    """Raise if ``names`` doesn't match this node type's declared columns."""
    expected = FEATURE_NAMES[node_type]
    if list(names) != expected:
        raise ValueError(
            f"feature names for '{node_type}' do not match schema: "
            f"got {list(names)!r}, expected {expected!r}"
        )
