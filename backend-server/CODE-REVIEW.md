# `backend-server` — Strict Rust Architecture & Idiomatic Review

Scope: `backend-server/` (8 crates, ~14.5K LOC incl. tests). Generated review
artifact — safe to delete. Reviewed against SOLID (Rust-flavored), generics &
trait bounds, idiomatic ownership/error/control-flow, and naming.

Method: `cargo clippy --workspace --all-targets` (baseline) + a 27-agent
adversarially-verified pass (per-crate finders + cross-cutting architecture and
naming reviewers, every finding re-checked against `file:line`) reconciled with a
manual read of all architecturally-central and largest files. Findings that
amounted to "add a trait/generic for extensibility" or that clippy already covers
were rejected as false positives, per this codebase's deliberate anti-speculation
contract.

---

## 1. High-Level Summary

This is **high-quality, idiomatic Rust** — among the cleaner multi-crate
workspaces you'll review. The architecture is the strongest part: 8 crates split
along real ownership boundaries (types → config → store → runtime/obs → api →
host → main), an intentional dependency DAG with **no cross-workspace back-edges**,
and trait seams placed *only* where substitution is load-bearing (the sealed,
object-safe `ProviderAdapter`; the `RunControl`/`SandboxRegistry` API ports backed
by test doubles; `SandboxGateway`/`AuditSink`/`RunHost`). The acceptance contracts
that matter are upheld and test-pinned: AC4 credential sanitization
(`SandboxView`, `ApiError`), AC5 persist-before-broadcast streaming (`EventBus`),
AC6 non-blocking audit drain (`PersistingSink`), AC7/AC8 id-separation and
epoch-safe cursors.

Tooling posture is excellent and **the mechanical idiomatic-Rust pillar is already
satisfied at the lint level**: the workspace forbids `unsafe`, denies
`clippy::correctness`/`suspicious`/`await_holding_lock`/`undocumented_unsafe_blocks`,
and warns `unwrap_used`/`needless_pass_by_value`/`redundant_closure_for_method_calls`/
`semicolon_if_nothing_returned` — and `clippy --workspace --all-targets` passes
with **zero warnings**. Lock discipline is careful: every `parking_lot` guard is
cloned-out and dropped before any `.await`; the one lock held across an await
(`DaemonClient`'s single-flight) is an async-aware `tokio::sync::Mutex` and is
documented.

| Pillar | Verdict |
|---|---|
| **SRP / cohesion** | Strong. The "big" files (`docker.rs` 782, `daemon_client.rs` 523, `sandbox_manager.rs` 546) are *mechanically cohesive* — one trait impl / one state machine / one lifecycle concern, with mechanical helpers already split into submodules. No real god-objects. |
| **Open/Closed & DIP** | Correct *for this codebase's stance*. Seams sit at genuine substitution boundaries; nothing is over-abstracted. The DIP question here is "are these the right ports and not one more" — and they are. |
| **Interface Segregation** | Good. `ProviderAdapter` is broad (~14 methods) but is one cohesive provider capability with a single concrete impl + mock; splitting it would be speculative. |
| **Generics / bounds** | Appropriate and minimal (`id_in<T: TryFrom<String>>` codec; lifetime-parameterized borrow-only gate inputs). Static dispatch by default; `dyn` reserved for real open sets. |
| **Ownership / borrowing** | Clean. Owned-vs-borrowed choices are deliberate; no clone-in-loop or needless allocation of note. |
| **Error handling** | `thiserror` for domain errors, `anyhow` absent (correct for a library workspace), `?` throughout, `#[non_exhaustive]` enums. A few **context-loss** spots (below). |
| **Naming** | Convention-clean; semantically accurate. One vestigial-vocabulary nit. |

**Severity distribution:** **0 Critical**, **2 Major**, **7 Minor**, **6 Nitpick**.
An empty Critical section is the *correct, expected* outcome here — with `unsafe`
forbidden, a clean compile, and clippy clean, memory-safety Criticals are nearly
impossible by construction. The two Majors are concurrency/resource-lifecycle
defects, not safety violations.

> **Material context (not a defect):** `eos-backend-main/src/main.rs` is an 8-line
> `fn main() {}` stub. The library crates are built and unit/contract-tested in
> isolation, but **nothing yet assembles them into a running server** (config →
> store → `SandboxManager` → runtime → `EventBus` → obs → axum bind → graceful
> shutdown). This is the planned Phase-8 boundary (`implementation_plan.md` marks
> Phase 8 `not_started`), so it's expected — but the review should not be read as
> "this binary runs." It does not, yet.

---

## 2. Critical Issues

**None.** No memory-safety violations, data corruption, deadlocks, panics on valid
input in a production path, or AC violations were found. This is the right result
for a workspace that forbids `unsafe`, compiles cleanly, and passes a
deny-level clippy gate. The two highest-impact issues are resource-lifecycle bugs,
filed as Major below.

---

## 3. Refactoring & Idiomatic Suggestions

### MAJOR — M1. Cancellation racing the *acquire* phase leaks an orphaned Docker container
`crates/eos-backend-runtime/src/launcher.rs:95-144` (with `sandbox_manager.rs:270-300`, `provisioning.rs:91-102`)

The run task is documented as the "sole finalizer" and "cancel-safe at its `.await`
points" — but the `select!` races the cancellation token against the **entire**
`work` future, and `work` *begins* with `manager.acquire`. For a fresh sandbox,
`acquire` provisions **outside the lock** via `provisioner.prepare_for_run` →
`lifecycle.create` → Docker `create_container` + `start_container`, and the
manager records the binding (`sandbox` + `by_request` maps) **only after**
provisioning returns. If the token fires while provisioning is in flight, the
`work` future is dropped with a container **already created in Docker** but no
manager entry — and the reaper's `release(request_id)` is then a no-op (no
`by_request` entry), so the container is never torn down. Net: a leaked container
per cancel-during-provision race.

