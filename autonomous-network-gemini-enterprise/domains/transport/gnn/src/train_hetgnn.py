"""train_hetgnn.py — Vertex AI training entrypoint.

Thin wrapper: reads configuration from the environment, sources snapshots
(GCS when the KFP pipeline pre-fetched them, live Spanner otherwise), delegates
to ``gnn.train.train_from_snapshots``, and uploads the resulting bundle.

Which model gets trained is a config choice, not a code path — set
``MODEL_VARIANT`` to ``pyg.sage`` (default, the incumbent), ``pyg.gat``, or
``dgf.hetconv``. Everything else about the run is identical, which is what
makes the comparison between them meaningful.
"""

import json
import logging
import os
import pathlib
import pickle
import sys

from google.cloud import storage
from tqdm import tqdm

from gnn.backends.base import TrainConfig
from gnn.core import split
from gnn.data import SpannerDataset
from gnn.train import train_from_snapshots
from gnn.train.artifacts import BUNDLE_FILES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Runtime configuration ────────────────────────────────────────────────────
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "transporttopology-db")
SERVICE_DATABASE = os.getenv("SERVICE_DATABASE", "servicetopology-db")
INFRA_DATABASE   = os.getenv("INFRA_DATABASE",   "infratopology-db")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")
INTERVAL_MINUTES = float(os.getenv("INTERVAL_MINUTES", "0.5"))
TRAINING_SNAPSHOTS = int(os.getenv("TRAINING_SNAPSHOTS", "100"))

# Vertex AI env vars — set by the KFP training component.
SNAPSHOTS_GCS_PATH = os.getenv("SNAPSHOTS_GCS_PATH", "")
SCALERS_GCS_PATH = os.getenv("SCALERS_GCS_PATH", "")
AIP_MODEL_DIR = os.getenv("AIP_MODEL_DIR", "")

