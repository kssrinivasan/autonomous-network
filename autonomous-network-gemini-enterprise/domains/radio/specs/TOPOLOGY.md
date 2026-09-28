# Radio Domain Topology API (GQL)

Graph Query Language (GQL) API for traversing radio access network topology using Spanner property graphs.

---

## Overview

**Purpose:** Execute graph queries to discover relationships between cell sites and UEs.

**Spanner Property Graph:** `radioGraph`

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/radio`

---

## Property Graph Schema

### Node Types

**CellSite**
- Properties: `id`, `name`, `container_name`, `gnb_id`, `nci`, `mcc`, `mnc`, `tac`, `phase`, `valid_start_ts`, `valid_end_ts`
- Labels: `CellSite`

**UE**
- Properties: `id`, `cellsite_id`, `imsi`, `apn`, `sst`, `sd`, `valid_start_ts`, `valid_end_ts`
- Labels: `UE`

### Edge Types

| Edge Label | Source Node | Target Node | Description |
|------------|-------------|-------------|-------------|
| `ServesUE` | CellSite | UE | Cell site serves UE |

---

## Common Query Patterns

### 1. Find All UEs for a Cell Site

```gql
GRAPH radioGraph
MATCH (cell:CellSite {name: $cellsite_name})-[:ServesUE]->(ue:UE)
WHERE cell.valid_end_ts IS NULL
  AND ue.valid_end_ts IS NULL
RETURN ue.imsi AS imsi,
       ue.apn AS apn,
       ue.sst AS sst,
       ue.sd AS sd
ORDER BY ue.imsi
```

### 2. Find Cell Sites by TAC

```gql
GRAPH radioGraph
MATCH (cell:CellSite {tac: $tac})
WHERE cell.valid_end_ts IS NULL
RETURN cell.name AS cellsite_name,
       cell.gnb_id AS gnb_id,
       cell.mcc AS mcc,
       cell.mnc AS mnc
ORDER BY cell.name
```

### 3. Count UEs per Cell Site

```gql
GRAPH radioGraph
MATCH (cell:CellSite)-[:ServesUE]->(ue:UE)
WHERE cell.valid_end_ts IS NULL
  AND ue.valid_end_ts IS NULL
RETURN cell.name AS cellsite,
       COUNT(ue) AS ue_count
GROUP BY cell.name
ORDER BY ue_count DESC
```

---

## Complete Example

```bash
POST /tmf-api/resourceInventoryManagement/v4/radio/graph/query
Content-Type: application/json

{
  "gql": "GRAPH radioGraph MATCH (cell:CellSite)-[:ServesUE]->(ue:UE) WHERE cell.tac = $tac AND cell.valid_end_ts IS NULL AND ue.valid_end_ts IS NULL RETURN cell.name, COUNT(ue) AS ue_count GROUP BY cell.name",
  "parameters": {
    "tac": 1
  }
}

Response:
{
  "results": [
    {"cell.name": "cellsite-gnb-01", "ue_count": 10},
    {"cell.name": "cellsite-gnb-02", "ue_count": 8}
  ],
  "totalCount": 2
}
```