```rust
// BEFORE — acquire is inside the cancellable `work` future:
let work = async move {
    let binding = match inner.manager.acquire(&run_request_id, /* ... */).await {
        Ok(binding) => binding,
        Err(_) => return Disposition::Failed,
    };
    // ... set Running, host.run ...
};
let disposition = tokio::select! {
    biased;
    () = slot.token.cancelled() => Disposition::Cancelled(slot.reason.lock().clone()),
    disposition = work => disposition,   // <-- token can interrupt acquire mid-provision
};

// AFTER — acquire to completion BEFORE entering the select; race only the run:
let binding = match self.manager.acquire(&request_id, sandbox_id_str).await {
    Ok(b) => b,
    Err(err) => { /* warn */ self.reaper.reap(&request_id, Disposition::Failed).await;
                  self.runs.lock().remove(&request_id); return; }
};
// binding is now recorded; a later cancel reaps -> release -> teardown works.
let run = inner.host.run(request_id.clone(), prompt, binding.sandbox_id, Some(callback));
let disposition = tokio::select! {
    biased;
    () = slot.token.cancelled() => Disposition::Cancelled(slot.reason.lock().clone()),
    outcome = run => match outcome { RunOutcome::Done => Disposition::Done,
                                     RunOutcome::Failed => Disposition::Failed },
};
```

This preserves the sole-finalizer model (exactly one `reap`) while making the
acquire phase atomic w.r.t. cancellation. (If acquire itself must remain
cancellable, the alternative is a drop-guard in the fresh-create path that destroys
a half-provisioned container — more code for the same guarantee.)

### MAJOR — M2. `DaemonClient` per-sandbox maps grow unbounded; never evicted on sandbox delete
`crates/eos-sandbox-host/src/daemon_client.rs:112-116,136-138,438-479` (with `lifecycle.rs` delete path)

