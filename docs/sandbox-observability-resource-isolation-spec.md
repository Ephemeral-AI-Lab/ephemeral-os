# Sandbox observability resource isolation specification

Status: Implemented
Owners: `sandbox-daemon`, `sandbox-manager`, `sandbox-provider-docker`, `sandbox-observability-telemetry`
Target: observability storage v2

## Summary

Sandbox observability must be idle-memory-neutral and strictly disk-bounded.
Opening a monitoring client, inspecting status, or reading telemetry must not
cause the sandbox daemon to collect or persist more telemetry. Resource metrics
must be collected by the host manager, not by waking the sandbox daemon. The
daemon may record operation-generated events, but it must retain no telemetry
history in heap memory and must keep its on-disk event store within a hard
per-sandbox budget.

The implementation should reuse the current operation contract, manager
runtime port, NDJSON record format, and two-segment event store. It must not add
a database, background message broker, or new service.

## Motivation

The current request path has four properties that violate resource isolation:

1. Every successful daemon RPC schedules `DaemonObservability::collect`.
2. An observability snapshot refreshes and appends resource samples before it
   answers.
3. The manager retains ten minutes of resource samples in a `HashMap` of
   `VecDeque`s.
4. Monitoring clients poll payloads containing timestamps and counters, so
   volatile data can prevent idle backoff.

The daemon serializes each record into a temporary `Vec<u8>`. Its latest-sample
reader is bounded to two samples per requested scope, but other views scan and
materialize the complete rotated and primary logs. Freed allocations may stay
resident in the process allocator. On hosts with transparent huge pages, a
small allocation burst can also produce a 2 MiB anonymous RSS step.

The daemon is written in Rust and has no garbage collector. The relevant
failure is allocator and page retention in the sandbox cgroup. That additional
pressure can still make a Node, Python, or JVM workload sharing the sandbox
budget collect more often or fail sooner.

## Definitions

### Idle sandbox

A sandbox is idle when all of the following are true:

- it is in the `ready` lifecycle state;
- it has no active namespace execution;
- it has no active layer lease or workspace mutation;
- no runtime mutation is being dispatched;
- no explicit observability history request is in flight.

A monitoring page being open, a fleet/status refresh, and a manager health
check do not count as sandbox activity.

### Memory-free observability

“Memory-free” does not mean that the daemon process occupies zero bytes. Code,
stacks, socket buffers, and fixed process state are unavoidable. For this
specification it means:

- no telemetry history, resource series, pending event queue, or response cache
  is retained in the sandbox daemon;
- the idle path performs no observability allocation, collection, serialization,
  file read, or file write;
- event encoding uses a fixed buffer no larger than `max_line_bytes` and does
  not allocate a second copy of the encoded record;
- query readers retain at most one input line plus the configured result limit;
- all request-time memory is bounded independently of telemetry file size;
- post-warmup anonymous memory has no statistically detectable upward trend;
- observability does not create anonymous huge pages in the daemon.

### Disk-bounded observability

Disk-bounded means every append checks and preserves the total budget before
writing. A later collection pass is not allowed to repair an earlier budget
overshoot.

## Required invariants

| ID | Requirement |
|---|---|
| M1 | An idle daemon performs zero observability work after warmup. |
| M2 | The daemon retains zero resource samples and zero event history in heap memory. |
| M3 | A telemetry read is side-effect-free: file content, size, timestamps, and sample count do not change. |
| M4 | The daemon's fixed observability overhead is at most 64 KiB compared with an otherwise identical observability-disabled daemon. |
| M5 | Request-time observability memory is bounded by `max_line_bytes + max_response_bytes`; it does not scale with stored history. |
| M6 | `AnonHugePages` remains zero for the daemon. |
| D1 | In-sandbox observability files consume at most 4 MiB of logical file data per sandbox by default. |
| D2 | The configurable in-sandbox hard maximum is 16 MiB. Values above it are rejected during config validation. |
| D3 | Host-side resource history consumes exactly one file of at most 64 KiB per live sandbox. |
| D4 | Destroying a sandbox removes its host resource history. Removing the container removes its in-sandbox event history. |
| D5 | `ENOSPC`, permission failure, corruption, or rotation failure degrades observability only; it never fails a runtime operation. |
| P1 | Fleet/status polling never contacts a sandbox daemon. |
| P2 | A stable, idle sandbox detail page never contacts a sandbox daemon after its initial state is resolved. |
| P3 | Resource sampling uses the provider's host-side cgroup/Docker metrics path and never invokes a daemon snapshot. |
| P4 | Observability output is best-effort under pressure: bounded storage wins over losslessness. |

