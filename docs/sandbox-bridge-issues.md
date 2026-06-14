# eos-sandbox Bridge — Issue Findings

The §4/§6 findings of `docs/sandbox-bridge-findings.md`, broken into discrete,
trackable issues. Each issue is standalone: it has a problem statement,
checkable acceptance criteria, the files it touches, its dependencies, and a
verification command. IDs match the findings report and `how-to-connect.md`.

- **Tracks:** `bridge` (TS-side, unblocked, no sandbox change) · `audit`
  (sandbox-side) · `transport` (sandbox-side) · `doc`.
- **Severity:** Blocker · High · Medium · Low.
- These are **markdown issues**, not GitHub issues. To file them on a remote,
  ask and I'll convert this registry to `gh issue create` calls.
- Live progress is tracked in `docs/sandbox-bridge-spec.md`.

## Backlog

| ID | Title | Track | Sev | E/R | Depends on | State |
|---|---|---|---|---|---|---|
| DOC-1 | Document the envelope-nesting rule | doc | Medium | S/low | — | open |
| BR-1 | `SandboxGatewayClient` (UDS, one-line req/resp) | bridge | Blocker | M/low | — | open |
| BR-2 | DI seam: inject client + per-run `sandboxId` | bridge | Blocker | M/low | BR-1 | open |
| BR-3 | Bind acquire/release/run.end to the run boundary | bridge | High | M/med | BR-1, BR-2 | open |
| BR-4 | Codegen typed TS op-contract from `ops.json` | bridge | Low | S/low | BR-1 | open |
| BR-5 | Tool→op arg/response adapters in `execute()` | bridge | High | M/low | BR-1, BR-2 | open |
| BR-6 | Wire `ctx.signal` + connect retry/timeout | bridge | Medium | S/low | BR-1 | open |
| AUD-1 | Host-served `sandbox.audit` readback op | audit | High | M/med | — | open |
| AUD-2 | Server-authoritative `meta.trace` receipt | audit | Medium | S/low | — | open |
| AUD-3 | Timed background trace drain | audit | High | M/med | — | open |
| AUD-4 | Retry per-request sidecar on ingest failure | audit | Medium | M/med | (shares AUD-3 timer) | open |
| AUD-5 | Host-served `sandbox.audit.verify` op | audit | Medium | M/low | — | open |
| TS-DTO-1 | Publish per-op arg/result JSON Schemas | transport | Medium | L→XL/med-high | TS-DTO-1-PRE | open |
| TS-DTO-2 | Publish closed `fault_kinds` set (as data) | transport | Medium | M/med | — | open |
| TS-DTO-3 | Declarative serde for command/file shaping | transport | Low | M/med | — | open |
| TS-DTO-5 | Byte-range file read + command-poll cursor | transport | Medium | M/S | — | open |
| TS-DTO-6 | Reconcile the two `protocol_version`s | transport | Low | S/low | — | open |
| BR-7 | Config field for gateway socket path | bridge | — | — | — | **wontfix** |
| TS-DTO-4 | Share one envelope DTO host↔box | transport | — | — | — | **wontfix** |

---

## DOC-1 — Document the envelope-nesting rule
`doc` · **Medium** · S/low · depends: —

**Problem.** Every daemon response is an `OperationEnvelope` whose top-level
`status` is the *transport* outcome (almost always `ok`), while the *domain*
status for command/file ops is nested at `result.status`. A running command — and
even `command_not_found` — surface as envelope `ok`. This is undocumented, so a
naive client mis-parses every command.

**Acceptance criteria.**
- [ ] `contract/PROTOCOL.md` §4 states: branch on envelope `status` first, then
      `result.status` for command/file ops; lists command `result.status` values
      (`running|ok|cancelled|error|timed_out`) and mutation values
      (`accepted|committed|rejected|aborted_version|aborted_overlap|dropped|failed`).
- [ ] `how-to-connect.md` carries the same rule (already present — verify it stays).
- [ ] An example running-command and a `command_not_found` envelope are shown.

