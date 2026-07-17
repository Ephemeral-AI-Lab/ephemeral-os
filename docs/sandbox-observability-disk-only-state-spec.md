# Sandbox observability disk-only state specification

Status: Draft
Scope: `sandbox-daemon`, `sandbox-manager`, `sandbox-observability-telemetry`,
`sandbox-observability-query`, `sandbox-provider-docker`, and `sandbox-console`

## Relationship to existing specifications

This is a new, additive specification. It does not replace or modify
[`sandbox-observability-resource-isolation-spec.md`](sandbox-observability-resource-isolation-spec.md).
The existing document defines the broader observability isolation architecture.
This document makes one narrower decision normative: retained observability
history is disk-only, while process memory contains no history and only bounded
request-lifetime state.

The corresponding live-test contract is
[`disk_only_state_live_spec.md`](../../ephemeral-sandbox-test/e2e/observability/disk_only_state_live_spec.md).

## 1. Decision

Observability state is split into two categories:

1. Historical state is persisted only in hard-capped files.
2. Transient state exists only during one sample, append, or query and has a
   fixed upper bound independent of stored history.

Neither the sandbox daemon nor the host manager may retain a telemetry history,
decoded series, response cache, append queue, or history index in memory.

“Memory-free” in this specification does not mean that a process has zero code,
stack, socket, configuration, or response memory. It means zero retained
telemetry-history records and zero history-sized allocations. All bounded
request storage must become unreachable when the response transport completes.

## 2. Incident and root cause

The investigated sandbox grew from approximately 5 MiB to approximately
120 MiB during one hour of observability polling. The evidence showed:

- approximately 11.6 MiB of primary and rotated NDJSON history;
- more than 56,000 records generated in repeated polling batches;
- `Reader::scan` reading complete files, retaining both parsed records and
  copied source lines, then sorting the complete history;
- query limits such as `last_n` being applied after full materialization;
- allocator arenas retaining freed pages after requests completed;
- transparent huge pages accounting for approximately 43 MiB of anonymous
  residency;
- read paths that emitted or refreshed telemetry, causing polling to grow the
  input that later polling had to read.

The no-op workspace sessions were intentional lifecycle state and were not the
source of the growth. The corrective architecture therefore removes the
read/write feedback loop and the history-sized allocation, rather than relying
on garbage collection, allocator trimming, reclaim, or periodic restart.

## 3. Normative terminology

### Historical state

Any sample, span, event, raw line, trace node, or decoded representation that
survives the operation that created or read it.

### Disk-only

The authoritative retained representation is a file with a predeclared hard
size. No complete or partial mirror is retained in a process collection.
Filesystem page cache is kernel-managed, reclaimable storage cache and is not
user-space history. Product code must not `mmap` observability stores.

### Idle daemon

A ready daemon with no active runtime mutation, execution, workspace lease, or
explicit daemon-owned observability request. Manager resource sampling does not
make the daemon active.

### Request-lifetime memory

Input, decode, result, and encode buffers owned by one operation. They may not
be moved into a process-lifetime object or reused as an unbounded capacity
cache after the operation completes.

## 4. Required invariants

| ID | Requirement |
|---|---|
| MEM-01 | The daemon retains zero resource samples, events, spans, traces, or raw telemetry lines between operations. |
| MEM-02 | The manager retains zero resource samples between sampling and read operations. |
| MEM-03 | Idle daemon observability performs zero allocation, collection, serialization, file read, or file write after warmup. |
| MEM-04 | Peak query-owned live heap is at most 1 MiB and does not increase with telemetry file size. |
| MEM-05 | Encoded responses are at most 256 KiB and 500 records, whichever is reached first. |
| MEM-06 | An input line buffer never exceeds 16 KiB, including malformed and newline-free input. |
| MEM-07 | No observability file is memory-mapped. |
| MEM-08 | No request-owned capacity survives response transmission. |
| MEM-09 | Daemon `AnonHugePages` and cgroup `anon_thp` remain zero. |
| DISK-01 | Daemon event history is at most 4 MiB per sandbox by default. |
| DISK-02 | Configured daemon event history is accepted only from 1 MiB through 16 MiB. |
| DISK-03 | The active and rotated event segments never exceed their total configured budget after an append. |
| DISK-04 | Manager resource history is one file of at most 64 KiB per live sandbox. |
| DISK-05 | Sandbox destruction removes its manager resource ring. Container removal removes its daemon event store. |
| DISK-06 | Migration never increases the pre-migration footprint and creates no third full-size copy. |
| READ-01 | Every observability read is side-effect-free. File length, allocated blocks, content, modification time, and sample count remain unchanged. |
| READ-02 | Fleet/status and manager resource polling never invoke a sandbox daemon. |
| FAIL-01 | Storage errors drop observability only; they never fail or retry a runtime operation. |

## 5. State-placement budget

