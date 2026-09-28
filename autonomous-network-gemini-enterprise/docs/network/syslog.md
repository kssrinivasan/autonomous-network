# VyOS Syslog

VyOS uses the standard **syslog** facility (via `rsyslog`) to record system and routing-protocol events. Logs are structured around the classic syslog model: each message carries a **facility** (e.g. `kern`, `daemon`, `local7`) and a **severity level** (`debug` → `info` → `notice` → `warning` → `err` → `crit` → `alert` → `emerg`). VyOS extends this with per-daemon configuration so that individual routing daemons (FRR's `bgpd`, `ospfd`, `pim6d`, etc.) can each write to their own log targets.

In our telco lab, every VyOS router (`pe1`–`pe4`, `ce1-red`–`ce4-red`) is configured to forward syslog to a **Google Cloud Logging** sink. The log collector service ([`services/logcollector`](../../services/logcollector/)) reads those entries, enriches them with GCE instance metadata, and writes them to **Spanner** alongside a semantic embedding vector. This gives the GNN and AI agents a queryable, vector-searchable history of network events that can be correlated with KPI metrics and topology state for root-cause analysis.

## Sample Data Summary

The file [`sample_syslog.csv`](./sample_syslog.csv) contains **114 syslog entries** captured from VyOS network devices over a ~9-second window on **2026-05-20 between 07:41:30Z and 07:41:39Z**. Each row includes a unique ID, timestamp, severity, source (device + daemon), human-readable message, full JSON payload, and a vector embedding.

---

## CSV Format

| Column | Description |
|--------|-------------|
| `id` | Unique log entry identifier (e.g. `10dir5kfh2cix1`) |
| `timestamp` | ISO 8601 timestamp with nanosecond precision |
| `severity` | Log severity: `ERROR`, `WARNING`, or `NOTICE` |
| `SOURCE` | Composite source in the form `vyos:<host>:<daemon>` (e.g. `vyos:pe4:bgpd`) |
| `message` | Short human-readable message: `<daemon>: <body>` |
| `content` | Full JSON payload from Google Cloud Logging (includes host, facility, priority, process ID, VRF, GCE instance metadata, etc.) |
| `embedding` | High-dimensional float vector (semantic embedding of the message) |

### Example Row

```
id:        10dir5kfh2cix1
timestamp: 2026-05-20T07:41:39.80496037Z
severity:  ERROR
SOURCE:    vyos:ce3-red:bgpd
message:   bgpd: [EC 33554455] 10.75.75.1 [Error] bgp_read_packet error: Connection reset by peer
content:   {"insertId":"10dir5kfh2cix1","jsonPayload":{"app_name":"bgpd","facility":"3: system daemons",
           "host":"ce3-red","msg_body":"[EC 33554455] 10.75.75.1 [Error] bgp_read_packet error: Connection reset by peer",
           "pri":"27","proc_id":"561","source":"vyos",...},"severity":"ERROR",...}
embedding: [-0.0216, 0.0112, ...]
```

## Severity Breakdown

| Severity | Count |
|----------|-------|
| ERROR    | 85    |
| WARNING  | 21    |
| NOTICE   | 8     |

## Devices and Daemons

Logs originate from **VyOS** routers running on GCE (`networkvm`, zone `europe-west1-c`, project `agents-1234`). The `SOURCE` field identifies the device and the routing daemon that generated the log.

**Devices seen:** `pe1`, `pe2`, `pe3`, `pe4` (Provider Edge routers), `ce1-red`, `ce2-red`, `ce3-red`, `ce4-red` (Customer Edge routers in the "red" VRF).

**Daemons seen:** `bgpd`, `pim6d`, `ospfd`, `ospf6d`, `ldpd`, `isisd`, `nhrpd`, `fabricd`, `ripd`, `ripngd`, `pathd`, `staticd`, `babeld`, `bfdd`, `zebra`, `commit`

---

## Message Types

The sample captures a network event — likely a VRF/interface reconfiguration — and its cascading effects across multiple routing protocols. There are four distinct categories of message:

### 1. BGP Session Drop (ERROR)
Multiple CE routers lost their BGP sessions to their PE peers simultaneously.

```
[ERROR] ce1-red/bgpd: [EC 33554455] 10.55.55.1 [Error] bgp_read_packet error: Connection reset by peer
[ERROR] ce2-red/bgpd: [EC 33554455] 10.65.65.1 [Error] bgp_read_packet error: Connection reset by peer
[ERROR] ce3-red/bgpd: [EC 33554455] 10.75.75.1 [Error] bgp_read_packet error: Connection reset by peer
[ERROR] ce4-red/bgpd: [EC 33554455] 10.85.85.1 [Error] bgp_read_packet error: Connection reset by peer
```

### 2. Configuration Commit (NOTICE)
All four PE routers (`pe1`–`pe4`) logged a successful configuration change by `root`, suggesting a deliberate reconfiguration triggered the event.

```
[NOTICE] pe1/commit: Successful change to active configuration by user root on unknown
[NOTICE] pe4/commit: Successful change to active configuration by user root on unknown
```

### 3. Interface/VRF State Errors (ERROR)
Immediately after the commit, every routing daemon on `pe4` (and `pe1`) reported that the interface `RED_MESH` could not be found in VRF 7. This affected all protocol daemons simultaneously, indicating the VRF or interface was removed/renamed by the configuration change.

```
[ERROR] pe4/ospfd:   [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/ldpd:    [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/isisd:   [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/ospf6d:  [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/nhrpd:   [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/fabricd: [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/ripd:    [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/pathd:   [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/staticd: [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/babeld:  [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/bfdd:    [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
[ERROR] pe4/pim6d:   [EC 100663301] INTERFACE_STATE: Cannot find IF RED_MESH in VRF 7
```

### 4. PIM6 / Interface Teardown Warnings (WARNING)
PIM6 daemons on `pe1` and `pe4` logged warnings about interface teardown and failed multicast sends, consistent with the interface being removed mid-operation.

```
[WARNING] pe4/pim6d:  pim_if_del_vif: vif_index=0 < 1 on interface pim6reg300 ifindex=8
[ERROR]   pe4/pim6d:  [EC 100663299] sendmsg failed: source: :: Dest: ff02::d ifindex: 8: Bad file descriptor (9)
[WARNING] pe4/ospf6d: Zebra: got delete of eth3.408, but interface is still up
[WARNING] pe1/ospf6d: Zebra: got delete of eth3.405, but interface is still up
[WARNING] pe1/ospf6d: Zebra: got delete of pim6reg300, but interface is still up
[ERROR]   pe1/zebra:  [EC 4043309093] netlink-cmd (NS 0) error: Invalid argument, type=RTM_SETLINK(19)
```

---

## Interpretation

The log sequence tells a coherent story:

1. A **configuration commit** was applied to all PE routers (`pe1`–`pe4`) at ~07:41:33–35Z.
2. The commit removed or renamed the `RED_MESH` interface from VRF 7, causing **every routing daemon** on the affected PEs to immediately report an interface-not-found error.
3. The loss of the VRF interface caused the **BGP sessions** between the PE and CE routers in the "red" VRF to drop (connection reset), logged ~07:41:37–39Z.
4. **PIM6 and OSPFv3** daemons also logged teardown warnings as the multicast and link-state adjacencies collapsed.
