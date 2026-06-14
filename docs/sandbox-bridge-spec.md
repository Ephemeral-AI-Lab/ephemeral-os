# eos-sandbox Bridge — Implementation Spec & Progress Tracker

The execution plan for connecting `eos-coding-agent` to `eos-sandbox` and the
sandbox-side improvements that follow. This is a **living tracker**: check boxes
as work lands. The *what/why* per ticket is `docs/sandbox-bridge-issues.md`; the
*assessment* is `docs/sandbox-bridge-findings.md`; the *manual* is
`how-to-connect.md`.

> Update rule: a phase is **Done** only when every box in it — including its
> verification gate — is checked. Edit the dashboard counts when you tick items.

## Goal & non-goals

**Goal.** A working, auditable, typed bridge: the coding agent acquires a sandbox
per run, drives files/commands/isolated-workspaces over the gateway socket, gets
a durable correlatable audit trail, and consumes a published, drift-gated type
contract.

**Non-goals.** No change to the host/box isolation law (no shared compiled code).
No new transport (stay newline-JSON-over-UDS). No automatic audit-chain sealing
(needs key management — out of scope). No revival of legacy `api.*` aliases.

## Progress dashboard

| Track | Items | Done | Status |
|---|---|---|---|
| Phase 0 · Docs | 2 | 0/2 | ☐ not started |
| Phase 1 · TS bridge | 6 | 0/6 | ☐ not started |
| Phase 2 · Auditability | 5 | 0/5 | ☐ not started |
| Phase 3 · Typed contract | 5 | 0/5 | ☐ not started |
| **Total** | **18** | **0/18** | — |

Legend: ☐ not started · ◐ in progress · ☑ done.

## Invariants (apply to every change)

- [ ] No crate dependency crosses the host/box boundary (gateway/host ↔ daemon/eosd). New contract surface is **data** in `ops.json`/`op_schemas.json`, gated by `check-contract`.
- [ ] `ops.json` is never hand-edited — edit `catalog.rs`, then `cargo run -p eosd -- dump-ops > crates/operation/ops.json` + `cargo run -p xtask -- gen-docs`.
- [ ] Golden fixtures under `contract/fixtures/` stay immutable; a change that would alter a fixture is wrong (or needs an explicit `CONTRACT.md` version bump).
- [ ] Sandbox-side commands run from `eos-sandbox/`; TS-side from `eos-coding-agent/`.

---

## Phase 0 — Documentation (no code risk; do first)

**DOC-1 — envelope-nesting rule**
- [ ] `contract/PROTOCOL.md` §4: branch envelope `status` first, then `result.status` for command/file ops; list both status vocabularies.
- [ ] Confirm `how-to-connect.md` carries the rule + a running-command / `command_not_found` example.

**TS-DTO-6(A) — version reconciliation (doc + rename)**
- [ ] `CONTRACT.md`: document three version surfaces (wire=1 / catalog=1 / envelope=2) and which governs what.
- [ ] Rename `meta.protocol_version` → `envelope_version` across `envelope.rs`, `gateway.rs`, `daemon/src/trace/envelope_meta.rs`, `PROTOCOL.md`, envelope tests.
- [ ] Fix the stale `daemon/src/wire/message.rs:8-10` comment.

**Gate 0**
- [ ] `cargo run -p xtask -- check-contract`
- [ ] `cargo test -p operation -p gateway -p daemon`

---

## Phase 1 — TS bridge (Track `bridge`; sandbox unchanged; strictly ordered)

**BR-1 — `SandboxGatewayClient`**
- [ ] `gateway-client.ts` over `node:net` `createConnection({ path })` (Unix socket).
- [ ] one-line `{op, sandbox_id, invocation_id, args}` + `\n`; read to `\n`/EOF; fresh connection per call.
- [ ] uuid4-hex `invocation_id` default; Zod `GatewayResponse` discriminated union; per-op result schemas at the edge.
- [ ] `sandbox_id` required on the request type.
- [ ] gate: `pnpm run typecheck && pnpm run lint && pnpm run test`.

