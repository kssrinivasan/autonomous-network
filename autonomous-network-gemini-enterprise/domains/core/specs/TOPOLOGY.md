# Core Domain Topology API (GQL)

Graph Query Language (GQL) API for traversing 5G core network topology using Spanner property graphs.

---

## Overview

**Purpose:** Execute graph queries to discover relationships between 5G cores and network functions.

**Spanner Property Graph:** `coreGraph`

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/core`

---

## Property Graph Schema

### Node Types

**FiveGCore**
- Properties: `id`, `name`, `phase`, `mongodb_uri`, `valid_start_ts`, `valid_end_ts`
- Labels: `FiveGCore`

**NetworkFunction**
- Properties: `id`, `name`, `nf_type`, `fivegcore_id`, `container_name`, `phase`, `ip_address`, `valid_start_ts`, `valid_end_ts`
- Labels: `NetworkFunction`

### Edge Types

| Edge Label | Source Node | Target Node | Description |
|------------|-------------|-------------|-------------|
| `ManagesNF` | FiveGCore | NetworkFunction | Core manages network function |

---

## Common Query Patterns

### 1. Find All Network Functions for a Core

```gql
GRAPH coreGraph
MATCH (core:FiveGCore {name: $core_name})-[:ManagesNF]->(nf:NetworkFunction)
WHERE core.valid_end_ts IS NULL
  AND nf.valid_end_ts IS NULL
RETURN nf.name AS nf_name,
       nf.nf_type AS nf_type,
       nf.phase AS phase,
       nf.ip_address AS ip_address
ORDER BY nf.nf_type, nf.name
```

### 2. Find All AMFs Across All Cores

```gql
GRAPH coreGraph
MATCH (core:FiveGCore)-[:ManagesNF]->(amf:NetworkFunction {nf_type: 'AMF'})
WHERE core.valid_end_ts IS NULL
  AND amf.valid_end_ts IS NULL
RETURN core.name AS core_name,
       amf.name AS amf_name,
       amf.phase AS phase
```

### 3. Count Network Functions by Type

```gql
GRAPH coreGraph
MATCH (core:FiveGCore {name: $core_name})-[:ManagesNF]->(nf:NetworkFunction)
WHERE core.valid_end_ts IS NULL
  AND nf.valid_end_ts IS NULL
RETURN nf.nf_type AS nf_type,
       COUNT(*) AS count
GROUP BY nf.nf_type
ORDER BY nf.nf_type
```
