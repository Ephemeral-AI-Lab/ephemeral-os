# Observability Rework — Spec

Status: ready-to-implement.

Collapse all observability into **one dependency-light crate**
(`sandbox-observability`) backed by an **append-only NDJSON event stream**,
replacing both the SQLite snapshot store and the six scattered `timing.rs`
modules. One model — `span` / `event` / `sample` — owns spans, traces, events,
and resource metrics; the runtime emits into it directly; a developer fetches it
with one command.

This spec is **example-driven**: §4 shows the literal bytes that land in the log
and the rendered views, for real scenarios. The rest is structure around those
examples.

---

## 1. What replaces what

| Today | After |
|---|---|
| `sandbox-observability` = SQLite store of 4 snapshot tables, `rusqlite` | same crate, an append-only NDJSON stream, `serde_json` only |
| `sandbox-daemon/src/observability` = a *second* "observability" (collect + RPC) | thin caller; cgroup/disk readers fold into the crate |
| 6 × `timing.rs`, ~38 `timing::duration` sites, `EOS_*_TIMING*` files | one `Observer` API; the dotted labels become span `name`s |
| no cross-process correlation, no fetch path | `trace` ids + one `get_observability` RPC + `sandbox-cli … observability` |

Why a rewrite and not a patch: "one layer" + "drop SQLite" *forces* it — SQLite
is precisely what keeps this crate out of the runtime today (there is a test
whose only job is to keep it out). Remove SQLite and the crate becomes a leaf the
runtime can emit into. The new internals are **less** code than the SQLite stack
they replace.

---

## 2. Architecture

Everything is produced **in the sandbox**, written to **one file**, and pulled to
the **host** over the daemon protocol.

```
                 IN SANDBOX (container)                                    HOST
 ┌──────────────────────────────────────────────────────────┐   ┌──────────────────────┐
 │ sandbox-daemon  (one process)                             │   │ sandbox-gateway CLI  │
 │                                                            │   │                      │
 │  runtime libs: operation / workspace / layerstack /        │   │                      │
 │                namespace-execution                         │   │                      │
 │        │ obs.span(…)  obs.event(…)  obs.sample(…)           │   │                      │
 │        ▼                                                   │   │                      │
 │     Observer ──────► Sink (file, O_APPEND)                 │   │                      │
 │                          │                                 │   │                      │
 │                          ▼                                 │   │                      │
 │   <runtime_dir>/observability/observability.ndjson         │   │                      │
 │                          ▲              ▲                  │   │                      │
 │  namespace-process ──────┘ (forked,     │                  │   │                      │
 │     obs.span(…)            same file)    │ Reader           │   │                      │
 │                                          │                 │   │                      │
 │  DaemonObservability ── get_observability RPC ◄────────────┼───┤ sandbox-cli <id>     │
 │                         └──── view JSON ───────────────────┼──►│   observability …    │
 └──────────────────────────────────────────────────────────┘   └──────────────────────┘
```

- **Write:** every in-sandbox process appends atomic single-line records.
- **Store:** one NDJSON file at `<daemon_runtime_dir>/observability/observability.ndjson`
  (the path `paths.rs` derives today, retargeted from `.sqlite`).
- **Read:** a `Reader` folds the stream into views; the daemon serves them over
  the existing RPC seam; the CLI renders them.

---

## 3. The record model

One JSON object per line. Three `kind`s. Shared envelope: `ts` (unix ms),
`kind`, `sandbox`, `component`, `pid`.

### 3.1 `span` — a timed unit of work, emitted as **two** records

```json
{"ts":..,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime",
 "trace":"req-7f3","span":"s-05","parent":"s-01","name":"namespace.exec.shell",
 "attrs":{"exec_id":"ns-9","async":true}}
{"ts":..,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime",
 "trace":"req-7f3","span":"s-05","dur_ms":4231.0,"status":"completed","exit_code":0}
```