**Touch.** `contract/PROTOCOL.md`, `how-to-connect.md`.
**Verify.** `cargo run -p xtask -- check-contract` (fixtures unchanged).

---

## BR-1 — `SandboxGatewayClient` (UDS, one-line req/resp)
`bridge` · **Blocker** · M/low · depends: — · verdict: refine

**Problem.** No socket client exists in TS; all 7 tools are stubs. The SDK ships
no socket helper, so the gateway client is greenfield.

**Acceptance criteria.**
- [ ] New `src/tools/sandbox/gateway-client.ts` opens `node:net` `createConnection({ path })` (Unix socket, **not** host/port).
- [ ] Writes one compact-JSON line `{op, sandbox_id, invocation_id, args}` + `\n`; reads to the single `\n`/EOF; fresh connection per call.
- [ ] Mints `invocation_id` as uuid4 hex when not supplied.
- [ ] Parses into a Zod `GatewayResponse` discriminated union (`ok|running|cancelled|timed_out` carry `result`; `rejected|error` carry `error`); per-op result schemas live at this edge, not `src/contracts`.
- [ ] `sandbox_id` is a **required** field on the request type.

**Touch.** `eos-coding-agent/src/tools/sandbox/gateway-client.ts`.
**Verify.** `pnpm run typecheck && pnpm run lint && pnpm run test`.

---

## BR-2 — DI seam: inject client + per-run `sandboxId`
`bridge` · **Blocker** · M/low · depends: BR-1 · verdict: refine

**Problem.** `sandboxTools()` takes no args and `ToolCallContext` has no sandbox
handle, so there is no seam to supply a client or a run-scoped `sandbox_id`.

**Acceptance criteria.**
- [ ] Signature `sandboxTools(client: SandboxGatewayClient, sandboxId: () => string)`.
- [ ] `client` + `sandboxId` threaded through `buildAgentFactory → selectOrdinaryTools` (mirroring the `readAgentRun(recordsDir)` / `runSubagent(...)` closure precedents).
- [ ] One process-level client constructed in `bootstrap.ts`.
- [ ] Each tool sources `invocation_id` from `ctx.toolUseId` (no new context field).

**Touch.** `src/tools/sandbox/index.ts`, `src/agents/agent-factory.ts`, `src/bootstrap.ts`.
**Verify.** `pnpm run typecheck && pnpm run lint && pnpm run test`.

---

## BR-3 — Bind acquire/release/run.end to the run boundary
`bridge` · **High** · M/med · depends: BR-1, BR-2 · verdict: refine

**Problem.** Nothing calls `sandbox.acquire`/`release`; the pursuit lifecycle has
no sandbox concept, so no owner mints/cleans up the per-run `sandbox_id`.

**Acceptance criteria.**
- [ ] On run start at `src/workflows/pursuit/service.ts:324-341` (the `.create(...).start(...)` site), `client.acquire()` → stash `sandbox_id` as BR-2's accessor.
- [ ] On settle / failure / interrupt (the `.outcome()` + abort listener paths): `sandbox.run.end` with `caller_id == agent_run_id`, then `sandbox.release(sandbox_id)`.
- [ ] caller_id granularity for multi-run pursuits is decided and documented (which run owns acquire/release vs. shares the sandbox). *(See open decision in findings §8.3.)*
- [ ] No sandbox-side change.

**Touch.** `src/workflows/pursuit/service.ts` (+ wherever the `sandboxId` accessor lives).
**Verify.** `pnpm run check`; manual acquire→…→release smoke against a live gateway.

---

## BR-4 — Codegen typed TS op-contract from `ops.json`
`bridge` · **Low** · S/low · depends: BR-1 · verdict: refine (defer)

**Problem.** Op-name strings would be hand-typed in the client. `ops.json` is the
drift-gated source of truth but the TS side has no codegen against it. *No
duplication exists today — do this only after BR-1 introduces op-name strings.*