## Resource budgets

### Sandbox daemon

- Fixed enabled-versus-disabled anonymous-memory overhead: at most 64 KiB.
- Fixed event encoder buffer: at most 16 KiB on the active thread stack.
- Retained event queue: zero records.
- Retained resource samples: zero records.
- Default event-store budget: 4 MiB total across active and rotated segments.
- Absolute configurable event-store budget: 16 MiB total.
- Maximum record size: 16 KiB including the trailing newline.
- Maximum query response: 256 KiB and 500 records, whichever is reached first.

The directory entry and filesystem metadata are excluded from the logical-byte
budget. Tests also check allocated blocks with one filesystem-block tolerance
per file.

### Host manager

- Resource ring: 64 KiB per live sandbox, including its header.
- Resource response: at most 512 samples and 256 KiB encoded JSON.
- In-memory resource history: zero records between requests.
- Per-request decoded resource series: at most 512 fixed records.
- Destroyed-sandbox retention: zero; deletion is part of sandbox teardown.

The existing ten-minute maximum view remains. With 64-byte records and a
two-second sample interval, the ring can retain more than the required ten
minutes without growing.

## Target architecture

```text
client status polling
        |
        v
manager persisted sandbox record ---- activity revision
        |
        +---- provider Docker/cgroup sample ---- fixed host disk ring
        |
        +---- daemon snapshot only after revision change or explicit refresh

runtime mutation ---- daemon observer ---- bounded two-segment event log
telemetry query --------------------------^ read only
```

### Manager-owned resource metrics

The manager continues to call the existing `SandboxRuntime` resource-metrics
port. The Docker provider reads sandbox cgroup metrics from the host. The
manager runs one host-side sampling loop and writes each fixed-size sample
directly to an on-disk ring without retaining the sample in `ResourceHistory`.
The loop is independent of client and API reads, sleeps when there are no ready
sandboxes, and never invokes a sandbox daemon.

The manager's sandbox snapshot must read the latest ring record. It must not
sample resources as a side effect. The cgroup/resource operation reads the ring
only. Consequently, repeated reads do not change sample count, timestamps, or
file contents.

When `manager.registry_path` is configured, the resource-ring root is the
`observability-resources/` sibling under that registry's parent directory. The
platform state directory is used otherwise. Deriving the path from an existing
manager-owned root keeps production state colocated and lets live tests use a
run-owned temporary root without adding a test-only configuration field.

The host ring uses a fixed layout:

- magic and format version;
- record size and capacity;
- next write index and valid record count;
- fixed records containing timestamp, validity bits, CPU, memory, and IO
  counters.

Writes use `pwrite` semantics followed by a header update. A torn newest record
may be discarded after a crash. Earlier records remain readable. Corruption
returns a partial/unavailable resource view and recreates the ring without
affecting sandbox lifecycle.

### Activity revision instead of daemon polling

Each manager sandbox record carries a monotonic `activity_revision`. The
manager increments it after successful state-changing operations that it
routes to the daemon. Read-only file, status, and observability operations do
not increment it.

Monitoring clients may poll manager state. A client requests a daemon snapshot
only when:

- the activity revision changes;
- an execution is known to be active;
- the window regains focus and the cached revision is stale; or
- the user explicitly refreshes a daemon-owned view.

Once the snapshot reports no active execution or lease, repeated snapshot
polling stops. Volatile timestamps, counters, topology values, and resource
samples must never be used as the activity fingerprint.

Server-Sent Events and WebSockets are not required for this version. Revision
polling solves the sandbox-memory problem without adding a persistent transport.

### Side-effect-free daemon queries

The daemon must remove both read-triggered collection paths:

- successful RPC/HTTP completion must not automatically call `collect`;
- `observability_snapshot` must not call `refresh_resource_samples`.

Reading observability must not emit a trace about that read into the same store.
Errors may be returned to the caller and host logs, but must not recursively
append to the queried telemetry file.

Resource samples are removed from the daemon event log. Runtime operations
continue to emit spans and events at the points where real work occurs.

### Strict event-store budget

The current primary plus one rotated sibling is retained. The configured
`max_disk_bytes` applies to their sum, not to each file. The active segment
rotates before an append that would exceed half of the total budget.

All producers coordinate through one per-store cross-process lock at the sink
boundary. The size check, optional rotation, and append are one serialized
operation. The lock has no queued in-process record buffer.
After any successful append:

```text
active.len + rotated.len <= max_disk_bytes
active.len <= max_disk_bytes / 2
rotated.len <= max_disk_bytes / 2
```

An oversized record is replaced with the existing truncation marker. If even
the marker cannot fit, the record is dropped. Rotation replaces the older
segment atomically. There is no retry loop on storage failure.

The configuration schema becomes:

```yaml
observability:
  enabled: true
  max_disk_bytes: 4194304
  max_line_bytes: 16384
```

`max_disk_bytes` accepts 1 MiB through 16 MiB. The former `max_file_bytes`
field is accepted for one compatibility release, interpreted as the legacy
per-segment value, clamped to the 16 MiB total safety maximum, and reported as
deprecated. New configuration and tests use `max_disk_bytes`.

### Allocation-bounded event encoding

`Sink::append` must stop using `serde_json::to_vec`. It serializes into one
fixed buffer implementing `std::io::Write`, appends the newline, and performs
one append write. The fixed buffer size is the validated `max_line_bytes` and
has a compile-time upper bound of 16 KiB.

The encoder does not clone the record. Existing operation-owned strings and
attributes may be borrowed for serialization. The observability path must not
build a second dynamic `Value` tree solely for persistence.

### Streaming, bounded reads

`Reader::scan` must not use `read_to_string` or return the full parsed log.
Each view folds the rotated and primary segments with one capped line buffer:

- latest sample: retain two matching records;
- resource series: removed from the daemon;
- events/raw: retain at most the response record and byte limits;
- trace: retain only records matching the requested trace, up to response
  limits;
- latest trace id: retain one candidate;
- layer views: retain only their documented limits.

Lines larger than `max_line_bytes`, invalid UTF-8, malformed JSON, and a partial
crash tail are skipped. Query memory therefore depends on the public response
limit, not the disk budget.

### Transparent huge pages

The daemon disables transparent huge pages for itself at process startup on
Linux. Failure to apply the process setting is reported in daemon startup logs
and fails the memory-isolation conformance test. Workload processes keep their
normal host policy.

## Failure behavior

| Failure | Required behavior |
|---|---|
| Event store full | Rotate before append; never exceed the hard cap. |
| Record larger than line cap | Persist a bounded truncation marker. |
| `ENOSPC` or read-only filesystem | Drop telemetry, increment a fixed atomic drop counter, continue the operation. |
| Host resource ring corrupt | Report resource view partial, recreate the ring, continue lifecycle service. |
| Daemon event line corrupt | Skip the line and continue streaming. |
| Crash during append | Ignore a partial final line after restart. |
| Crash during rotation | Recover either the old or new complete segment pair within the same budget. |
| Old daemon without activity revision support | Manager disables continuous snapshot polling; explicit refresh remains available. |

Drop/error counters are fixed-width atomics, not an in-memory error queue. They
are exposed in health output and reset only on daemon restart.

## Test specification

Test ownership is deliberately split:

- deterministic Rust unit, allocation, and application integration tests live
  with the owning product crates in this repository;
- downstream polling tests live with their owning client and use fake timers
  and request counters;
- every backend test requiring a real packaged daemon and a real Docker
  sandbox lives in
  `/Users/yifanxu/Ephemeral-AI-Lab/ephemeral-sandbox-test/e2e/observability`.

The detailed external live catalog, measurement format, artifact limits,
cleanup contract, and CI admission rules are defined in
[`e2e/observability/test_spec.md`](../../ephemeral-sandbox-test/e2e/observability/test_spec.md).
In this document, “live” always means a sandbox created through that external
harness and reached through the public backend CLI/gateway path. It does not
mean a mocked daemon, an in-process service, or a browser test.

### 1. Deterministic unit tests

Add these tests under existing crate `tests/` directories. No test helpers or
allocator instrumentation belongs in `src/`.

#### Telemetry sink

1. `append_never_exceeds_total_budget`
   - use 16 KiB records until at least ten rotations occur;
   - assert the sum of both segment lengths after every append;
   - assert every persisted line parses.
2. `concurrent_processes_preserve_budget_and_lines`
   - run 32 producer processes against one store;
   - mix small and maximum records for 100,000 appends;
   - assert strict size bounds, no interleaving, and no partial middle line.
3. `boundary_append_rotates_before_write`
   - exercise exact-fit, one-byte-short, and one-byte-over boundaries.
4. `oversized_record_writes_one_bounded_marker`
   - test strings requiring JSON escaping and multi-byte UTF-8.