`ev` = `start|end`; `trace` groups one flow; `span` pairs start↔end; `parent`
builds the tree; `dur_ms`/`status`/`exit_code` land on `end`.

**Why two records, not a drop-guard.** The work is asynchronous: the call
returns long before the work finishes.

```
 wall: 0ms        ~1051ms                                        ~4273ms
        │            │                                              │
caller  ├─ exec_command span ─────────────────────────────────────┤
        │  s-01.start                                  s-01.end ───┘  (returns at yield)
        │
child   ├─ namespace.exec.shell span ─────────────────────────────┼──── on_terminal
        │  s-05.start (on_running)                       s-05.end ─┘   (watcher thread)
        │                                                          │
                        the span OUTLIVES the call ────────────────┘  → start/end pair
```

An **unpaired `start`** (start, no end yet) = *in flight right now*. That single
fact replaces the SQLite `namespace_execution_snapshots` table and its
replace/reconcile polling. A parent span may `end` before its children — normal
for async; `parent` is a logical ancestor, not a lifetime scope.

### 3.2 `event` — a point-in-time domain fact, hung off a span

```json
{"ts":..,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime",
 "trace":"req-7f3","parent":"s-07","name":"layerstack.publish",
 "attrs":{"base":"r5","revision":"r6","layers_added":2,"bytes":40960,"no_op":false}}
```

Side-effects (publish, lease, state transition, error) — carry `trace`/`parent`
so they attach to the originating flow even when emitted in the async tail.

### 3.3 `sample` — a periodic metric reading (cgroup + disk)

```json
{"ts":..,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon",
 "scope":"ws-1","cpu_usec":12345,"mem_cur":1048576,"mem_max":2097152,
 "disk_bytes":40960,"files":12,"dirs":3,"symlinks":0,"truncated":false}
```

`scope` = `"sandbox"` or a workspace id. **Deltas are not stored** — the reader
computes them from adjacent samples (§4.4).

---

## 4. Example cases — the observability outputs

Each case shows the **trace diagram**, the **raw NDJSON** (append order = time
order), and the **rendered view** a developer sees.

### 4.1 Case A — one-shot `exec_command` (sync call + async tail + finalize-publish)

A client runs a command with no existing session. The runtime creates a one-shot
workspace (mount over a layerstack lease), runs the shell, and on child exit tears
the workspace down — publishing its diff into the layerstack — **on the watcher
thread, after the call already returned**.

```
req-7f3   exec_command  (one-shot)
 ├─ daemon.dispatch ─────────────────────────── returns at yield (~1.05s)
 │   └─ exec_command
 │       ├─ workspace_session.create
 │       │   └─ workspace.create
 │       │       • lease.acquired r5
 │       │       └─ namespace.exec.mount_overlay   [async]
 │       └─ namespace.exec.shell                   [async] ── outlives the call ──┐
 │           └─ ns_runner.shell.spawn_child  (namespace-process)                  │
 └─ ── watcher thread, after return ──                                            │
     • exec.terminal exit=0  ◄──────────────────────────────────────────── child exits
     └─ workspace_session.destroy (one-shot)
         • layerstack.publish r5→r6  (+2 layers, 40KB)
         • lease.released r6
```

**Raw `observability.ndjson` (in emission order):**

