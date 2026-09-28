"""model/hetgnn.py — DEPRECATED compatibility shim.

``HetGNN`` moved to ``gnn.backends.pyg.model`` and is now architecture-agnostic
(``arch="sage"`` reproduces this module's behaviour exactly, and is the
default). Re-exported here for the compiled KFP components and existing test
scripts. Import from ``gnn.backends.pyg`` in new code.
"""

from gnn.backends.pyg.model import HetGNN  # noqa: F401  (re-export)

__all__ = ["HetGNN"]