**BR-2 — DI seam** *(needs BR-1)*
- [ ] `sandboxTools(client, sandboxId)`; threaded through `buildAgentFactory → selectOrdinaryTools`.
- [ ] one process-level client in `bootstrap.ts`; `invocation_id` from `ctx.toolUseId`.
- [ ] gate: `pnpm run typecheck && pnpm run lint && pnpm run test`.

**BR-5 — tool→op adapters** *(needs BR-1, BR-2)*
- [ ] arg adapters: edit→`edits[]`; exec `timeout_ms`→seconds; `cwd`→`cd … &&` (not dropped); stdin→`chars`; transcript→`last_n_lines`; `read` offset/limit client-side; `multi_read`=N× read.
- [ ] response adapter: envelope-`status` first, then command `result.status`.
- [ ] `sandbox_id` + `caller_id` on every call.
- [ ] gate: `pnpm run check` + per-adapter unit tests.

**BR-3 — lifecycle binding** *(needs BR-1, BR-2)*
- [ ] acquire on run start at `pursuit/service.ts:324-341`; stash `sandbox_id`.
- [ ] `run.end` (caller_id == agent_run_id) + `release` on settle/fail/interrupt.
- [ ] caller_id granularity for multi-run pursuits decided & documented.
- [ ] gate: `pnpm run check` + live acquire→write→exec→poll→release smoke.

**BR-6 — abort + connect resilience** *(needs BR-1)*
- [ ] `ctx.signal`→`socket.destroy()` + reject; bounded connect timeout + `ECONNREFUSED` backoff.
- [ ] `sandbox_unavailable` retryable; `uncertain_outcome` terminal.
- [ ] gate: `pnpm run test` (abort + refused cases).

**BR-4 — op-name codegen** *(needs BR-1; optional)*
- [ ] build step emits `sandbox-ops.generated.ts` from `ops.json`; client imports it.
- [ ] freshness gate (if any) is a deliberate cross-tree decision.
- [ ] gate: `pnpm run typecheck`.

**Phase 1 exit**
- [ ] All 7 tools live (no `NOT_WIRED`); one full run drives a real sandbox end-to-end; `pnpm run check` green.

---

## Phase 2 — Auditability (Track `audit`; AUD-3 → AUD-1 → AUD-2 first)

**AUD-3 — timed background drain** *(valid as-is)*
- [ ] periodic host thread in `SandboxHost::open` over the registry.
- [ ] `resolve_endpoint` before `schedule` when endpoint is `None`; reuse single-flight/coalesce.
- [ ] gate: `cargo test -p host --all-targets`.

**AUD-1 — `sandbox.audit` readback op**
- [ ] `BuiltinOp` `sandbox.audit` (2-segment host grammar, operator, non-mutating) in `catalog.rs`; regenerate `ops.json` + docs.
- [ ] `HostVerb::Audit` + `Engine::audit` + `SandboxHost::audit` over private `trace_store`; both test `Engine` impls updated.
- [ ] args `{trace_id?|request_id?, since_seq?, limit?}` → `{request, events[], event_count}`; no `chain_verified`.
- [ ] gate: `cargo run -p xtask -- check-contract` + `cargo test -p gateway -p host`.

**AUD-2 — authoritative `meta.trace` receipt**
- [ ] daemon-forward path: overwrite `meta.trace.event_count` from `event_count_for_trace` after all host events appended.
- [ ] host-served verbs left as honest `pending_host_ingest`/`0`.
- [ ] update the host unit test (was asserting the daemon-embedded count).
- [ ] gate: `cargo test -p host --all-targets`.