```json
{"ts":1719500000000,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-daemon","trace":"req-7f3","span":"s-00","name":"daemon.dispatch","attrs":{"op":"exec_command"}}
{"ts":1719500000002,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-01","parent":"s-00","name":"exec_command","attrs":{"one_shot":true}}
{"ts":1719500000003,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-02","parent":"s-01","name":"workspace_session.create"}
{"ts":1719500000004,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-03","parent":"s-02","name":"workspace.create"}
{"ts":1719500000009,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","parent":"s-03","name":"lease.acquired","attrs":{"revision":"r5","owner":"req-7f3"}}
{"ts":1719500000012,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-03","dur_ms":8.0,"status":"completed"}
{"ts":1719500000013,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-04","parent":"s-02","name":"namespace.exec.mount_overlay","attrs":{"exec_id":"ns-8","async":true}}
{"ts":1719500000040,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-04","dur_ms":27.0,"status":"completed","exit_code":0}
{"ts":1719500000041,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-02","dur_ms":38.0,"status":"completed"}
{"ts":1719500000042,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-05","parent":"s-01","name":"namespace.exec.shell","attrs":{"exec_id":"ns-9","async":true}}
{"ts":1719500000055,"kind":"span","ev":"start","sandbox":"eos-abc","component":"namespace-process","trace":"req-7f3","span":"s-06","parent":"s-05","name":"ns_runner.shell.spawn_child","attrs":{"exec_id":"ns-9"}}
{"ts":1719500000061,"kind":"span","ev":"end","sandbox":"eos-abc","component":"namespace-process","trace":"req-7f3","span":"s-06","dur_ms":6.0,"status":"completed"}
{"ts":1719500001050,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-01","dur_ms":1048.0,"status":"completed"}
{"ts":1719500001051,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-daemon","trace":"req-7f3","span":"s-00","dur_ms":1051.0,"status":"completed"}
{"ts":1719500004273,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-05","dur_ms":4231.0,"status":"completed","exit_code":0}
{"ts":1719500004274,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","parent":"s-05","name":"exec.terminal","attrs":{"status":"completed","exit_code":0}}
{"ts":1719500004275,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-07","parent":"s-05","name":"workspace_session.destroy","attrs":{"one_shot":true}}
{"ts":1719500004290,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","parent":"s-07","name":"layerstack.publish","attrs":{"base":"r5","revision":"r6","layers_added":2,"bytes":40960,"no_op":false}}
{"ts":1719500004295,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","parent":"s-07","name":"lease.released","attrs":{"revision":"r6"}}
{"ts":1719500004300,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-7f3","span":"s-07","dur_ms":25.0,"status":"completed"}
```

Note `s-01`/`s-00` close at ~1.05 s while `s-05` is still open; the publish/lease
events arrive at ~4.29 s under the same `req-7f3`.

**Rendered — `sandbox-cli eos-abc observability --trace req-7f3`:**

```
trace req-7f3   sandbox eos-abc   wall 4.30s   (call returned at 1.05s)

  +00.000  daemon.dispatch op=exec_command                    1051ms  ✓
  +00.002   └ exec_command one_shot                           1048ms  ✓
  +00.003      ├ workspace_session.create                       38ms  ✓
  +00.004      │   └ workspace.create                            8ms  ✓
  +00.009      │      • lease.acquired r5
  +00.013      ├ namespace.exec.mount_overlay   [async]         27ms  ✓ exit0
  +00.042      └ namespace.exec.shell           [async]       4231ms  ✓ exit0   ← outlives call
  +00.055         └ ns_runner.shell.spawn_child                 6ms  ✓
  +04.274         • exec.terminal exit=0
  +04.275         └ workspace_session.destroy one_shot          25ms  ✓
  +04.290            • layerstack.publish r5→r6  +2 layers 40KB
  +04.295            • lease.released r6
```

### 4.2 Case B — persistent session + an in-flight snapshot

A long-running command against an existing session. While it runs, the developer
asks for the live snapshot — the command shows up as **in flight** (a `start`
with no `end`), with no polling table behind it.

**Raw (the relevant lines so far):**

```json
{"ts":1719500100000,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-9a1","span":"s-10","name":"exec_command","attrs":{"workspace_session":"ws-7","one_shot":false}}
{"ts":1719500100020,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-9a1","span":"s-11","parent":"s-10","name":"namespace.exec.shell","attrs":{"exec_id":"ns-42","async":true}}
{"ts":1719500101020,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"req-9a1","span":"s-10","dur_ms":1020.0,"status":"completed"}
```

