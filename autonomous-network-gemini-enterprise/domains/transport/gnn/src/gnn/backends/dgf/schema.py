"""schema.py — ``gnn.core.schema`` expressed as a DGF ``GraphSchema``.

Two structural differences from PyG have to be reconciled here, and both are
easy to get subtly wrong.

**Edge sets are named, not triples.** PyG keys a relation by
``(source, relation, target)``, so a relation and its inverse are two separate
edge types with separate weights. DGF keys by name only — source and target
live in ``EdgeSchema`` — and expresses direction at message-passing time via a
plan entry ``(edge_set_name, is_reversed)``. So our 11 PyG edge types collapse
into 7 DGF edge sets, and the plan re-expands them back to 11 message
directions:

    PyG                                    DGF edge set      plan entry
    (router,    has_interface,  interface) has_interface     (…, False)
    (interface, interface_of,   router)    has_interface     (…, True)
    (interface, connected_to,   interface) connected_to      (…, False)
    (router,    peer_of,        router)    peer_of           (…, False)
    (router,    member_of,      vpn)       member_of         (…, False)
    (vpn,       has_member,     router)    member_of         (…, True)
    (flow, originates_from,     interface) originates_from   (…, False)
    (interface, source_of,      flow)      originates_from   (…, True)
    (flow,      terminates_at,  interface) terminates_at     (…, False)
    (interface, destination_of, flow)      terminates_at     (…, True)
    (flow,      runs_over,      vpn)       runs_over         (…, False)

``connected_to`` and ``peer_of`` get **one** plan entry each, not two, even
though they are symmetric: the Spanner reader already emits both directions as
separate rows, so adding a reversed plan entry would pass every message twice.

**Features are packed, not named.** DGF's convention is one named feature per
column so ``auto_normalize`` can assign a normalizer per column. We deliberately
do not use DGF normalization — the A/B comparison holds the sklearn
``StandardScaler`` fixed across backends — so a single packed ``[N, F]`` float
feature is both simpler and exactly the tensor PyG reconstructs, which keeps the
reconstruction target identical between the two. If DGF-native normalization is
ever wanted, this is the decision to revisit: it would mean declaring all 17
router columns individually.
"""

from typing import Dict, List, Tuple

from gnn.core.schema import EDGE_TYPES, FEATURE_DIMS, NODE_TYPES

#: The single packed feature every node set carries.
FEATURE_KEY = "features"

#: The uniform-width feature the message-passing layers read and write.
EMBEDDING_KEY = "embedding"

#: DGF edge set name → (source node set, target node set).
EDGE_SETS: Dict[str, Tuple[str, str]] = {
    "has_interface":   ("router", "interface"),
    "connected_to":    ("interface", "interface"),
    "peer_of":         ("router", "router"),
    "member_of":       ("router", "vpn"),
    "originates_from": ("flow", "interface"),
    "terminates_at":   ("flow", "interface"),
    "runs_over":       ("flow", "vpn"),
}

#: PyG edge-type triple → (DGF edge set name, is_reversed).
#: Reversed entries are the PyG inverse relations; they contribute adjacency to
#: the forward edge set and only a plan entry of their own.
EDGE_TYPE_TO_SET: Dict[Tuple[str, str, str], Tuple[str, bool]] = {
    ("router",    "has_interface",   "interface"): ("has_interface", False),
    ("interface", "interface_of",    "router"):    ("has_interface", True),
    ("interface", "connected_to",    "interface"): ("connected_to", False),
    ("router",    "peer_of",         "router"):    ("peer_of", False),
    ("router",    "member_of",       "vpn"):       ("member_of", False),
    ("vpn",       "has_member",      "router"):    ("member_of", True),
    ("flow",      "originates_from", "interface"): ("originates_from", False),
    ("interface", "source_of",       "flow"):      ("originates_from", True),
    ("flow",      "terminates_at",   "interface"): ("terminates_at", False),
    ("interface", "destination_of",  "flow"):      ("terminates_at", True),
    ("flow",      "runs_over",       "vpn"):       ("runs_over", False),
}

#: Forward edge types whose adjacency defines each edge set. The inverse PyG
#: types are redundant — DGF derives the reverse direction from the plan — so
#: only these contribute rows.
FORWARD_EDGE_TYPES: List[Tuple[str, str, str]] = [
    et for et, (_, reversed_) in EDGE_TYPE_TO_SET.items() if not reversed_
]

#: Message-passing plan: one entry per PyG edge type, so both backends pass
#: exactly the same 11 message flows.
MESSAGE_PLAN: List[Tuple[str, bool]] = [
    EDGE_TYPE_TO_SET[et] for et in EDGE_TYPES
]


def _consistency_check() -> None:
    """Guard the two tables against drifting out of sync with core.schema."""
    missing = [et for et in EDGE_TYPES if et not in EDGE_TYPE_TO_SET]
    if missing:
        raise AssertionError(
            f"EDGE_TYPE_TO_SET does not cover {missing} — a relation was added "
            "to gnn.core.schema without being mapped to a DGF edge set."
        )
    for name, (src, dst) in EDGE_SETS.items():
        for et, (set_name, reversed_) in EDGE_TYPE_TO_SET.items():
            if set_name != name:
                continue
            expect = (dst, src) if reversed_ else (src, dst)
            if (et[0], et[2]) != expect:
                raise AssertionError(
                    f"edge type {et} maps to set '{name}' "
                    f"({src}→{dst}, reversed={reversed_}) but its endpoints are "
                    f"{et[0]}→{et[2]}"
                )


_consistency_check()


def build_graph_schema():
    """The raw-feature ``GraphSchema``: one packed float feature per node set."""
    from dgf.data import (EdgeSchema, FeatureFormat, FeatureSchema,
                          FeatureSemantic, GraphSchema, NodeSchema)

    return GraphSchema(
        node_sets={
            node_type: NodeSchema(features={
                FEATURE_KEY: FeatureSchema(
                    format=FeatureFormat.FLOAT_32,
                    semantic=FeatureSemantic.NUMERICAL,
                    shape=(FEATURE_DIMS[node_type],),
                ),
            })
            for node_type in NODE_TYPES
        },
        edge_sets={
            name: EdgeSchema(source=src, target=dst)
            for name, (src, dst) in EDGE_SETS.items()
        },
    )


def build_embedded_schema(dims: int):
    """The post-encoder schema the message-passing layers are built against.

    The tutorial gets this from ``EmbedGraphConfig.output_schema``. We build our
    own encoder — a per-node-type ``Dense``, mirroring the PyG model's
    ``lin_dict`` exactly so the two architectures start from the same projection
    — so the matching schema is constructed here instead.
    """
    from dgf.data import (EdgeSchema, FeatureFormat, FeatureSchema,
                          FeatureSemantic, GraphSchema, NodeSchema)

    return GraphSchema(
        node_sets={
            node_type: NodeSchema(features={
                EMBEDDING_KEY: FeatureSchema(
                    format=FeatureFormat.FLOAT_32,
                    semantic=FeatureSemantic.EMBEDDING,
                    shape=(dims,),
                ),
            })
            for node_type in NODE_TYPES
        },
        edge_sets={
            name: EdgeSchema(source=src, target=dst)
            for name, (src, dst) in EDGE_SETS.items()
        },
    )


__all__ = [
    "FEATURE_KEY", "EMBEDDING_KEY", "EDGE_SETS", "EDGE_TYPE_TO_SET",
    "FORWARD_EDGE_TYPES", "MESSAGE_PLAN",
    "build_graph_schema", "build_embedded_schema",
]
