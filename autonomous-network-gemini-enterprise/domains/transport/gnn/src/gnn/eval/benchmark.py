"""benchmark.py — the labelled window set every variant is scored against.

The benchmark is the product. Without a frozen set of labelled windows, "A/B
test" degenerates into comparing validation losses across different loss
surfaces, which measures nothing. With one, every architecture question — and
every future scoring change — has an answer that is reproducible and arguable.

A benchmark directory looks like:

    benchmark/
      manifest.json          # window definitions and their labels
      snapshots/
        train/0000.json ...  # healthy history the model is fitted on
        healthy-a/0000.json  # held-out healthy windows: false-positive rate
        bgp-flap/0000.json   # fault windows: recall and time-to-detect

``manifest.json``::

    {
      "name": "transport-v1",
      "interval_minutes": 0.5,
      "train": "snapshots/train",
      "healthy": [{"name": "healthy-a", "path": "snapshots/healthy-a"}],
      "faults": [
        {"name": "bgp-flap", "path": "snapshots/bgp-flap",
         "scenario": "docs/scenarios/transport.md#bgp-flap",
         "onset_index": 4,
         "faulty_nodes": {"router": ["pe1"], "interface": ["pe1-eth0"]}}
      ]
    }

Fault labels come from two places, both of which know ground truth exactly:
``networkagents/chaos/`` (it injected the fault, so it knows which node and
when) and the scenarios in ``docs/scenarios/``. Labelling by hand from
dashboards is not worth doing — the label noise would exceed the effect size
being measured.

Until a real capture exists, ``synthetic_benchmark()`` builds a structurally
valid stand-in so the harness can be exercised end to end. It is a smoke test,
not evidence: never rank architectures on it.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import numpy as np

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"


@dataclass
class Window:
    """A contiguous run of snapshots with a label."""

    name: str
    snapshots: List[Dict]
    #: node_type → faulty node ids. Empty for healthy windows.
    faulty_nodes: Dict[str, Set[str]] = field(default_factory=dict)
    #: index of the first snapshot in which the fault is present
    onset_index: int = 0
    scenario: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        return not any(self.faulty_nodes.values())

    def faulty_ids(self, node_type: str) -> Set[str]:
        return set(self.faulty_nodes.get(node_type, ()))

    @property
    def all_faulty_ids(self) -> Set[str]:
        out: Set[str] = set()
        for ids in self.faulty_nodes.values():
            out |= set(ids)
        return out

    def __repr__(self) -> str:
        kind = "healthy" if self.is_healthy else f"fault({len(self.all_faulty_ids)} nodes)"
        return f"Window({self.name}, {len(self.snapshots)} snapshots, {kind})"


@dataclass
class Benchmark:
    """Training history plus held-out healthy and fault windows."""

    name: str
    train_snapshots: List[Dict]
    healthy: List[Window] = field(default_factory=list)
    faults: List[Window] = field(default_factory=list)
    interval_minutes: float = 0.5
    source: Optional[Path] = None

    def validate(self) -> List[str]:
        """Structural problems that would silently corrupt a result.

        Returned rather than raised so a caller can report them all at once;
        ``load_benchmark`` raises on anything found here, because a benchmark
        that quietly mislabels a window produces confident wrong answers.
        """
        problems: List[str] = []
        if not self.train_snapshots:
            problems.append("no training snapshots")
        if not self.faults:
            problems.append("no fault windows — recall cannot be measured")
        if not self.healthy:
            problems.append("no healthy windows — false-positive rate cannot be measured")

        for w in self.faults:
            if not w.all_faulty_ids:
                problems.append(f"fault window '{w.name}' labels no faulty nodes")
            if not 0 <= w.onset_index < len(w.snapshots):
                problems.append(
                    f"fault window '{w.name}' onset_index {w.onset_index} outside "
                    f"0..{len(w.snapshots) - 1}"
                )
            present = {n.get("id") for s in w.snapshots for n in s.get("nodes", [])}
            missing = w.all_faulty_ids - present
            if missing:
                problems.append(
                    f"fault window '{w.name}' labels nodes absent from its snapshots: "
                    f"{sorted(missing)[:5]}"
                )
        for w in self.healthy:
            if not w.is_healthy:
                problems.append(f"healthy window '{w.name}' carries fault labels")
        return problems

    def summary(self) -> str:
        return (
            f"{self.name}: {len(self.train_snapshots)} train snapshots, "
            f"{len(self.healthy)} healthy windows "
            f"({sum(len(w.snapshots) for w in self.healthy)} snapshots), "
            f"{len(self.faults)} fault windows "
            f"({sum(len(w.snapshots) for w in self.faults)} snapshots)"
        )


# ── Loading ───────────────────────────────────────────────────────────────────

def _load_snapshot_dir(directory: Path) -> List[Dict]:
    """Read ``*.json`` snapshots in filename order.

    Order is the time axis — time-to-detect is measured in snapshot indices —
    so filenames must sort chronologically. Zero-padded numbers do; bare
    integers do not, which is why the writer pads them.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"snapshot directory not found: {directory}")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no .json snapshots in {directory}")
    return [json.loads(p.read_text()) for p in paths]