`s-11` has a `start` and no `end` → still running.

**Rendered — `sandbox-cli eos-abc observability` (snapshot view):**

```
sandbox eos-abc   state ready

  workspaces
    ws-7   active    profile=default   layers=4

  in-flight executions            (spans started, not ended)
    ns-42  namespace.exec.shell   trace req-9a1   running 7.3s   ws-7

  resources (latest)
    sandbox   cpu 12.3s   mem 41MB / 256MB
    ws-7      cpu  4.1s   mem 18MB        disk 1.2MB (320 files)
```

### 4.3 Case C — squash / autosquash (background trace, no request)

Squash has no request driving it. It opens a root span with a fresh trace and a
`trigger` attr; a future **autosquash policy** is the *same shape* with
`trigger:"autosquash"` — no new machinery.

```
sq-22  layerstack.squash  trigger=autosquash         [async root, no parent]
 • squash.planned     layers=5 est_reclaim=12MB
 └ squash.project_checkpoint
 • squash.completed   5→1 layers  reclaimed=11.8MB  revision=r9
```

**Raw:**

```json
{"ts":1719600000000,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","span":"q-0","name":"layerstack.squash","attrs":{"trigger":"autosquash"}}
{"ts":1719600000005,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","parent":"q-0","name":"squash.planned","attrs":{"layers":5,"est_reclaim_bytes":12582912}}
{"ts":1719600000010,"kind":"span","ev":"start","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","span":"q-1","parent":"q-0","name":"squash.project_checkpoint"}
{"ts":1719600000820,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","span":"q-1","dur_ms":810.0,"status":"completed"}
{"ts":1719600000825,"kind":"event","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","parent":"q-0","name":"squash.completed","attrs":{"from_layers":5,"to_layers":1,"reclaimed_bytes":12373196,"revision":"r9"}}
{"ts":1719600000830,"kind":"span","ev":"end","sandbox":"eos-abc","component":"sandbox-runtime","trace":"sq-22","span":"q-0","dur_ms":830.0,"status":"completed"}
```

**Rendered — `sandbox-cli eos-abc observability --trace sq-22`:**

```
trace sq-22   sandbox eos-abc   wall 0.83s   trigger=autosquash

  +00.000  layerstack.squash                                  830ms  ✓
  +00.005   • squash.planned   layers=5  est_reclaim=12.0MB
  +00.010   └ squash.project_checkpoint                       810ms  ✓
  +00.825   • squash.completed 5→1 layers  reclaimed=11.8MB → r9
```

### 4.4 Case D — resource samples + deltas

Periodic `sample` lines; the reader computes deltas at read time (none stored).

**Raw:**

```json
{"ts":1719500000000,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"ws-1","cpu_usec":1000000,"mem_cur":18000000,"disk_bytes":1200000,"files":320}
{"ts":1719500010000,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"ws-1","cpu_usec":4100000,"mem_cur":21000000,"disk_bytes":1320000,"files":340}
{"ts":1719500020000,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"ws-1","cpu_usec":4250000,"mem_cur":20500000,"disk_bytes":1320000,"files":340}
```

**Rendered — `sandbox-cli eos-abc observability --samples ws-1 --window 60000`:**

```
scope ws-1   window 60s   (Δ computed at read)

  t(+s)   cpu_total   Δcpu      mem_cur    disk        Δdisk
  00.0     1.00s        –       18.0MB     1.20MB        –
  10.0     4.10s     +3.10s     21.0MB     1.32MB     +120KB
  20.0     4.25s     +0.15s     20.5MB     1.32MB        +0
```

---

## 5. Emit seams — where the `Observer` plugs in

Hook the **existing** lifecycle edges; do not sprinkle inline timing.