| State | Owner | Retention | In-memory allowance | Hard bound |
|---|---|---|---|---|
| Runtime spans and events | Daemon | Two disk segments | Zero retained records | 4 MiB default; 16 MiB maximum |
| Resource samples | Manager | One fixed disk ring | Zero retained records | 64 KiB per live sandbox |
| Event encoder | Producer thread | None | One fixed buffer | 16 KiB |
| Event query | Request task | None | Transient result and transport data | 1 MiB live heap; 256 KiB encoded |
| Resource query | Manager request task | None | At most 512 decoded fixed records | 256 KiB encoded |
| Health counters | Owning process | Fixed atomics | Constant | Constant |

Process-lifetime observability objects may retain only paths, scalar limits,
locks, fixed counters, and observer identity. Collections whose size is a
function of elapsed time, sample count, event count, path count, or file size
are forbidden.

## 6. Target architecture

```text
Docker/cgroup metrics
        |
        v
manager sampler -- one sample --> fixed 64 KiB host ring
        ^                              |
        |                              v
resource query <---------------- bounded disk read

runtime mutation --> fixed encoder --> bounded active/rotated event files
                                            |
                                            v
telemetry query <------------------- bounded streaming fold
```

### 6.1 Manager-owned resource ring

The manager calls the existing provider resource-metrics port. The Docker
provider reads host cgroup metrics without entering or contacting the sandbox
daemon. One sample is written directly into the sandbox's fixed ring.

The ring is exactly 64 KiB including its header and uses fixed 64-byte records.
The header records magic, version, record size, capacity, next index, and valid
count. A record contains timestamp, validity bits, CPU, memory, PID, and I/O
counters.

The manager may hold the current sample while writing it and may decode at most
512 records while serving one response. It must not own `ResourceHistory`, a
per-sandbox `VecDeque`, a decoded-ring cache, or a cached resource response.
After the append or response finishes, no sample remains reachable from the
manager service graph.

Ring writes use positional I/O and update the header last. A torn newest record
may be discarded. Corruption returns a structured partial result and recreates
the ring without failing sandbox lifecycle.

### 6.2 Bounded daemon event store

The daemon keeps the current NDJSON format and two files: active and rotated.
`max_disk_bytes` applies to their sum. Each segment is limited to half the
total.

Every producer uses the same per-store cross-process lock. Under that lock it:

1. serializes one bounded record;
2. reads both segment lengths;
3. removes or replaces the older rotated segment when rotation is required;
4. rotates before the append would cross the active-segment limit;
5. appends the complete line once;
6. releases the lock without retaining the line.

After every successful append:

```text
active.len <= max_disk_bytes / 2
rotated.len <= max_disk_bytes / 2
active.len + rotated.len <= max_disk_bytes
```

Append-then-rotate is forbidden because it temporarily violates the hard cap.
There is no in-process event queue and no retry queue. A storage failure
increments one fixed-width drop counter and returns success to the runtime
operation.

### 6.3 Fixed event encoding

`Sink::append` serializes directly into a fixed 16 KiB writer. It must not call
`serde_json::to_vec`, clone the complete record, construct a second dynamic
`Value` tree, or retry with progressively smaller dynamic buffers.

If the record does not fit, the sink serializes one bounded truncation marker.
If the marker cannot fit, the record is dropped and counted. The trailing
newline is included in the 16 KiB limit.

### 6.4 Streaming query folds

The whole-history `Reader::scan` primitive is removed. Each public view owns a
specific streaming fold over rotated then active segments.

The input reader uses a reusable capped byte buffer. An oversized or
newline-free line is drained in fixed chunks without growing that buffer.
Invalid UTF-8, malformed JSON, and a partial crash tail are skipped.

Each view retains only:

- latest sample: the two newest matching records;
- latest root trace: one candidate trace id;
- events and raw: a bounded `VecDeque` applying `last_n` during the scan;
- trace: matching nodes only, stopped at both response limits;
- layer views: only their existing documented limits.

Filters and limits are applied before an operation response or
`serde_json::Value` is built. A request never sorts the complete store. Finding
`trace_id=last` may use two bounded streaming passes; it may not retain the
first pass.

### 6.5 Side-effect-free reads and polling

Successful RPC completion must not trigger observability collection. Snapshot
reads must not refresh resource samples. Observability reads must not trace
themselves into the store they are reading.

The console may poll manager lifecycle state and the manager-owned resource
route. It requests a daemon snapshot only after an activity revision changes,
while an execution is known active, after an explicitly requested refresh, or
after a stale-focus transition. Volatile timestamps and resource counters are
not activity fingerprints.

### 6.6 Anonymous memory and huge pages

The design must be correct without `malloc_trim`, allocator purge APIs, cgroup
reclaim, or daemon restart. Those mechanisms may be used only for diagnostics.

On Linux the daemon disables transparent huge pages for itself before worker
threads start. Failure is reported during startup and fails conformance. The
setting does not apply to sandbox workload processes.

## 7. Configuration

```yaml
observability:
  enabled: true
  max_disk_bytes: 4194304
  max_line_bytes: 16384
```

