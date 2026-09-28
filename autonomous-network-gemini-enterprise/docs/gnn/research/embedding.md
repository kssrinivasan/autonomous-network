# THGAT Embeddings Integration Walkthrough

We've successfully updated the architecture to generate and store GNN embeddings natively within the network topology environment.

## What Was Done

1. **Updated Spanner Schema (`gcp/spanner.tpl.yaml`)**
   - Added a new `NodeEmbedding` table to logically separate machine-generated features from physical configuration.
   - Associated the `NodeEmbedding` table with the property graph (`networkGraph`).
   - Added `CurrentNodeEmbedding` view.
   - Created `RouterHasEmbedding` and `InterfaceHasEmbedding` edge views in the graph, making it incredibly easy to traverse from a device to its latest embedding.

2. **Added Generation Logic (`domains/transport/gnn/src/serve.py`)**
   - The `/inference` endpoint natively supports embedding generation.
   - When triggered, it fetches the latest snapshot from Spanner, runs it through the THGAT model, extracts the hidden embeddings from the model's `state_dict`, and seamlessly commits them back to `NodeEmbedding` in a bulk mutation, alongside returning anomaly results.

## How to Trigger Embedding Generation

Since the logic is built into `serve.py`, you can hit the `/inference` endpoint on the running container or locally. Assuming the `serve.py` web server is running on `localhost:8080`:

```bash
curl -X POST http://localhost:8080/inference
```

This will run the inference logic, fetch the snapshot, compute and write bindings to Spanner, and return the inferences via JSON as normal.

## Example Spanner Graph Query

GQL can be used directly on the `networkGraph` to retrieve a device alongside its newly-generated topological embedding. Since we added the `RouterHasEmbedding` edge, traversing the graph is simple.

**Goal:** Find all PE Routers and their latest embeddings.

```gql
GRAPH networkGraph
MATCH (router:NetworkNode {kind: 'PhysicalRouter'}) -[:RouterHasEmbedding]-> (embedding:NetworkNode {kind: 'NodeEmbedding'})
WHERE router.role = 'PE' 
RETURN 
    router.name AS router_name, 
    embedding.embedding AS router_embedding
```

**Goal:** Find an interface and its embedding.

```gql
GRAPH networkGraph
MATCH (interface:NetworkNode {kind: 'PhysicalInterface'}) -[:InterfaceHasEmbedding]-> (embedding:NetworkNode {kind: 'NodeEmbedding'})
WHERE interface.name = 'eth0' 
RETURN 
    interface.name AS interface_name, 
    embedding.embedding AS interface_embedding
```

**Goal:** Aggregate embeddings over the past 24 hours for a specific device to calculate its average state.

Since embeddings are stored historically as arrays, we can use Spanner's built-in array unnesting and aggregation to find the average embedding vector over a period of time.

```sql
SELECT
    router_name,
    ARRAY(
        SELECT AVG(val)
        FROM UNNEST(all_embeddings) AS val WITH OFFSET AS idx
        GROUP BY idx
        ORDER BY idx
    ) AS avg_embedding
FROM (
    SELECT 
        router_name,
        ARRAY_CONCAT_AGG(router_embedding) AS all_embeddings
    FROM GRAPH_TABLE(
        networkGraph
        MATCH (router:NetworkNode {kind: 'PhysicalRouter'}) -[:RouterHasEmbedding]-> (embedding:NetworkNode {kind: 'NodeEmbedding'})
        WHERE router.name = 'ce-router-1' 
          AND embedding.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        COLUMNS (router.name AS router_name, embedding.embedding AS router_embedding)
    )
    GROUP BY router_name
)
```