# ── Model configuration ──────────────────────────────────────────────────────
MODEL_VARIANT = os.getenv("MODEL_VARIANT", "pyg.sage")
HIDDEN_CHANNELS = int(os.getenv("HIDDEN_CHANNELS", "64"))
NUM_LAYERS = int(os.getenv("NUM_LAYERS", "2"))
DROPOUT = float(os.getenv("DROPOUT", "0.3"))
EPOCHS = int(os.getenv("EPOCHS", "50"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.001"))

# Deterministic RNG seed. Same seed → same weight init and dropout masks → same
# trajectory. Set it to run a seed sweep without editing code; it does not
# reduce variance, only makes a given run repeatable.
#
# Default is STOCHASTIC. Pinned seeds proved brittle for this autoencoder:
# seed=42 produced a VPN average score of 43,000 at serve time, seed=1337 a
# val_loss of 2.42 that trips the pipeline quality gate. Stochastic init plus
# the quality gate is more robust than picking a seed and hoping.
_raw_seed = os.getenv("RANDOM_SEED")
RANDOM_SEED = int(_raw_seed) if _raw_seed else None

# GAT-only: attention heads. Ignored by other architectures.
_raw_heads = os.getenv("ATTENTION_HEADS")


def build_config() -> TrainConfig:
    """Assemble a ``TrainConfig`` from the environment."""
    arch_kwargs = {}
    if _raw_heads and MODEL_VARIANT.endswith(".gat"):
        arch_kwargs["heads"] = int(_raw_heads)
    return TrainConfig(
        hidden_channels=HIDDEN_CHANNELS,
        num_layers=NUM_LAYERS,
        dropout=DROPOUT,
        epochs=EPOCHS,
        learning_rate=LEARNING_RATE,
        seed=RANDOM_SEED,
        arch_kwargs=arch_kwargs,
    )


def load_snapshots_from_gcs(gcs_path: str) -> list:
    """Download serialised snapshot dicts written by the ingest KFP component."""
    client = storage.Client()
    bucket_name = gcs_path.replace("gs://", "").split("/")[0]
    prefix = "/".join(gcs_path.replace("gs://", "").split("/")[1:])
    bucket = client.bucket(bucket_name)
    blobs = sorted(
        (b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".pkl")),
        key=lambda b: b.name,
    )
    snapshots = [pickle.loads(b.download_as_bytes()) for b in blobs]
    logger.info("Loaded %d snapshots from %s", len(snapshots), gcs_path)
    return snapshots


def fetch_snapshots_from_spanner() -> list:
    """Pull snapshots live, compute temporal features, and dump JSON copies."""
    dataset = SpannerDataset(
        instance_id=SPANNER_INSTANCE,
        database_id=SPANNER_DATABASE,
        service_database_id=SERVICE_DATABASE,
        infra_database_id=INFRA_DATABASE,
        num_snapshots=TRAINING_SNAPSHOTS,
        interval_minutes=INTERVAL_MINUTES,
    )
    snapshots = []
    for ts in tqdm(dataset._get_timestamps(), desc="Fetching Snapshots"):
        try:
            snapshot = dataset.fetch_snapshot(ts)
            if snapshot["nodes"]:
                snapshots.append(snapshot)
        except Exception as exc:
            logger.error("Error fetching snapshot at %s: %s", ts, exc)

    # Gradient / delta features are finite differences across consecutive
    # snapshots, so they can only be computed once the whole series is present.
    SpannerDataset.compute_temporal_features(
        snapshots, interval_seconds=INTERVAL_MINUTES * 60
    )
    logger.info("Temporal features computed across snapshot sequence")

    snap_dir = pathlib.Path("snapshots")
    snap_dir.mkdir(exist_ok=True)
    for i, snap in enumerate(snapshots):
        (snap_dir / f"snapshot_{i:04d}.json").write_text(json.dumps(snap, indent=2))
    logger.info("Wrote %d snapshot JSON files to %s/", len(snapshots), snap_dir)
    return snapshots


def upload_blob(bucket_name: str, source_file_name: str, destination_blob_name: str) -> None:
    try:
        storage.Client().bucket(bucket_name).blob(destination_blob_name) \
            .upload_from_filename(source_file_name)
        logger.info("File %s uploaded to %s.", source_file_name, destination_blob_name)
    except Exception as exc:
        logger.error("Failed to upload %s to GCS: %s", source_file_name, exc)


def upload_bundle(output_dir: pathlib.Path) -> None:
    """Copy the artefact bundle to GCS and to ``AIP_MODEL_DIR``."""
    if GCS_BUCKET_NAME:
        for name in BUNDLE_FILES:
            upload_blob(GCS_BUCKET_NAME, str(output_dir / name), f"models/hetgnn/{name}")

    if AIP_MODEL_DIR:
        logger.info("Copying artefacts to Vertex AI model dir: %s", AIP_MODEL_DIR)
        bucket_name = AIP_MODEL_DIR.replace("gs://", "").split("/")[0]
        prefix = "/".join(AIP_MODEL_DIR.replace("gs://", "").split("/")[1:]).rstrip("/")
        for name in BUNDLE_FILES:
            upload_blob(bucket_name, str(output_dir / name), f"{prefix}/{name}")
        logger.info("Artefacts copied to %s", AIP_MODEL_DIR)


def run_training_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("HETGNN TRAINING SERVICE STARTED — variant=%s", MODEL_VARIANT)
    logger.info("Instance: %s, Database: %s", SPANNER_INSTANCE, SPANNER_DATABASE)
    logger.info("=" * 60)

    try:
        if SNAPSHOTS_GCS_PATH:
            logger.info("Loading pre-fetched snapshots from GCS: %s", SNAPSHOTS_GCS_PATH)
            snapshots = load_snapshots_from_gcs(SNAPSHOTS_GCS_PATH)
        else:
            logger.info("Fetching snapshots live from Spanner: %s/%s",
                        SPANNER_INSTANCE, SPANNER_DATABASE)
            snapshots = fetch_snapshots_from_spanner()

        if not snapshots:
            logger.error("Error: No data found. Exiting.")
            return

        output_dir = pathlib.Path(".")
        result, _, _ = train_from_snapshots(
            snapshots,
            output_dir=output_dir,
            variant=MODEL_VARIANT,
            config=build_config(),
            val_fraction=split.VALIDATION_SPLIT,
        )
        logger.info("Training complete — best val_loss=%.4f after %d epochs",
                    result.best_val_loss, result.epochs_run)

        upload_bundle(output_dir)

    except Exception as exc:
        logger.error("Training pipeline failed: %s", exc)
        import traceback

        traceback.print_exc()


# ── Backwards-compatible entrypoint ──────────────────────────────────────────
# tests/test_model_training.py and the local KFP runner call this directly.

def train_hetgnn_on_snapshots(
    snapshot_objects: list,
    output_dir: str = ".",
    hidden_channels: int = HIDDEN_CHANNELS,
    num_layers: int = NUM_LAYERS,
    dropout: float = DROPOUT,
    epochs_override: int = None,
    variant: str = None,
):
    """Deprecated shim for ``gnn.train.train_from_snapshots``.

    Returns ``(torch_model, best_val_loss, graph_builder, history)`` to match
    the pre-refactor signature.
    """
    config = build_config()
    config.hidden_channels = hidden_channels
    config.num_layers = num_layers
    config.dropout = dropout
    if epochs_override is not None:
        config.epochs = epochs_override

    result, normalizer, _ = train_from_snapshots(
        snapshot_objects,
        output_dir=pathlib.Path(output_dir),
        variant=variant or MODEL_VARIANT,
        config=config,
    )

    from gnn.backends.pyg.graph import GraphBuilder

    return (
        getattr(result.model, "model", result.model),
        result.best_val_loss,
        GraphBuilder(normalizer=normalizer),
        result.history,
    )


if __name__ == "__main__":
    run_training_pipeline()