Validation rules:

- `max_disk_bytes`: 1 MiB through 16 MiB inclusive;
- `max_line_bytes`: 1 through 16 KiB inclusive;
- public response: fixed maximum 256 KiB and 500 records;
- host resource ring: fixed 64 KiB and not configurable.

`max_file_bytes` is accepted for one compatibility release as a deprecated
legacy per-segment value. It is converted to a total budget and clamped to the
16 MiB absolute maximum. New configuration and tests use `max_disk_bytes`.

## 8. Legacy migration

An existing store may already exceed the new limit. Startup and the first
post-upgrade append must handle it without loading it into memory.

Migration uses bounded streaming and never increases the legacy logical or
allocated footprint. It may delete the oldest segment before compaction and may
drop legacy telemetry when bounded compaction is not possible. Bounded storage
wins over historical completeness.

Migration must not create a third full-size segment. After its first successful
transition or append, the ordinary total-disk invariant applies. If migration
fails, runtime work continues and new telemetry is dropped and counted until
the store can be made safe.

## 9. Known implementation gaps at specification time

- `sandbox-observability-telemetry::Reader::scan` materializes complete files,
  duplicates source lines, and sorts the full history.
- event and raw views return complete vectors; `last_n` is applied later by the
  query application.
- `Sink::append` uses dynamic JSON vectors and has only a line limit.
- daemon sink construction does not enforce a strict total event-store budget.
- resource history is being moved to a manager-owned ring, but that work alone
  does not bound daemon event queries or event-file growth.
- process-level transparent huge-page disabling is not yet implemented.

The polling fix may land independently, but this specification is not complete
until the storage, reader, encoder, and memory gates all pass.

## 10. Deterministic test requirements

### Sink

- append maximum records through at least ten rotations and check the total
  after every append;
- run 32 producer processes for 100,000 appends and prove complete lines and a
  strict total cap;
- cover exact fit, one byte under, and one byte over;
- cover escaped and multibyte oversized records;
- inject `ENOSPC`, permission, and rotation failures and prove fail-open runtime
  behavior;
- interrupt deterministic rotation fault points and prove bounded recovery.

### Reader and query

- exercise one record, half-cap, full-cap, and a streamed 12 MiB legacy store;
- cover invalid UTF-8, malformed JSON, an oversized newline-free line, a partial
  tail, and matches spanning both segments;
- prove peak allocation is independent of input size;
- prove the encoded response and record limits are enforced during the scan;
- drop the response transport and prove no reader/query capacity remains live.

### Manager ring

- cover wraparound, exact capacity, restart, torn header, torn newest record,
  unsupported version, and sandbox-id isolation;
- after 100 wraps, prove the file remains 64 KiB and the manager retains zero
  decoded records;
- prove destroy removes only the target sandbox's ring.

### Routing and read purity

- 10,000 fleet/status polls produce zero daemon calls;
- 10,000 manager resource reads produce zero daemon calls;
- 10,000 reads of every daemon view leave file fingerprints identical;
- one activity revision change causes one daemon snapshot and unchanged
  revisions cause none.

## 11. Required live proof

All live proof is implemented under
`/Users/yifanxu/Ephemeral-AI-Lab/ephemeral-sandbox-test/e2e/observability` and
follows the separate additive
[`disk_only_state_live_spec.md`](../../ephemeral-sandbox-test/e2e/observability/disk_only_state_live_spec.md).

The live suite must use a real Docker sandbox, the packaged Linux daemon, and
the public manager/runtime/observability CLI through the gateway. Docker and
`/proc` access are measurement-only. A mock, host debug daemon, direct daemon
request, browser-only test, or ad hoc script is not conformance evidence.

## 12. Rollout order

1. Add failing deterministic and live incident regressions.
2. Add strict total event-store configuration and pre-append rotation.
3. Replace dynamic encoding with the fixed writer.
4. Replace whole-history scans with view-specific bounded folds.
5. complete the manager disk ring and remove manager in-memory history.
6. Remove read-triggered daemon collection and gate polling on activity.
7. Disable daemon transparent huge pages.
8. Run smoke, nightly, and release live conformance tiers.

Existing oversized daemons are restarted once after the new packaged binary is
deployed. Restart releases memory retained by the old allocator, but restart is
not part of the ongoing correctness mechanism.

## 13. Acceptance criteria

The implementation is complete only when:

- daemon and manager retain zero telemetry-history records;
- idle daemon observability performs zero work;
- reads do not change storage;
- query memory is independent of a 1-record, 4 MiB, or 12 MiB input;
- post-response anonymous memory returns to its bounded baseline;
- daemon anonymous huge pages remain zero;
- event storage never crosses its configured total, including during rotation;
- each manager ring stays at or below 64 KiB and is deleted on destroy;
- storage faults cannot fail runtime operations;
- all deterministic gates pass; and
- the packaged daemon passes every required live case in the external
  observability directory.