def load_benchmark(root: Path) -> Benchmark:
    """Load a benchmark directory; raise if it is structurally unsound."""
    root = Path(root)
    manifest = json.loads((root / MANIFEST_FILENAME).read_text())

    def window(entry: Dict, faulty: bool) -> Window:
        raw = entry.get("faulty_nodes", {}) if faulty else {}
        return Window(
            name=entry["name"],
            snapshots=_load_snapshot_dir(root / entry["path"]),
            faulty_nodes={k: set(v) for k, v in raw.items()},
            onset_index=int(entry.get("onset_index", 0)),
            scenario=entry.get("scenario"),
        )

    benchmark = Benchmark(
        name=manifest.get("name", root.name),
        train_snapshots=_load_snapshot_dir(root / manifest["train"]),
        healthy=[window(e, False) for e in manifest.get("healthy", [])],
        faults=[window(e, True) for e in manifest.get("faults", [])],
        interval_minutes=float(manifest.get("interval_minutes", 0.5)),
        source=root,
    )

    problems = benchmark.validate()
    if problems:
        raise ValueError(
            f"benchmark at {root} is not usable:\n  - " + "\n  - ".join(problems)
        )
    logger.info("Loaded benchmark %s", benchmark.summary())
    return benchmark


def save_benchmark(benchmark: Benchmark, root: Path) -> Path:
    """Write a benchmark to disk in the layout ``load_benchmark`` expects."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def dump(snapshots: Sequence[Dict], rel: str) -> str:
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        for i, snap in enumerate(snapshots):
            (directory / f"{i:04d}.json").write_text(json.dumps(snap))
        return rel

    manifest = {
        "name": benchmark.name,
        "interval_minutes": benchmark.interval_minutes,
        "train": dump(benchmark.train_snapshots, "snapshots/train"),
        "healthy": [
            {"name": w.name, "path": dump(w.snapshots, f"snapshots/{w.name}")}
            for w in benchmark.healthy
        ],
        "faults": [
            {
                "name": w.name,
                "path": dump(w.snapshots, f"snapshots/{w.name}"),
                "scenario": w.scenario,
                "onset_index": w.onset_index,
                "faulty_nodes": {k: sorted(v) for k, v in w.faulty_nodes.items()},
            }
            for w in benchmark.faults
        ],
    }
    path = root / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2))
    logger.info("Wrote benchmark to %s", path)
    return path


# ── Synthetic stand-in ────────────────────────────────────────────────────────

_ROLES = ["P", "PE", "RR", "CE"]


def _snapshot(rng, index: int, n_routers: int, faulty: Optional[Dict[str, Set[str]]] = None,
              severity: float = 6.0) -> Dict:
    """One synthetic snapshot; faulty nodes get their metrics pushed off-manifold."""
    faulty = faulty or {}
    nodes: List[Dict] = []
    edges: List[Dict] = []

    for r in range(n_routers):
        rid = f"router-{r}"
        bad = rid in faulty.get("router", set())
        nodes.append({
            "id": rid, "type": "router", "hostname": rid, "role": _ROLES[r % 4],
            "state": 1.0,
            "cpu": float(rng.normal(90, 3) if bad else rng.normal(25, 4)),
            "mem": float(rng.normal(45, 5)),
            "ospf_num_routes": float(rng.normal(20 if bad else 120, 5)),
            "pfx_count_norm": float(rng.normal(0.5, 0.03)),
            "routes_fib": float(rng.normal(120, 6)),
            "bfd_peers": float(rng.integers(2, 5)),
            "ospf_neighbors": float(rng.integers(2, 5)),
            "ospf_adj": float(rng.integers(2, 5)),
            "vrf_count": float(rng.integers(1, 4)),
        })
        for i in range(3):
            iid = f"{rid}-eth{i}"
            ibad = iid in faulty.get("interface", set())
            nodes.append({
                "id": iid, "type": "interface", "name": f"eth{i}",
                "state": 0.0 if ibad else 1.0,
                "mtu_norm": 0.5,
                "rx_errs_rate": float(abs(rng.normal(severity if ibad else 0.01, 0.02))),
                "tx_errs_rate": float(abs(rng.normal(0.01, 0.02))),
                "rx_bytes_rate": float(rng.normal(0.05 if ibad else 0.5, 0.05)),
                "tx_bytes_rate": float(rng.normal(0.05 if ibad else 0.5, 0.05)),
                "rx_packets_rate": float(rng.normal(0.4, 0.05)),
                "tx_packets_rate": float(rng.normal(0.4, 0.05)),
                "rx_util": float(rng.normal(0.3, 0.04)),
                "tx_util": float(rng.normal(0.3, 0.04)),
                "carrier_state": 0.0 if ibad else 1.0,
                "carrier_changes": float(rng.integers(8, 20) if ibad else 0),
            })
            # The key is "relation", matching SpannerDataset.fetch_snapshot;
            # build_arrays ignores any edge without it, so a snapshot written
            # with "type" here yields a graph with no edges at all — which
            # trains and scores without complaint, and measures nothing.
            edges.append({"source": rid, "target": iid, "relation": "has_interface"})
            edges.append({"source": iid, "target": rid, "relation": "interface_of"})

    for v in range(2):
        vid = f"vpn-{v}"
        bad = vid in faulty.get("vpn", set())
        nodes.append({
            "id": vid, "type": "vpn", "state": 0.0 if bad else 1.0,
            "num_active_peers": float(rng.normal(1 if bad else 6, 0.5)),
            "total_prefixes": float(rng.normal(30 if bad else 240, 10)),
        })
        edges.append({"source": f"router-{v}", "target": vid, "relation": "member_of"})
        edges.append({"source": vid, "target": f"router-{v}", "relation": "has_member"})

    for f in range(3):
        fid = f"flow-{f}"
        bad = fid in faulty.get("flow", set())
        nodes.append({
            "id": fid, "type": "flow",
            "throughput_sent_bps": float(rng.normal(5e7, 4e6)),
            "throughput_recv_bps": float(rng.normal(2e6 if bad else 5e7, 4e6)),
            "latency_ms": float(rng.normal(180 if bad else 12, 3)),
            "jitter_ms": float(rng.normal(40 if bad else 1.5, 0.5)),
            "packet_loss_pct": float(rng.normal(35 if bad else 0.2, 0.3)),
            "is_running": 1.0,
            "is_bidirectional": float(f % 2),
        })
        src_if = f"router-{f % n_routers}-eth0"
        dst_if = f"router-{(f + 1) % n_routers}-eth0"
        edges.append({"source": fid, "target": src_if, "relation": "originates_from"})
        edges.append({"source": src_if, "target": fid, "relation": "source_of"})
        edges.append({"source": fid, "target": dst_if, "relation": "terminates_at"})
        edges.append({"source": dst_if, "target": fid, "relation": "destination_of"})
        edges.append({"source": fid, "target": f"vpn-{f % 2}", "relation": "runs_over"})

    # Symmetric relations are emitted in both directions, as the Spanner reader
    # does — a single PyG edge type only passes messages src → dst.
    for r in range(n_routers - 1):
        a, b = f"router-{r}", f"router-{r + 1}"
        edges.append({"source": a, "target": b, "relation": "peer_of"})
        edges.append({"source": b, "target": a, "relation": "peer_of"})
        ia, ib = f"{a}-eth1", f"{b}-eth2"
        edges.append({"source": ia, "target": ib, "relation": "connected_to"})
        edges.append({"source": ib, "target": ia, "relation": "connected_to"})

    return {"timestamp": f"2026-08-05T{index // 60:02d}:{index % 60:02d}:00Z",
            "nodes": nodes, "edges": edges}


def synthetic_benchmark(
    seed: int = 20260805,
    n_routers: int = 6,
    train_snapshots: int = 60,
    window_length: int = 12,
) -> Benchmark:
    """A structurally valid benchmark with injected faults, for smoke tests.

    The faults here are crude — a metric shoved several sigma off its healthy
    mean — so any working detector finds them. That makes this useful for
    proving the harness runs and useless for choosing an architecture. Replace
    it with a chaos-agent capture before drawing conclusions.
    """
    rng = np.random.default_rng(seed)

    train = [_snapshot(rng, i, n_routers) for i in range(train_snapshots)]
    healthy = [
        Window(name=f"healthy-{tag}",
               snapshots=[_snapshot(rng, i, n_routers) for i in range(window_length)])
        for tag in ("a", "b")
    ]

    fault_specs = [
        ("router-cpu-spike",  {"router": {"router-1"}}),
        ("interface-errors",  {"interface": {"router-2-eth1"}}),
        ("vpn-peer-loss",     {"vpn": {"vpn-0"}}),
        ("flow-degradation",  {"flow": {"flow-1"}}),
    ]
    onset = window_length // 3

    faults = []
    for name, faulty in fault_specs:
        snaps = [
            _snapshot(rng, i, n_routers, faulty=faulty if i >= onset else None)
            for i in range(window_length)
        ]
        faults.append(Window(name=name, snapshots=snaps, faulty_nodes=faulty,
                             onset_index=onset, scenario="synthetic"))

    benchmark = Benchmark(
        name="synthetic-smoke",
        train_snapshots=train,
        healthy=healthy,
        faults=faults,
    )
    problems = benchmark.validate()
    if problems:  # pragma: no cover — a bug in this generator, not in user data
        raise AssertionError(f"synthetic benchmark is malformed: {problems}")
    return benchmark


__all__ = [
    "Benchmark", "Window", "load_benchmark", "save_benchmark",
    "synthetic_benchmark", "MANIFEST_FILENAME",
]
