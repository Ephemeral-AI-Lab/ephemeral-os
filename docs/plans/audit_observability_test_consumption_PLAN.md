# Audit / Observability redesign — built for a cross-workspace test runner

## Context

Rust (`agent-core/`, `sandbox/`) is the primary implementation; `backend/src` Python is
legacy. The **primary consumer** of the audit/observability layer is a (future)
**test runner** that runs scenarios against sandbox, agent-core, and both together, and
extracts four validation signals: **tool use, performance stats, resource usage,
message correctness**. So the design goal is a *clean, coherent, easily-accessible
consumption contract*, not a richer audit framework.

What already exists (reuse, don't reinvent):
- **sandbox** has the read surface: `api.audit.pull` / `api.audit.snapshot` drains the
  in-memory ring (schema `sandbox.daemon.audit.pull.v1`, counted-loss). The bench
  harness already turns this into `bench/*.sandbox_events.jsonl` +
  `*.performance_report.json`, whose `audit` block (`event_type_counts`, `drop_free`,
  `missing_required_event_types`, buffer stats) IS the validation contract.
- Related prior planning: `docs/plans/test_runner_rust_SPEC.md`,
  `docs/plans/test_runner_migration_PLAN.md`,
  `docs/plans/daemon-audit-pull-consolidation-*.md` — align with these.

Gaps this plan closes:
- **agent-core has no symmetric consumable stream.**
- **resource usage** is unwired: `OsResourceSection` is schema-defined but never emitted.
- **no unified cross-workspace reader / correlation join.**
- audit conflates a durable record with diagnostics; carries dead code (`AuditEventBus`)
  and sandbox `json!` drift.

**This plan delivers the access contract + the missing complete-capture signals. It does
NOT build the test runner** (that is the user's separate, future work).

## Core principle: route by *consumer*, with completeness where it's asserted

The runner asserts `drop_free: true` and `missing_required_event_types: []` as PASS
criteria — so **anything it validates on must be on a complete-capture surface**, never
a may-drop one. That yields three surfaces, each owned by a consumer:

| Surface | Guarantee | Consumer | Carries |
|---|---|---|---|
| **Authoritative state** (Task rows, `ToolResult`, conversation transcript) | complete by construction | runner: **tool use** + **message correctness** | the facts themselves (redaction-free) |
| **Complete-capture obs stream** (sandbox ring pull; agent-core obs JSONL) | complete / counted-loss, `drop_free` is a property | runner: **performance** + **resource** + durable records | event-only signals (timings, RSS/cpu/io) + records (plugin, isolated) |
| **Diagnostics** (`tracing`) | best-effort, may-drop | humans only | pure shadows reconstructable from state |

Key consequences (these resolve the earlier "shadow→tracing" tension):
- **tool use** and **message correctness** are read from authoritative state, which is
  complete by construction — so the shadow events (`workflow.task.*`, subagent
  `background_tool.*`, `engine.tool.*`) can safely become **tracing diagnostics**
  (approved) without weakening validation; the runner never depends on them.
- **performance** and **resource** are the only genuinely event-only signals → they get
  a **complete-capture** surface (ring + agent-core obs JSONL), never tracing.
- **message correctness** uses the existing transcript/state (`parity/prompt_report/
  session_golden.jsonl` pattern); audit stays redacted (shape + `sha256` digest); the
  obs stream keeps only digests for cross-run regression checks.

## The consumption contract (the deliverable)

1. **Two read surfaces, same spirit:**
   - **sandbox:** keep `api.audit.pull` / `snapshot` (schema `pull.v1`) verbatim — the
     bench harness already consumes it. Type the ring payloads; wire the missing
     resource events (below). Wire shape unchanged.
   - **agent-core:** add a symmetric **obs JSONL events file** (via the existing
     `BufferedJsonlSink` to a configured path), captured **completely in test mode**,
     mirroring `sandbox_events.jsonl`. Reserve an in-process collecting layer only for
     unit tests.
2. **Reader-side categorization (NOT a wire field):** the runner maps
   `event_type → category {Tool, Perf, Resource, Lifecycle}` and **does not** add a
   `category` enum to the envelopes (and must not bump the frozen `pull.v1` schema).
3. **Correlation join:** both surfaces already carry `request_id` / `agent_id` /
   `tool_use_id` (e.g. `eos-protocol::audit` sections, agent-core `AuditNode`). The
   reader joins on these to stitch a "both together" run.
4. **Report shape:** extend the existing `performance_report.json` `audit` block
   (`event_count`, `event_type_counts`, `drop_free`, `missing_required_event_types`,
   buffer stats) to also describe an **agent-core source** — do not redesign it.

A short doc (`docs/architecture/observability` or a `CONTRACT.md` section) specifies:
the obs JSONL line shape, the per-surface read mechanism, the `event_type→category`
map, the correlation keys, and which categories come from state vs the obs stream.

## What must be added to make perf + resource accessible

- **sandbox resource:** wire `OsResourceSection` emission (periodic `os_resource.*`
  sampling: rss/cpu/io) onto the ring (`Lane::Sample`). It is schema-ready and unwired.
- **agent-core perf:** emit a small, explicit set of **complete-capture** perf events
  to the obs JSONL — per-tool-call and per-agent-run durations — as typed records
  (NOT tracing). Source timings at the engine/dispatch boundary.
- **agent-core resource:** a periodic process resource sample (RSS/CPU) emitted as a
  typed obs record.
- **tool use / message correctness:** no new emission — documented as
  read-from-authoritative-state (Task/`ToolResult`/transcript). Sandbox tool-use also
  remains countable from the ring (`event_type_counts`), which is complete.

## Clean-ups (retained from the approved plan, but subordinate to the contract)

- **agent-core `eos-audit` trait redesign:** `pub trait AuditEvent: Serialize { const
  EVENT_TYPE; const SOURCE }`, rename `struct AuditEvent → AuditEnvelope`, add
  `AuditCtx` (explicit correlation; never `Span::current()`), `Audit::emit<E>` handle.
  This now carries records **and** the perf/resource obs events — all to the obs JSONL.
  **No `category` wire field** (reader-side). Keep eos-audit a `{eos-types}` leaf.
- **Delete `AuditEventBus`** (never constructed in prod).
- **Move shadow events to tracing** (`workflow.task.*`, subagent `background_tool.*`;
  `engine.tool.*` dead code deleted) — they become human diagnostics; `eos-engine` /
  `eos-workflow` drop their `eos-audit` dep.
- **sandbox `dispatcher.rs` json! → typed `*Section`** (kills the drift; behavior-
  preserving, matches `server.rs:408`).
- **Relocate `PluginSection` + `plugin.*` event structs** to `eos-plugin-catalog`
  (staged; `plugin.*` stays unwired until plugin *execution* is ported — no dispatch
  site exists in Rust yet).
- **Guard updates:** `agent-core/parity/tests/dependency_dag.rs` frozen edge set (fewer
  `eos-audit` dependents); `no_downstream_deps.rs` stays `{eos-types}`.

## Per-area change map

- `agent-core/crates/eos-audit/`: `event.rs` (trait + `AuditEnvelope`), new `ctx.rs`,
  new `handle.rs`, `sink.rs`/`jsonl.rs` take `&AuditEnvelope`, `lib.rs` re-exports;
  delete `bus.rs`/`engine_stream.rs`; move out `plugin.rs`. Add typed `PerfSample` /
  `ResourceSample` obs-event structs (in the emitting crate, impl `AuditEvent`).
- `agent-core/crates/eos-engine`, `eos-workflow`: shadow events → `tracing`; drop
  `eos-audit` dep; emit per-tool/per-agent-run perf records to the obs sink at the
  dispatch/stage boundary; periodic resource sample.
- `agent-core/crates/eos-runtime`: build the `Audit` handle + obs JSONL path
  (`app_state.rs:404-413`, `entry.rs`); `observability.rs` already inits tracing —
  document the `target:"audit"` tee + a test-mode "capture everything" config.
- `sandbox/crates/eos-daemon/dispatcher.rs`: typed sections for the emit helpers; wire
  `os_resource.*` sampling. `eos-protocol/audit.rs`: sections already exist.
- `sandbox/crates/eos-isolated`: type the isolated JSONL events (keep `"published":
  false`); these remain durable records on the complete surface.
- `docs/`: the observability consumption contract doc.

## Staged execution (each stage builds + tests green; no runner built)

1. **eos-audit foundation:** trait + `AuditEnvelope` + `AuditCtx` + `Audit::emit<E>`;
   delete `bus.rs`; move `PluginSection`. *Verify:* `cargo build/test -p eos-audit
   -p eos-plugin-catalog`, `no_downstream_deps.rs`.
2. **Shadow → tracing + dep drop:** migrate `eos-workflow` then `eos-engine`; update
   tests (`tracing-test`); update `dependency_dag.rs`. *Verify:* `cargo test` both
   crates + parity DAG guard.
3. **agent-core obs surface + perf/resource events:** wire the obs JSONL path; add the
   perf + resource typed events; test-mode complete-capture config. *Verify:* a JSONL
   appears with the perf/resource events under a test run.
4. **sandbox typing + resource:** `dispatcher.rs` json!→typed sections; wire
   `os_resource.*`. *Verify:* `cargo test -p eos-daemon` (byte-identical existing
   events; new `os_resource.*` present), `api.audit.pull` still well-formed.
5. **sandbox isolated typing.** *Verify:* `cargo test -p eos-isolated`.
6. **Contract doc + correlation/category map** (the reader spec the future runner uses).

Stages 1–3 (agent-core) and 4–5 (sandbox) are independent and parallelizable.

## Verification

- Per workspace: `cargo build` / `cargo test` / `cargo clippy`.
- Guards: `no_downstream_deps.rs`, `dependency_dag.rs` (updated).
- **Consumption smoke test (no runner):** a test that (a) runs an agent-core scenario
  and reads the obs JSONL back, asserting perf + resource events present and
  `drop_free`; (b) pulls the sandbox ring via `api.audit.pull` and asserts
  `os_resource.*` present and `missing_required_event_types: []`. This proves the
  access contract end-to-end without building the runner.
- Byte-exact: sandbox typed sections must serialize identically to the prior `json!`.

## Decisions already made

- Full trait redesign of agent-core audit API. ✓
- Shadow events → tracing diagnostics (safe because tool-use/correctness validate from
  authoritative state). ✓
- Message correctness validates from the transcript / persisted state; audit stays
  redacted (digests only in obs). ✓
- Categorization is reader-side; agent-core obs surface is a JSONL file mirroring the
  sandbox pattern; reuse the `performance_report` `audit` block shape. ✓

## Open items to confirm

- **Agent-core perf granularity:** the plan adds per-tool-call + per-agent-run duration
  events as the minimal complete-capture perf set. Finer (per-stage, per-LLM-call) or
  coarser is a knob.
- **`plugin.*` stays staged/unwired** until plugin *execution* is ported to Rust (no
  dispatch site exists yet); the record infra is ready and lights up when it lands.

## Risks / call-outs

- **Don't build the runner here** — deliver the surfaces + contract; the smoke test is
  the proof, not a full runner.
- **Completeness is the load-bearing property:** never route a runner-validated event
  through `tracing` (EnvFilter / `STATIC_MAX_LEVEL` silent drop). Perf/resource stay on
  the complete obs surface.
- **No `category` on the wire** (esp. not `pull.v1`) — reader-side map only.
- **Byte-exact sandbox parity** for the `json!`→section refactor; `os_resource.*` is the
  only intentional new sandbox emission.
- **Cross-workspace correlation** depends on `request_id`/`agent_id`/`tool_use_id`
  actually flowing agent-core→sandbox at call time — verify during stage 6, since the
  "both together" join relies on it.
- **Parallel agents / dirty worktree:** `dispatcher.rs` has an unrelated in-flight OCC
  change; scope edits to the audit emit helpers.