**AUD-5 — `sandbox.audit.verify` op**
- [ ] read-only op (operator) → `{ok, entries_checked, errors, pruned_ranges}` over `verify_chain`; `Engine` method + test impls; regenerate `ops.json`.
- [ ] no automatic sealing here.
- [ ] gate: `cargo run -p xtask -- check-contract` + `cargo test -p gateway -p host`.

**AUD-4 — per-request sidecar retry** *(shares AUD-3 timer)*
- [ ] ingest-error branch appends bounded `pending_sidecar` (decoded bytes + ids) via `append_pending_sidecar`; decode-failure branch unchanged.
- [ ] host-local recovery pass re-feeds `ingest_trace_batch`, flips `store→local_sqlite`, deletes row on success.
- [ ] gate: `cargo test -p host --all-targets`.

**Phase 2 exit**
- [ ] orchestrator can read its own trail (`sandbox.audit`) and trust `meta.trace`; idle sandboxes drain.
- [ ] gate: `cargo run -p e2e-test --bin e2e-runner -- --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4`.

---

## Phase 3 — Typed contract (Track `transport`)

**TS-DTO-6(B) — optional skew guard** *(continues Phase 0 item)*
- [ ] daemon compares `_eos_daemon_protocol_version` against its own added version constant; rejects on major mismatch + fixture.
- [ ] gate: `cargo test -p daemon` + `check-contract`.

**TS-DTO-2 — `fault_kinds` data set**
- [ ] `fault_kinds` array rendered into `ops.json` (daemon + gateway + domain kinds; exclude `aborted_*`).
- [ ] no shared enum across the boundary; `check-contract` asserts coverage; `PROTOCOL.md` documents it.
- [ ] gate: `cargo run -p xtask -- check-contract` + `cargo test -p operation -p gateway`.

**TS-DTO-5 — paging**
- [ ] file read: additive `offset`/`limit` → `{content, next_offset?, eof}`.
- [ ] command poll: additive `since_offset` → `{chunk, next_offset, complete}` via existing `read_output_since`.
- [ ] new fixtures for the windowed shapes.
- [ ] gate: `cargo test -p operation -p daemon`.

**TS-DTO-3 — declarative serde cleanup**
- [ ] replace `timings`-strip/flatten loops with a wire DTO using `#[serde(skip)]`; drop `files.rs` `object.remove("timings")`.
- [ ] envelope status arm unchanged; net-negative LOC.
- [ ] gate: `cargo test -p operation -p daemon` (conflict fixture green).

**TS-DTO-1 — published JSON Schemas** *(largest; has a prerequisite)*
- [ ] **PRE:** convert each `parse()` to real serde `Deserialize` (`alias/rename/default`), fixture-pinned.
- [ ] `schemars` derive + dedicated wire-result DTOs; `op_schemas.json` via `eosd dump-op-schemas`; `check-contract` gate.
- [ ] BR-1 client codegens types from `op_schemas.json`; LLM tool schemas stay distinct.
- [ ] gate: `cargo test -p operation --all-targets` + `check-contract`; (TS) `pnpm run check`.

**TS-DTO-6(C) — stamp catalog version into schema artifact** *(after TS-DTO-1)*
- [ ] catalog `protocol_version` stamped into `op_schemas.json`.

**Phase 3 exit**
- [ ] TS consumes a published, drift-gated type + error contract; e2e assertions migrated to `meta`/`status`/`result`.

---

## Definition of done

- [ ] Phases 0–1 complete: the coding agent runs real sandbox work end-to-end with no stubs.
- [ ] Phase 2 complete: every tool call yields a durable, server-correlatable audit record reachable over the operator socket.
- [ ] Phase 3 complete: the TS side generates its types and error union from drift-gated sandbox artifacts.
- [ ] All gates green; `cargo run -p xtask -- check-contract` and `cargo run -p e2e-test --bin e2e-runner -- --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` pass; `pnpm run check` passes in `eos-coding-agent/`.
- [ ] `docs/sandbox-bridge-issues.md` items all closed or explicitly deferred; this dashboard reads 18/18.