5. `storage_failure_never_fails_business_operation`
   - inject permission denied, missing directory, and `ENOSPC`;
   - assert one drop count per attempted record and no retry loop.
6. `crash_during_rotation_recovers_within_budget`
   - terminate at each rename/write synchronization point;
   - reopen and assert valid recovery and the hard cap.

#### Allocation harness

Use a dedicated integration-test binary with a counting global allocator. It
must run serially and enable counting only around the operation under test.

1. Construct the record and open the sink before counting.
2. Append one normal record and one truncated record.
3. Assert the encoder performs zero heap allocations after the record exists.
4. Fold a maximum-size store with a result limit of one.
5. Assert peak live allocation remains below `max_line_bytes + 8 KiB`.
6. Repeat the fold with 1, 10, and 1,000 stored records and assert the peak does
   not grow with input record count.

#### Streaming reader

Test malformed JSON, invalid UTF-8, a partial final line, a maximum escaped
line, records split across internal read buffers, both segment orders, and a
trace whose matching records straddle rotation. Every view must enforce both
record and encoded-byte response limits.

#### Manager resource ring

Test wraparound, exact capacity, timestamp ordering, restart recovery, torn
header, torn newest record, unsupported version, and sandbox-id isolation.
After 100 complete wraps, the file length must remain exactly 64 KiB.

### 2. Application integration tests

#### Read purity

For every observability read operation:

1. create known event files;
2. capture file length, allocated blocks, modification time, and SHA-256;
3. execute the read 10,000 times;
4. assert all captured values are unchanged;
5. assert the daemon collection-call counter is zero.

The snapshot test must additionally prove that no resource sample is appended.

#### Manager routing

Use a counting fake `SandboxDaemonClient` and real manager service:

- 10,000 fleet/status polls produce zero daemon invocations;
- 10,000 sandbox resource reads produce zero daemon invocations;
- unchanged activity revision produces zero daemon snapshots;
- one revision change produces exactly one daemon snapshot;
- an active execution permits polling only until the first inactive snapshot.

### 3. Live memory conformance test

Implement this section in
`ephemeral-sandbox-test/e2e/observability/resource_isolation`. Run the packaged
Linux daemon used in production, not a host debug build. Test
observability-enabled and observability-disabled sandboxes as an A/B pair with
the same image, limits, workload, and start order alternated between runs.
Product behavior is invoked only through the public CLI/gateway path. Docker
and `/proc` access are permitted only for out-of-band measurement and
deterministic fixture installation.

Collect once per second:

- cgroup `memory.current`;
- `memory.stat` fields `anon`, `file`, `kernel`, `kernel_stack`, `pagetables`,
  `sock`, `slab`, and `anon_thp`;
- `/proc/<daemon-pid>/smaps_rollup` fields `Rss`, `Pss`, `Anonymous`,
  `Private_Dirty`, and `AnonHugePages`;
- daemon CPU usage and IO bytes;
- event-store logical and allocated bytes;
- manager host-ring logical and allocated bytes.

Use three repetitions for nightly tests and five for release qualification.
Stream every raw sample immediately to a bounded JSONL artifact; the Python
test process must not retain a sample list proportional to test duration.

#### Phases

1. Start and warm both sandboxes for five minutes.
2. Leave both idle with no monitoring client for thirty minutes.
3. Poll the public aggregate and scoped snapshot backend routes for thirty
   minutes at the active client cadence.
4. Poll the public manager-owned Resources/cgroup backend route for thirty
   minutes at the active client cadence.
5. Run 100,000 bounded event-producing operations.
6. Stop backend polling and allow a ten-minute cooldown.
7. Request cgroup reclaim where supported, then capture the final five minutes.

No browser is required for these backend phases. Client request scheduling is
covered separately by each client implementation. Exact manager-to-daemon
invocation counts are proved by the application integration tests with a
counting fake; live E2E proves the consequence by observing no daemon CPU/IO
work, memory trend, or event-store mutation.

#### Memory gates

Analyze `memory.stat anon` and `smaps_rollup Anonymous` rather than gating on
RSS alone. RSS includes reclaimable file-backed pages.

- During each idle phase, the Theil-Sen anonymous-memory slope must be no more
  than 4 KiB/hour.
- The final-five-minute anonymous median minus the first-five-minute median
  must be no more than 64 KiB.
- Enabled minus disabled steady-state anonymous memory must be no more than
  64 KiB after warmup.
- After burst and cooldown, anonymous memory must return within 128 KiB of its
  pre-burst median.