`DaemonClient` is a process-lived singleton (built once in
`SandboxManager::with_docker`, shared as the `Arc<dyn SandboxTransport>`). Its
`tcp_cache` and `tcp_locks` are keyed by `SandboxId`. `tcp_locks` entries are
inserted on first endpoint resolution and **removed nowhere**; `tcp_cache` is
pruned only on the connect-failed/empty-response path, **never on sandbox delete**
(`SandboxLifecycle::delete` tears down the container but doesn't touch these maps).
In EphemeralOS — a long-lived backend churning short-lived per-request sandboxes —
both maps accumulate one entry per sandbox ever seen: a slow, unbounded leak
(`tcp_locks` holds an `Arc<tokio::sync::Mutex>` per id forever).

```rust
// ADD a forget hook on DaemonClient:
pub fn forget_sandbox(&self, sandbox_id: &SandboxId) {
    self.tcp_cache.write().remove(sandbox_id);
    self.tcp_locks.write().remove(sandbox_id);
}
// CALL it from SandboxLifecycle::delete after the adapter delete succeeds,
// so eviction is tied to teardown.
```

### MINOR — M3. Epoch-reset cursor records `lost_before_seq = Some(0)` on a zero-boundary reboot
`crates/eos-backend-obs/src/ingestor.rs:199-208`

`max_opt(Some(prior_last), ring_lost)` is **always `Some`** (left operand is
`Some`), and the result is re-wrapped in `Some(...)`, so the epoch-reset branch
makes `lost_before_seq` non-null on **every** reboot — including the degenerate
case `prior_last == 0` with no ring-reported loss. That writes a "loss boundary of
0", which `has_counted_loss` (`seq > 0`) treats as *no* loss while the
`audit_sandboxes_with_loss` count treats any non-null boundary as loss — the two
loss consumers disagree on the same row.

```rust
// BEFORE:
let lost = Some(max_opt(Some(prior_last), ring_lost).unwrap_or(prior_last));
// AFTER — only record a real (>0) boundary, matching has_counted_loss semantics:
let lost = max_opt(Some(prior_last), ring_lost).filter(|&seq| seq > 0);
```

### MINOR — M4. `BackendStore::pool()` widens the public API past the typed-repo contract
`crates/eos-backend-store/src/db.rs:135-139`

The crate doc states the design premise — "one concrete repository per table…
backend-server is the only consumer" — and the typed per-table repos are meant to
be the *only* SQL surface. The `pub fn pool(&self) -> &SqlitePool` getter exposes
the raw pool for a single **test-only** consumer (obs fault-injection). Narrow it
to `pub(crate)` (or remove it and reach the pool via a crate-local test helper) so
the typed repositories stay the only public SQL surface.

### MINOR — M5. Config errors drop the offending file path in a multi-file loader
`crates/eos-backend-config/src/loader.rs:19-23,80-86`

`load_from_paths` folds an ordered file list, but `ConfigError::ReadFile`/
`ParseYaml` carry no path, so a bad `local.yml` surfaces only as "failed to parse
config yaml" with no indication of *which* file — defeating the point of a layered
loader. The path is in scope at both failure sites (`read_yaml(path: &Path)`).

```rust
// Add a `path: PathBuf` to both variants; stop using `#[from]` for ParseYaml
// (it erases context) and attach the path in read_yaml:
let text = std::fs::read_to_string(path)
    .map_err(|e| ConfigError::ReadFile { path: path.to_owned(), source: e })?;
let doc: Value = serde_yaml::from_str(&text)
    .map_err(|e| ConfigError::ParseYaml { path: path.to_owned(), source: e })?;
```

### MINOR — M6. `AgentCoreConfigSource` identity fields are unvalidated while numerics are
`crates/eos-backend-config/src/server.rs:40-44,52-60`

`ServerConfig::validate` range-checks `sandbox`/`obs` but never inspects
`AgentCoreConfigSource`, whose `database_url`/`config_dir` are required-identity
fields. A `local.yml` override of `agent_core: { database_url: "" }` deserializes
cleanly and only fails much later at DB-connect time with an opaque error (the
crate's own test even asserts `database_url` is non-empty). Add a minimal
`AgentCoreConfigSource::validate()` that rejects empty `database_url`/`config_dir`
and call it from `ServerConfig::validate`, matching the existing pattern. Keep it
non-empty-only — no filesystem probing.

### MINOR — M7. `SandboxState` exposes two variants the manager never constructs
`crates/eos-backend-types/src/sandboxes.rs:14-27`

A workspace-wide grep shows `SandboxState::Provisioning` and
`SandboxState::Destroyed` are constructed **nowhere** — the landed
`SandboxManager` only ever sets `Active`/`Ready`/`Retained`/`Destroying`. Because
they're emitted into the **public** `SandboxView` (`GET /api/sandboxes`) and the
JsonSchema/OpenAPI doc, they're speculative *contract* surface, not just dead
internal code — exactly the kind of speculative DTO this codebase's stance says to
drop. Remove both so the public view enumerates only producible states. *(This is
the correct anti-speculation direction: tightening a user-facing contract, not
adding indirection.)*

### MINOR — M8. Unused `thiserror` dependency on a crate that hand-writes its error type
`crates/eos-backend-api/Cargo.toml:18`

`ApiError` is a hand-written enum with explicit `From` impls that log-and-collapse
(deliberate, for the AC4 sanitization contract). No `thiserror` usage exists
anywhere in `eos-backend-api/{src,tests}` — drop `thiserror.workspace = true`. The
hand-written error is the right shape; just remove the dead crate edge.

### MINOR (own, not in agent set) — M9. A failed terminal `set_status` in the reaper is not retried
`crates/eos-backend-runtime/src/reaper.rs:68-83`

`reap` releases the sandbox first (good — teardown survives a run-meta write
failure), then writes the terminal `run_meta` status; a failure is logged but not
retried. For `Done`/`Failed` the detail handler's CAS `reconcile` recovers the
status from agent-core on the next GET, so the gap self-heals. For **`Cancelled`**
(backend-local, never in agent-core state) there is no recovery path: a failed
cancel-reap write leaves the run reading `running` indefinitely. Low-probability on
local SQLite, but worth a bounded retry or a reconcile fallback for the cancelled
case.

---

## 4. Nitpicks / Naming

- **N1 — `event_log` append retry can stall the seq space (nitpick, theoretical).**
  `event_bus.rs:305-310`: `append_with_retry` retries the *identical* `record`
  (same `seq`). In the narrow committed-but-errored case, the retry collides on
  `PRIMARY KEY (request_id, seq)`, returns `Err`, the seq doesn't advance, and a
  spurious gap is armed (the record is actually durable, so subscribers still get
  it via replay). Effectively impossible on local SQLite (no committed-but-unacked
  window). Either drop the retry (the pool busy-timeout already absorbs
  `SQLITE_BUSY`) or treat a PK-collision-on-retry as success. *(Note: the sibling
  `obs_event` retry in `sink.rs`/`ingestor.rs` is the opposite — autoincrement id,
  so a retry duplicates rather than collides; same theoretical window.)*

- **N2 — `contains_tool_use_id` linear-scans a `BTreeSet` (nitpick).**
  `gates.rs:413-415`: `observed_tool_use_ids` is a `BTreeSet<&str>` but membership
  uses `iter().any()` (O(n)), defeating the ordered set. The helper is also
  redundant — call `ids.contains(needle)` directly (O(log n)). Cold path, tiny set;
  pure idiom. *(Also: `gates.rs` is the one crate file missing the `//!`
  module-doc its siblings all have.)*

