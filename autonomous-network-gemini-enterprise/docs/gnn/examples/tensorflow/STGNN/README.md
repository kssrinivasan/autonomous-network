# Spatio-Temporal Graph Neural Network (STGNN) Example

This directory contains a minimal, self-contained example demonstrating how a **Spatio-Temporal Graph Neural Network (STGNN)** can be used for failure pinpointing in a network topology.

## Overview

The STGNN represents the apex of the digital twin's anomaly detection capabilities by combining the strengths of two prior architectures:

1. **Heterogeneous Graphs (`HetGNN`)**: The ability to model different infrastructural layers (Routers, Interfaces, BGP Sessions, OSPF Adjacencies) simultaneously, each with their own unique features and message-passing logic.
2. **Temporal Sequences (`TGAT`)**: The ability to evaluate a rolling window of history (a trajectory) rather than just a single static snapshot, allowing the model to detect slow drifts, intermittent flapping, or memory leaks.

### Files

*   **`simple_stgnn_pinpointing.py`**: The core example script. It builds a synthetic Heterogeneous topology, generates normal sequential traffic, trains the STGNN Autoencoder, and then injects an intermittent "Flapping BGP Session" fault to demonstrate the detection.
*   **`maths_walkthrough.md`**: A detailed breakdown of the mathematical operations inside the Spatial message-passing layer and the Temporal GRU layer, calculating step-by-step how a heterogeneous sequence is scored.

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
    python simple_stgnn_pinpointing.py
    ```

## Architecture

The STGNN Autoencoder in this example processes data formatted as `[batch, T, N_type, F_type]`:

1.  **Heterogeneous Spatial Encoder**: At each individual time step $t$, the features of each node type are projected into a shared hidden space. Messages are then passed between different node types using specific Incidence Matrices (e.g., $P_{RI}$ for Router $\rightarrow$ Interface). 
2.  **Temporal Encoder (Typed GRUs)**: The spatially-aggregated sequences for each node are then grouped by type and passed through a dedicated Gated Recurrent Unit (GRU) for that specific component (e.g., `gru_router`, `gru_bgp`).
3.  **Typed Decoders**: The final hidden state of the GRU for each node type is passed through a dedicated Dense layer to reconstruct the expected component features at the final time step.

## The Simulated Anomaly

The script simulates a **Flapping BGP Session on `pe1_bgp`**. Over a sequence of 5 snapshots, the session's state alternates: `UP(1) → DOWN(0) → UP(1) → DOWN(0) → UP(1)`.

Notice that the final snapshot at $t=4$ is `UP(1)`, which is mathematically identical to a perfectly healthy baseline snapshot. **A purely static GCN or HetGNN model would evaluate this final snapshot and determine the network is 100% healthy, completely missing the fault.** 

However, the STGNN's temporal GRU recognizes the erratic trajectory leading up to that final step as highly anomalous, and flags `pe1_bgp` with a massive reconstruction error, successfully isolating the intermittent protocol fault.