- `AnonHugePages` and cgroup `anon_thp` must remain zero.
- Idle daemon CPU difference between enabled and disabled must be below one
  scheduler tick per minute.
- Public manager polling must produce no daemon storage IO and no post-warmup
  anonymous-memory trend; exact zero invocation count is the integration-test
  gate above.
- Event-store bytes must remain exactly unchanged throughout both idle phases.

A run fails if any individual repetition breaches a hard bound. Slope and
median results are also reported with bootstrap 95% confidence intervals to
make slow regressions visible before they reach the hard bound.

### 4. Workload GC isolation test

The daemon has no GC, but hosted runtimes do. Run a fixed Node.js allocation
workload in enabled and disabled A/B sandboxes under the same tight cgroup
limit. Record GC pause duration, collection count, event-loop delay, peak
workload RSS, and OOM outcome.

The release gate is:

- no OOM in either arm;
- enabled p99 GC pause is no more than disabled p99 plus 1 ms;
- enabled p99 event-loop delay is no more than 5% above disabled plus 1 ms;
- the daemon memory gates above still pass during workload execution.

Run at least five alternating A/B repetitions. This is a release benchmark,
not a per-commit unit test, because host scheduling noise makes single-run GC
comparisons unreliable.

### 5. Disk conformance and fault tests

Cases involving real sandboxes belong in the external observability suite;
deterministic crash-point injection remains in product tests until the external
harness has a run-scoped restart/fault primitive.

1. Sustain maximum-size event production for one hour; assert the event store
   never exceeds its configured total at any sample.
2. Verify logical bytes and allocated blocks independently.
3. Run 100 live sandboxes and assert aggregate observability storage is no more
   than the sum of their declared budgets.
4. Destroy all 100 sandboxes and assert host ring files are gone and Docker
   writable-layer usage returns to its pre-test tolerance.
5. Give the current test sandbox an isolated tiny store filesystem or quota,
   force `ENOSPC`, and prove runtime commands still succeed without a retry
   storm. Never fill Docker's global disk or a shared host filesystem.
6. Use deterministic product fault points for append/rotation crash tests. Add
   a live restart test only after the harness can target and recover the
   current run's sandbox safely; a timed best-effort `SIGKILL` is forbidden.
7. Start with legacy logs larger than the new cap. Migration must stream from
   disk, retain only the newest complete records that fit, and never exceed the
   memory gates.

### 6. CI tiers

| Tier | Owner | Frequency | Coverage |
|---|---|---|---|
| Unit | Product repository | Every change | Sink, reader, ring, allocation harness, polling logic |
| Integration | Product repository | Every change | Read purity and routing counts |
| Smoke | External live E2E | Every observability change | Real sandbox public contracts plus five-minute idle/polling regression |
| Nightly | External live E2E | Nightly | Three-run memory test, read purity, strict disk cap |
| Release | External live E2E | Before release | Five-run A/B and Node GC tests, six-hour disk soak, 100-sandbox cleanup, isolated faults |

Passing unit tests is not sufficient for conformance. A release is
memory-isolation compliant only after the packaged daemon passes the release
live tests.

## Migration

1. Add hard total-disk validation and bounded streaming readers while retaining
   the existing record format.
2. Remove daemon collection from successful request completion and snapshot
   reads.
3. Replace manager `ResourceHistory` with the fixed host ring.
4. Add manager activity revision for downstream snapshot gating.
5. Disable daemon transparent huge pages and enable live conformance tests.
6. Deprecate `max_file_bytes`; remove it after one compatibility release.

On first v2 startup, an oversized legacy store is compacted by a bounded
streaming pass. No legacy file may be loaded wholesale. Old daemons remain
readable, but clients must not continuously poll them; users receive an explicit
notice that resource-isolation guarantees require a v2 daemon.

## Acceptance criteria

The work is complete only when all of the following are true:

- unchanged activity revisions cause no daemon observability request;
- no telemetry read changes telemetry storage;
- the manager retains no resource history in memory;
- the daemon retains no telemetry history in memory;
- post-warmup idle anonymous memory satisfies every live-test gate;
- the daemon creates no anonymous huge pages;
- in-sandbox event storage never exceeds the configured hard total;
- host resource history remains a fixed 64 KiB per live sandbox and is deleted
  on teardown;
- storage failures cannot fail or materially delay runtime operations;
- the packaged production daemon, not only unit-test components, passes the
  nightly and release conformance suites under
  `ephemeral-sandbox-test/e2e/observability`.