- **N3 — OpenAPI omits the 404/409 the delete handlers return (nitpick).**
  `openapi.rs:89-92,159-162`: the two delete operations document only `202`, but
  `cancel`/`sandboxes::delete` return `404` (unknown) and `409` (refused while
  active/retained). Add description-only entries (no schema body needed — the
  `ApiError` envelope is uniform).

- **N4 — `ProviderRegistry` vestigial "default" vocabulary (nitpick, naming).**
  `registry.rs:46,67`, `error.rs:15`: the registry holds exactly one slot in a
  Docker-only model, so "default" (which implies a fallback among many) misleads.
  `set_default` → `seed`/`set_adapter` (matching its own doc verb and the
  `adapter()` getter); `NoDefaultProvider` → `NoProvider`. Single production call
  site.

- **N5 — Unchecked cross-crate rustdoc link (nitpick, docs).**
  `types/stats.rs:62-68`: `[PersistingSink]: ../../eos_backend_obs/struct.PersistingSink.html`
  is a hand-written HTML path to a non-dependency crate (correctly — `types` is a
  leaf below `obs`), so rustdoc can't validate it and it rots silently on rename.
  Refer to it in plain prose instead; do **not** add an `obs` dependency.

- **N6 — `x < 1` reads less clearly than `== 0` on unsigned (nitpick).**
  `config/sandbox.rs:30,36`, `config/obs.rs:28,34`: all four fields are
  `usize`/`u64`, so `< 1` is exactly `== 0`; the intent is "reject zero". Use
  `== 0` to match the `must be >= 1` detail strings. Optional.

- **N7 (own) — `parse_sandbox_id` fabricates a random UUID on parse failure (nitpick).**
  `docker.rs:773-778`: a malformed Docker id silently becomes a freshly-minted
  `SandboxId::new_v4()` rather than an error. Documented as unreachable from a real
  daemon, but "fabricate an identity on parse failure" is a surprising fallback for
  an id type; consider surfacing it as an error or an explicit sentinel.

### Considered and intentionally **not** filed (false positives / by-design)

- **Adding traits/generics "for extensibility"** — rejected wholesale; the codebase
  deliberately avoids speculative seams, and the existing ones are correctly placed.
- **`docker.rs` "mixes adapter I/O with serialization helpers"** — rejected: the
  pure helpers are already a clearly-delimited `pub(crate)` block for unit tests;
  one cohesive concept, not real concept-mixing.
- **`put_archive` "copies the multi-MB tar buffer"** — rejected as factually wrong:
  the `Bytes::copy_from_slice` is in the *non*-tmpfs branch, and both production
  callers upload under `/eos` (the tmpfs branch, which streams via exec stdin).
- **`signed_preview_url`/`build_logs_url` dead trait surface** — borderline; left
  as-is. The methods are part of the sealed `ProviderAdapter` contract with a
  documented future-provider rationale; removing them is a judgment call, not a
  clear defect.
- **`BackendRunStatus` vs `ApiRunStatus` 1:1 identical enums** — justified as
  distinct vocabularies (persisted backend state vs API-resolved join); the
  precedence table in `status.rs` is the seam where they'll diverge.
- **Config dir pinned to compile-time `CARGO_MANIFEST_DIR`** (`loader.rs:72-77`) —
  a deployment caveat (no runtime override), but a deliberate choice mirroring the
  agent-core/sandbox config model.
