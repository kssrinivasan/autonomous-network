# Temporal Graph Attention Network (TGAT) Example

This directory contains a minimal, self-contained example demonstrating how a **Temporal Graph Attention Network (TGAT)** can be used for failure pinpointing in a network topology.

## Overview

Unlike a standard Graph Convolutional Network (GCN) which looks at a single static snapshot, a TGAT processes a *sequence* of snapshots over time. This temporal context is crucial for detecting slow-burning issues, such as memory leaks or gradually drifting states, which might appear perfectly normal in any single isolated snapshot but are highly anomalous when viewed as a trajectory.

### Files

*   **`simple_tgat_pinpointing.py`**: The core example script. It builds a synthetic Hub-and-Spoke topology, generates normal sequences, trains a TGAT Autoencoder, and then injects a "Memory Leak" fault to demonstrate the detection.
*   **`maths_walkthrough.md`**: A detailed breakdown of the mathematical operations inside the GAT (Graph Attention) layer and the GRU (Gated Recurrent Unit) temporal layer.

## How to Run

1.  Create a virtual environment (optional but recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```
2.  Install dependencies:
    ```bash
    pip install tensorflow numpy
    ```
3.  Run the script:
    ```bash
    python simple_tgat_pinpointing.py
    ```

## Architecture

The TGAT Autoencoder in this example consists of three main stages:

1.  **Spatial Encoder (GAT)**: At each individual time step $t$, a Graph Attention layer is applied to aggregate features from neighboring nodes. The attention mechanism (`LeakyReLU(a^T [Wh_i || Wh_j])`) allows nodes to dynamically weigh different neighbors based on their current features.
2.  **Temporal Encoder (GRU)**: The spatially-aggregated sequences for each node are then passed through a Gated Recurrent Unit (GRU). The GRU learns the normal expected *trajectory* of features over time.
3.  **Decoder**: The final hidden state of the GRU is passed through a Dense layer to reconstruct the expected node features at the final time step.

## The Simulated Anomaly

The script simulates a **Memory Leak on PE2**. Over a sequence of 5 snapshots, PE2's memory gradually increases: `0.30 → 0.45 → 0.60 → 0.75 → 0.85`.

While a memory value of `0.85` might occasionally happen organically and might not trigger a static GCN anomaly threshold, the steady upward trajectory breaks the temporal patterns learned by the GRU. Consequently, the TGAT accurately flags the final step as highly anomalous, pinpointing the drift.