**Acceptance criteria.**
- [ ] A build step in eos-coding-agent reads `eos-sandbox/crates/operation/ops.json` and emits `src/tools/sandbox/sandbox-ops.generated.ts` (a `SANDBOX_OPS` map, a public-op-name union, per-op `mutates_state`).
- [ ] BR-1's client imports the generated names instead of literals.
- [ ] No new Rust artifact. Any `xtask check-contract` freshness check is a separate, deliberate cross-tree decision (xtask's workspace root is `eos-sandbox/`).

**Touch.** `eos-coding-agent` build + `src/tools/sandbox/sandbox-ops.generated.ts`.
**Verify.** `pnpm run typecheck`; regeneration is idempotent.

---

## BR-5 — Tool→op arg/response adapters in `execute()`
`bridge` · **High** · M/low · depends: BR-1, BR-2 · verdict: refine

**Problem.** Stub Zod arg names don't match the daemon wire, and command/file
responses nest the domain status (DOC-1).

**Acceptance criteria (arg adapters).**
- [ ] `edit` → `edits:[{old_text:old_string, new_text:new_string, replace_all}]`.
- [ ] `exec_command` → `cmd`; `timeout_ms`→`timeout` in **seconds** (`ceil/1000`); `cwd` folded as `cd <cwd> && <command>` (**never silently dropped**).
- [ ] `command_stdin` → `chars`; `read_command_transcript` → `sandbox.command.poll` with `last_n_lines`.
- [ ] `read` offset/limit applied client-side; `multi_read` = N× `sandbox.file.read`.
- [ ] `sandbox_id` threaded on every call; `caller_id` = agent_run_id.

**Acceptance criteria (response adapter, envelope-first).**
- [ ] Branch envelope `status` first: `ok|running`→read `result`; `rejected|error`→`{error}`; `cancelled|timed_out`→`{error}` or partial result.
- [ ] Then for command ops branch `result.status`: `running`→`{output, command_id}` poll handle; `ok`→`{output}`; `error|timed_out|cancelled`→`{error}`. Never treat `rejected` as a command status.

**Touch.** `src/tools/sandbox/index.ts`.
**Verify.** `pnpm run check`; unit tests for each adapter mapping.

---

## BR-6 — Wire `ctx.signal` + connect retry/timeout
`bridge` · **Medium** · S/low · depends: BR-1 · verdict: refine

**Problem.** `ctx.signal` is not connected to any request; an interrupted run
leaves the socket call hanging, and a not-yet-ready gateway has no handling.

**Acceptance criteria.**
- [ ] On `ctx.signal` abort, `socket.destroy()` and reject the in-flight request.
- [ ] Bounded connect timeout + small `ECONNREFUSED` backoff (client-side only; not the host→daemon ladder).
- [ ] `sandbox_unavailable` treated retryable; `uncertain_outcome` terminal/non-retryable, surfaced to the tool result.

**Touch.** the BR-1 client module.
**Verify.** `pnpm run test` (abort + connect-refused cases).

---

## AUD-1 — Host-served `sandbox.audit` readback op
`audit` · **High** · M/med · depends: — · verdict: refine

**Problem.** The hash-chained audit store is fully queryable host-side but **no op
maps to it**; an orchestrator cannot read back a single record over the socket.

**Acceptance criteria.**
- [ ] New op **`sandbox.audit`** (2-segment **host** grammar — not `sandbox.audit.query`; the 3-segment name fails `canonical_names_follow_grammar`), `operator` visibility, `mutates_state=false`.
- [ ] Added to the `declare_builtin_ops!` table in `protocol/src/catalog.rs`; `ops.json` **regenerated** via `eosd dump-ops` (not hand-edited).
- [ ] `HostVerb::Audit` arm + `Engine::audit` trait method + `SandboxHost::audit` reading the private `trace_store`; both test `Engine` impls updated.
- [ ] Args `{trace_id? | request_id?, since_seq?, limit?}` → `{request, events[], event_count}`; `chain_verified` **omitted** (full-chain rescan).

**Touch.** `crates/protocol/src/catalog.rs`, `crates/operation/ops.json`, `crates/gateway/src/gateway.rs`, `crates/host/src/host.rs`, `crates/gateway/tests/contract/mod.rs`.
**Verify.** `cargo run -p xtask -- check-contract` · `cargo test -p gateway -p host`.

---

## AUD-2 — Server-authoritative `meta.trace` receipt
`audit` · **Medium** · S/low · depends: — · verdict: refine

**Problem.** `meta.trace.event_count` is stale (daemon-embedded) or `0`, and
`store` is a placeholder on host-served paths — the caller has no trustworthy
"durably persisted, N events under this key" receipt.

**Acceptance criteria.**
- [ ] On the **daemon-forward path only**, after all host events are appended (after `record_response_persisted` in `tcp_once`/`exec_thin_client`, not inside `mark_response_trace_ingested`), overwrite outgoing `meta.trace.event_count` from `TraceStore::event_count_for_trace(trace_id)`.
- [ ] Host-served verbs (acquire/release/status/list) left as honest `pending_host_ingest`/`0` (they persist no trace).
- [ ] The host unit test asserting the daemon-embedded count (`event_count:9`) is updated to the refreshed server count.

**Touch.** `crates/host/src/host.rs`, `crates/host/tests/unit/host.rs`.
**Verify.** `cargo test -p host --all-targets`.

---

## AUD-3 — Timed background trace drain
`audit` · **High** · M/med · depends: — · verdict: **valid as-is**

**Problem.** The bounded daemon trace spool drains only opportunistically after a
forward; idle sandboxes silently retain/lose background roots until overflow.

**Acceptance criteria.**
- [ ] `SandboxHost::open` spawns one host-level periodic thread (2–5 s) over the `SandboxRegistry`.
- [ ] For each live sandbox, `resolve_endpoint(record)` is called **before** `TraceExportDrainer.schedule` when `cached_endpoint()` is `None` (so the schedule snapshot sees the resolved endpoint).
- [ ] Reuses the existing single-flight/coalesce machinery (timed tick + forward-triggered drain never double-run).
- [ ] No new op, no protocol change.

**Touch.** `crates/host/src/host.rs`.
**Verify.** `cargo test -p host --all-targets`; (live) idle sandbox drains within one tick.

---

## AUD-4 — Retry per-request sidecar on ingest failure
`audit` · **Medium** · M/med · depends: shares AUD-3 timer · verdict: refine

**Problem.** A per-request `_trace_events` sidecar that fails host ingest stays
`pending_host_ingest` forever; the background pull (AUD-3) doesn't cover it, so a
successful mutating tool call can lose its rich per-request span tree.

**Acceptance criteria.**
- [ ] On the **ingest-error branch only** (`host.rs:1018`, decoded batch in hand), append a bounded `pending_sidecar` marker (decoded batch bytes + `sandbox_id` + `trace_id`) via a new `TraceStore::append_pending_sidecar`.
- [ ] Leave the **decode-failure** branch as-is (unrecoverable bytes).
- [ ] A host-local recovery pass (separate from the AUD-3 daemon-pull drainer) re-feeds `pending_sidecar` rows through `ingest_trace_batch`, deletes the row and flips `store→local_sqlite` on success.

**Touch.** `crates/host/src/host.rs`, `crates/host/src/trace_store.rs`.
**Verify.** `cargo test -p host --all-targets` (ingest-failure → recovery case).

---

## AUD-5 — Host-served `sandbox.audit.verify` op
`audit` · **Medium** · M/low · depends: — · verdict: refine

**Problem.** Chain verification has no reachable op and no runtime caller; tamper-
evidence is unverifiable from outside.

**Acceptance criteria.**
- [ ] New read-only op `sandbox.audit.verify` (`operator`, `mutates_state=false`) → `{ok, entries_checked, errors, pruned_ranges}` from `TraceStore::verify_chain`.
- [ ] `HostVerb` arm + `Engine` method (no `sandbox_id`, like Acquire/List); both test `Engine` impls updated; `ops.json` regenerated.
- [ ] **No** automatic sealing in this issue (`seal_all_unsealed` needs signing-key host config that doesn't exist — track separately).

**Touch.** `crates/protocol/src/catalog.rs`, `crates/operation/ops.json`, `crates/gateway/src/gateway.rs`, `crates/host/src/host.rs`, `crates/gateway/tests/contract/mod.rs`.
**Verify.** `cargo run -p xtask -- check-contract` · `cargo test -p gateway -p host`.

---

## TS-DTO-6 — Reconcile the two `protocol_version`s
`transport` · **Low** · S/low · depends: — · verdict: refine

**Problem.** Catalog/wire version is `1`; response `meta.protocol_version` is `2`;
nothing reconciles them, and a stale daemon comment claims the version is never
read.

**Acceptance criteria.**
- [ ] (A) `CONTRACT.md` documents three independent surfaces (wire / catalog / envelope) and which governs what.
- [ ] (A) `meta.protocol_version` renamed to **`envelope_version`** across `protocol/src/envelope.rs`, `gateway.rs`, `daemon/src/trace/envelope_meta.rs`, `PROTOCOL.md`, and the envelope tests.
- [ ] (A) `daemon/src/wire/message.rs:8-10` comment corrected ("read only into the trace record, not gated").
- [ ] (B, optional) daemon skew-guard compares `_eos_daemon_protocol_version` against the daemon's **own** version constant (add to `wire/mod.rs`) and rejects on major mismatch, with a fixture.
- [ ] (C) deferred: stamp the catalog version into the published schema artifact — only after TS-DTO-1.

**Touch.** `CONTRACT.md`, `contract/PROTOCOL.md`, `crates/protocol/src/envelope.rs`, `crates/gateway/src/gateway.rs`, `crates/daemon/src/trace/envelope_meta.rs`, `crates/daemon/src/wire/{mod,message}.rs`, `crates/daemon/src/transport/server.rs`.
**Verify.** `cargo run -p xtask -- check-contract` · `cargo test -p operation -p gateway -p daemon`.

---

## TS-DTO-2 — Publish closed `fault_kinds` set (as data)
`transport` · **Medium** · M/med · depends: — · verdict: refine

**Problem.** Error kinds are split across a typed daemon enum, gateway literals,
and domain rejection strings; no closed set is published for the TS client.

**Acceptance criteria.**
- [ ] A `fault_kinds` array is rendered into `ops.json` (via `ops_json_document()`), unioning daemon `ErrorKind` + gateway API kinds + domain rejection kinds. Exclude `aborted_version`/`aborted_overlap` (those are `MutationStatus`, not fault kinds).
- [ ] **No shared Rust enum across the host/box boundary**; each side keeps its local enum/literals. (Optional: a gateway-local enum to enumerate the host-side set in one place.)
- [ ] `cargo xtask check-contract` asserts every kind the daemon/gateway can emit appears in `fault_kinds`.
- [ ] `contract/PROTOCOL.md` §4 documents the published set.

**Touch.** `crates/protocol/src/catalog.rs`, `crates/operation/ops.json`, `contract/PROTOCOL.md`, `xtask/src/main.rs` (+ a gateway-local enum if chosen).
**Verify.** `cargo run -p xtask -- check-contract` · `cargo test -p operation -p gateway`.

---

## TS-DTO-5 — Byte-range file read + command-poll cursor
`transport` · **Medium** · M (read) / S (poll) · depends: — · verdict: refine

**Problem.** Large file reads return whole and hard-error at the 16 MiB cap;
`command.poll` tails to `last_n_lines` (the engine byte cursor isn't surfaced).

**Acceptance criteria.**
- [ ] **File read (new):** additive optional `offset:u64`, `limit:u64` on `ReadFileInput`/`ReadFileRequest`; result adds `{content, next_offset?, eof:bool}`; large files page instead of erroring.
- [ ] **Command poll (wiring):** additive optional `since_offset:u64` on `ReadProgressInput`; `read_command_progress` calls the existing `read_output_since(since_offset)` instead of `read_recent_output` when present; result adds `{chunk, next_offset, complete}`. `last_n_lines` stays the default.
- [ ] Both additive; no framing change; `command/src/{process,transcript}.rs` unchanged.

**Touch.** `crates/operation/src/file/{contract,lib}.rs`, `crates/daemon/src/op_adapter/files.rs`, `crates/operation/src/command/{contract,service}.rs`.
**Verify.** `cargo test -p operation -p daemon`; new fixtures for the windowed shapes.

---

## TS-DTO-3 — Declarative serde for command/file shaping
`transport` · **Low** · M/med · depends: — · verdict: refine

**Problem.** `CommandResponse::to_wire_value` and `files.rs::mutation_response`
strip `timings` and flatten maps imperatively (`if key == "timings" { continue }`,
`object.remove`).

**Acceptance criteria.**
- [ ] The runtime `timings`-strip/flatten loops are replaced by a typed wire DTO whose serde derive omits `timings` (`#[serde(skip)]`), preserving today's wire shape.
- [ ] `files.rs::mutation_response` `object.remove("timings")` dropped once the DTO omits it declaratively.
- [ ] **Envelope status arm unchanged** — command lifecycle status stays in `result.status` (remapping to `OperationEnvelope::running/cancelled` would be a contract change).
- [ ] Net-negative LOC; existing contract/daemon tests pass unchanged.

**Touch.** `crates/operation/src/command/contract.rs`, `crates/daemon/src/op_adapter/{command,files}.rs`.
**Verify.** `cargo test -p operation -p daemon` (incl. the conflict-fixture test).

---

## TS-DTO-1 — Publish per-op arg/result JSON Schemas
`transport` · **Medium** · L→XL/med-high · depends: TS-DTO-1-PRE · verdict: refine

**Problem.** `ops.json` carries no arg/result schema, so the TS side hand-mirrors
shapes. The input structs are hand-parsed with aliases/defaults, so a naive
`schemars` derive would publish a schema that **lies** about the accepted shape.

**TS-DTO-1-PRE (prerequisite).**
- [ ] Convert each op's hand-written `parse()` into a real `serde` `Deserialize`
      struct using `#[serde(alias=…, rename=…, default)]` so the struct **is** the
      wire shape (preserving `timeout`|`timeout_seconds`, `caller`←`caller_id`,
      optional-with-default behavior), pinned by `contract/fixtures/wire_messages/*_request.json`.

**Acceptance criteria.**
- [ ] `#[derive(schemars::JsonSchema)]` on the now-serde input structs; dedicated wire-result DTOs where a typed result exists.
- [ ] A new `op_schemas.json` emitted by `eosd dump-op-schemas`, gated by `cargo xtask check-contract` exactly like `ops.json`.
- [ ] The bridge client (BR-1) codegens Zod/types from `op_schemas.json`; LLM-facing tool schemas stay distinct from daemon wire schemas.

**Touch.** `crates/operation/src/*/contract.rs`, `crates/operation/Cargo.toml`, `crates/protocol/src/catalog.rs`, `crates/eosd/src/main.rs`, `xtask/src/main.rs`, `crates/operation/op_schemas.json`.
**Verify.** `cargo test -p operation --all-targets` · `cargo run -p xtask -- check-contract` · fixtures green.

---

## Closed — wontfix (kept as boundary-discipline lessons)

### BR-7 — Config field for gateway socket path · **wontfix**
Premise false: no TS client exists to hold a hardcoded path. A dangling
`EosConfig.sandboxGatewaySocketPath` with no consumer is speculative config the
repo forbids. **Make the endpoint configurable inside BR-1/BR-2 when the client
is built**, not as a standalone field now.

### TS-DTO-4 — Share one envelope DTO host↔box · **wontfix**
Directly violated the isolation law when envelope DTOs lived in `operation`: the
host-side gateway depended only on `host`, while `operation` was box-side and
pulled in `command`/`layerstack`/`nix`/…. `OperationEnvelope`/`ResponseMeta` now
live in `protocol`; the gateway still uses fixture-gated wire conformance rather
than linking box-side execution crates.
Any anti-drift work lives in fixtures + `check-contract`, never a crate dependency.