```
 sync scope        →  let _s = obs.span("workspace.create.lock_state");   (RAII: start now, end on drop)
 async ns exec     →  ExecutionObserver::on_running  → obs.open(ctx, "namespace.exec.<kind>")
                      ExecutionObserver::on_terminal → handle.end(status, exit_code)   (watcher thread)
 layerstack facts  →  acquire_snapshot_with_lease → event lease.acquired
                      publish_changes            → event layerstack.publish | publish_rejected
                      squash                     → root span + squash.planned/completed
 cgroup/disk       →  daemon collect() → obs.sample(scope, metrics)   (readers moved into the crate)
```

- `ExecutionObserver` already exists (`namespace-execution/src/types.rs:19`,
  `on_running`/`on_terminal`) wired as `NoopObserver`
  (`operation/src/command/service/core.rs:34`). Replace it with
  `ObsExecutionObserver`. The async span then spans exactly `on_running …
  on_terminal` (`engine.rs:108`/`:138` → `:202`), closing on the watcher thread.
- The one-shot finalize (`exec_command.rs:181`) runs inside that watcher and
  carries the trace context, so its `destroy` span and `publish`/`lease` events
  land under the originating trace (Case A tail).
- **trace id:** reuse the `owner_request_id` already threaded into layerstack
  leases (`layerstack/src/stack/mod.rs:75`) as the request-wide trace id.

---

## 6. Crate rework — keep / reshape / delete

| File | Fate | Note |
|---|---|---|
| `src/paths.rs` | **Keep** | `database_path()` → `log_path()` (`observability.ndjson`) |
| `src/records.rs` | **Reshape** | keep length bounds + validation; structs → `Span`/`Event`/`Sample` |
| `src/store.rs` | **Delete+replace** | `rusqlite` store → `Sink` (append `O_APPEND` writer) |
| `src/store/schema.rs` | **Delete** | 8 migrations gone |
| `src/store/{read,rows}.rs` | **Delete+replace** | SQL queries → `Reader` + views |
| `src/lib.rs` | **Rewrite** | export `Observer`, `Span`, `TraceContext`, `Reader`, record + view types |
| `Cargo.toml` | **Edit** | drop `rusqlite`; add `serde`/`serde_json` |

```
crates/sandbox-observability/src/
  lib.rs            paths.rs            record.rs        emit.rs        read.rs
  collect/{mod.rs,  cgroup.rs,  disk.rs}      ← moved from sandbox-daemon (pure &Path → struct)
```

**Atomicity / bounds.** Records are appended as single lines; `O_APPEND` writes
≤ `PIPE_BUF` (4096 B) are atomic on local Linux fs — so the forked
namespace-process and the daemon can share the file. The reused `MAX_*` length
bounds keep every line under that ceiling (their new purpose). Soft size cap with
one rotation (`…ndjson.1`) keeps the file bounded; the reader reads both.

**Emit API (shape):**

```rust
impl Observer {
    fn span(&self, name: &'static str) -> Span;                 // sync; end on drop; thread-local parent
    fn span_in(&self, ctx: TraceContext, name: &'static str) -> Span;
    fn open(&self, ctx: TraceContext, name: &'static str) -> SpanHandle;  // async; Send; .end(status, code)
    fn event(&self, ctx: TraceContext, name: &'static str, attrs: Attrs);
    fn sample(&self, scope: Scope, metrics: SampleMetrics);
}
```

Emit is config-gated (`observability.enabled`, default on in-sandbox; off for the
host CLI unless a flag) and near-free when disabled. Observability MUST never
fail the operation it observes — over-long attrs truncate, errors are swallowed.

**Reader (shape):** `snapshot()` (current state + in-flight = unpaired starts),
`trace(id)` (the waterfall), `samples(scope, window)` (series + deltas),
`raw(filter)`. Single forward scan, filter-while-reading.

**Boundary.** `sandbox-observability` stays a **leaf** (`serde`, `serde_json`,
`thiserror` only — never `protocol`/`runtime`/`daemon`/`config`). All dependency
edges point into it; the graph stays acyclic. That leaf-ness is what lets the
runtime emit into it.

---

## 7. Fetch — one generalized op

The current single op `get_observability_snapshot`
(`sandbox-daemon/src/server/dispatch.rs:9`, handler `:87`) is **generalized**:

```
op: "get_observability"
params: { view:"snapshot"|"trace"|"samples"|"raw" (default "snapshot"),
          trace?, scope?, since_ms?, window_ms? (≤600_000), kind? }
→ JSON of the view
```

```
sandbox-cli <id> observability                       # snapshot (Case B)
sandbox-cli <id> observability --trace req-7f3       # waterfall (Case A)
sandbox-cli <id> observability --samples ws-1 --window 60000   # series (Case D)
sandbox-cli <id> observability --raw --kind span --since <ms>
```

---

## 8. Removal checklist (mechanical; greps must go to zero)

1. Delete 6 `timing.rs` + their `mod` decls: `sandbox-daemon` (`lib.rs:8`),
   `sandbox-gateway/cli` (`cli/mod.rs:7`), `namespace-execution` (`lib.rs:9`),
   `namespace-process` (`lib.rs:12`), `operation` (`lib.rs:12`), `workspace`
   (`lib.rs:24`).
2. Replace/delete ~38 call sites → `grep -rn 'timing::' crates` is empty. Dotted
   labels carry over verbatim as span `name`s.
3. Delete `runtime_timing_env()` + uses (`sandbox-provider-docker/src/runtime.rs:88,113`)
   → `grep -rn 'EOS_.*TIMING' crates` is empty.
4. Delete `src/store/**` + `rusqlite` → `grep -rn 'rusqlite' crates` is empty.
5. Collapse `sandbox-daemon/src/observability` to the thin caller (cgroup/disk
   moved; delta/counter caches removed; `collect()` emits `obs.sample`).
6. Repoint the boundary test
   `operation/tests/observability_snapshot.rs` to the new leaf invariant (no
   protocol/runtime/daemon dep) or remove it.

**Untouched:** `layerstack/src/stack/projection/checkpoint.rs` — a legitimate
layer-projection domain concept, not timing.

---

## 9. Rollout

1. **Crate rework** (§6) — record types, `Sink`, `Observer`/`Span`,
   `Reader`/views, `paths.rs` retarget, move `collect/{cgroup,disk}`. Standalone,
   unit-tested; nothing consumes it yet.
2. **Daemon swap** — build `Observer`; `NoopObserver` → `ObsExecutionObserver`;
   `collect()` → `obs.sample`; generalize the RPC op + CLI.
3. **Replace the 38 timing sites** with span guards / events (Phase A:
   per-process correlation; in-daemon spans already form per-request trees).
4. **Layerstack events** (lease / publish / squash).
5. **Removal checklist** (§8) — the three greps gate the change.
6. **Phase B (follow-up):** thread `trace`/`parent` through
   `NamespaceRunnerRequest` (and optionally the daemon protocol) so the forked
   process and gateway-initiated requests stitch into one cross-process tree; the
   watcher already carries the handle. Autosquash opens a background root span
   here. **No schema change between phases** — Phase A already has `trace`/`parent`
   fields; Phase B only populates them across boundaries.

---

## 10. Testing

- **Unit (crate):** record round-trip; bounds keep lines < `PIPE_BUF`; `Reader`
  folds — in-flight detection (start w/o end), latest-state-per-id, pairwise
  sample deltas; N concurrent appenders → every line parses.
- **Integration:** an `exec_command` reproduces Case A's shape (spans paired,
  `namespace.exec.shell` closes on terminal, finalize events share the trace); a
  `squash` reproduces Case C.
- **Fetch:** `get_observability` returns each view; `--trace` the waterfall;
  `--samples` the series with deltas.
- **Gates:** the three removal greps return nothing; `cargo build`, `cargo test`,
  `cargo clippy --all-targets` clean.
