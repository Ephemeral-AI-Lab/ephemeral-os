# eos-sandbox ↔ eos-coding-agent: Bridge Findings & Recommendations

**Status:** Assessment · **Date:** 2026-06-13 · **Scope:** how `eos-coding-agent`
(TypeScript) connects to `eos-sandbox`, the operations offered, and the
sandbox-side changes that would make that bridge clean, auditable, and typed.

Companion documents:
- **`how-to-connect.md`** — the practical connection manual + full 33-op API
  reference. This report is the *assessment*; that file is the *manual*.
- `contract/PROTOCOL.md` — normative wire contract · `docs/SPEC.md` — target
  architecture · `docs/sandbox-event-tracing-response-plan.md` — the audit/trace
  design this report measures the implementation against.

---

## 0. How to read this document

| Section | Question it answers |
|---|---|
| §1 Executive summary | What's the headline verdict? |
| §2 Method & confidence | How sure are we, and how was this verified? |
| §3 Good news | What is genuinely well-built and should not be touched? |
| §4 Bad news | What is missing or risky, and how bad is it? |
| §5 Optimization targets | What to change, prioritized by value-to-effort? |
| §6 Recommendations | The verified change set, with effort/risk/sequencing. |
| §7 Instructions | The ordered implementation playbook + verification gates. |
| §8 Appendix | Evidence index, the contradiction we caught, open decisions. |

Severity scale used throughout: **Blocker** (cannot proceed) · **High** (real
data-loss / correctness exposure) · **Medium** (friction, foot-gun, or silent
divergence) · **Low** (docs / cosmetics).

---

## 1. Executive summary

`eos-sandbox` is a **well-architected, contract-disciplined system that is ready
to be bridged today** — the gateway, the 33-op catalog, and the uniform response
envelope are sufficient to connect `eos-coding-agent` with **no sandbox-side
change required**. The work to make the bridge *exist* is entirely on the
TypeScript side, where the 7 sandbox tools are still stubs.

The two areas where sandbox-side investment pays off most are **auditability**
and **published typing**:

- A durable, hash-chained audit store **already exists**
  host-side and records every operation — but it is **structurally unreachable**
  from outside the host (no op maps to it), and the response's audit pointer is
  not yet a server-authoritative receipt.
- Strongly-typed Rust DTOs **already exist** for every operation — but they are
  **never published** to the TypeScript consumer, so the TS side must hand-mirror
  every arg/result shape.

Nothing found is a design dead-end. Every gap is closable with additive,
isolation-law-respecting changes, most of them small. The single highest-risk
*correctness* issue is not a bug but an **undocumented foot-gun**: the response
envelope nests the domain status one level down (`result.status`) for command and
file ops, and a naive client that reads only the top-level `status` will
mis-parse every command.

**Bottom line:** Build the TS bridge (it's unblocked). In parallel, ship the two
high-value sandbox-side pairs — **AUD-1 + AUD-2** (reachable, authoritative
audit) and **TS-DTO-6 + TS-DTO-2** (version clarity + a published error set) —
and document the envelope-nesting rule before any client consumes it.

---

## 2. Method & confidence

| Layer | How it was established | Confidence |
|---|---|---|
| Client-hop wire protocol, routing, envelope, lifecycle | Read first-hand: `gateway/src/{serve,gateway}.rs`, `host/src/{host,protocol}.rs`, `daemon/src/transport/server.rs`, `daemon/src/wire/message.rs`, `protocol/src/envelope.rs`, `daemon/src/op_adapter/mod.rs`, `daemon/src/dispatch/builtin.rs` | **High** (direct) |
| 33-op arg/result DTO catalog | Workflow agent over `operation/src/*/contract.rs` + adapters + fixtures; spot-checked against first-hand reads | **High** |
| 18 modification recommendations | Each proposed by a dimension finder, then adversarially verified against the code by an independent skeptic (premise / citation / isolation-law / minimality) | **High** for verdicts; refined proposals carry the corrections |
| Auditability behavior | `trace/`, `daemon/src/trace/`, `host/src/trace_store.rs`, `operation/src/core/audit.rs` + the two design docs | **High** |

The analysis ran as a 29-agent workflow (7 subsystem readers + 1 catalog
extractor + 3 dimension finders + 18 adversarial verifiers). One **direct
contradiction between agents** was caught and resolved first-hand (see §8.2); it
materially changes how a client must parse command responses.

---

## 3. Good news — what is genuinely well-built

> These are strengths to **preserve**. Several proposed "improvements" were
> rejected precisely because they would have damaged the items below.

| # | Strength | Evidence |
|---|---|---|
| G1 | **One socket, pure catalog routing.** The gateway never branches on op names; `served_by`/`visibility` from `ops.json` drive everything. New ops need no router code. | `gateway.rs:249-318` |
| G2 | **Hard host/box isolation law, drift-gated.** No compiled code crosses the boundary; the only shared artifacts are `ops.json` + `contract/`, enforced by `cargo xtask check-contract`. This is what lets a TS client be a pure data client. | `CONTRACT.md`, `PROTOCOL.md:3-7` |
| G3 | **Uniform response envelope.** Every response — host-built or daemon-forwarded — is the same externally-tagged `OperationEnvelope<T>` (`status` ∈ ok/running/rejected/cancelled/timed_out/error) with a uniform `meta`. Bare adapter outputs are auto-wrapped, so the invariant holds at the wire boundary. | `protocol/src/envelope.rs`, `daemon/src/op_adapter/mod.rs:26-32` |
| G4 | **Rich, render-from-trace metadata.** `meta` carries `trace{trace_id,request_id,store,event_count,degraded}`, `duration_ms`, `modules_touched`, `steps`, `resource_summary`, `warnings` — never hand-inserted on the daemon path. | `protocol/src/envelope.rs`, `daemon/src/trace/envelope_meta.rs` |
| G5 | **A real audit pipeline already exists.** A fail-closed (mutations don't forward if the request-start row can't be written), hash-chained SQLite store records request → daemon op → files/commands touched → response, keyed by `trace_id`/`sandbox_id`. | `host/src/trace_store.rs:111-426,708-839` |
| G6 | **Honest failure semantics.** `uncertain_outcome` for delivery-ambiguous mutations (never retried), `sandbox_unavailable` after recovery exhaustion, plus a connect-retry/respawn/thin-client recovery machine — all invisible to the caller. | `host/src/host.rs:690-905` |
| G7 | **Typed DTOs already in place.** Every op input is a typed struct; most results are typed too. The schema information *exists*; it is only the *publishing* that is missing. | `operation/src/*/contract.rs` |
| G8 | **Immutable golden fixtures + conformance.** Request byte-identity and response canonical-equality are pinned by frozen fixtures captured from the original runtime. | `contract/fixtures/`, `PROTOCOL.md:89-103` |
| G9 | **Async command model is sound.** `exec → poll → collect_completed` with `command_id`, an in-flight registry (heartbeat/cancel/count keyed by `invocation_id`), and a persisted full transcript on disk. | `daemon/src/runtime/invocation_registry.rs`, `command/src/transcript.rs` |

---

## 4. Bad news — gaps & risks

### 4.1 Bridge

| ID | Severity | Finding | Rec |
|---|---|---|---|
| B1 | **Blocker** (to *use* the sandbox) | The bridge is **entirely unbuilt on the TS side**. All 7 sandbox tools return `{ error: "sandbox daemon bridge is not wired in this build" }`; there is no socket client, no `sandbox_id` threading, no acquire/release lifecycle. The SDK ships no socket helper. *This is TS-side work, not a sandbox defect.* | BR-1..6 |
| B2 | **Medium** (foot-gun) | **The envelope nests domain status.** For command/file ops the envelope `status` is `ok` while the real lifecycle status is at `result.status` (`running`/`committed`/`aborted_version`/`error`/…). A client reading only top-level `status` mis-parses every command (a running command, and even `command_not_found`, both surface as envelope `ok`). **Undocumented today.** | doc + BR-5 adapter |

### 4.2 Auditability

| ID | Severity | Finding | Rec |
|---|---|---|---|
| A1 | **High** | **The audit trail is captured but unreachable.** None of the `TraceStore` query methods (`events_for_trace`, `request_by_id`, `verify_chain`, …) maps to any catalog op. The gateway serves exactly four host verbs (acquire/release/status/list). An external orchestrator can correlate by `trace_id` but **cannot read back a single audit record over the socket**. | AUD-1 |
| A2 | **High** (data loss) | **Idle sandboxes silently drop background trace events.** The bounded in-memory daemon trace spool (idle-evictions, command advances, plugin services, post-sidecar transport failures) drains **only opportunistically after a successful forward**, single-flight, no-op when the endpoint is unresolved. A sandbox that stops receiving ops accumulates roots until overflow drops them — exactly the teardown/loss events an auditor most needs. | AUD-3 |
| A3 | **Medium** | **`meta.trace` is not a server-authoritative receipt.** Gateway-built (host-served) responses hard-code `store="pending_host_ingest"`, `event_count=0`; for daemon ops the host rewrites `store→local_sqlite` but never refreshes `event_count` from SQLite. The caller has no trustworthy "durably persisted, N events under this key" receipt. | AUD-2 |
| A4 | **Medium** | **No retry for a per-request sidecar that fails host ingest.** It silently stays `pending_host_ingest` forever; the background pull (A2) doesn't cover it. A successful mutating tool call can lose its rich per-request span tree (the `request_start`/`response_persisted` rows survive; the detailed tree does not). | AUD-4 |
| A5 | **Medium** | **Tamper-evidence is unverifiable from outside, and nothing seals automatically.** `verify_chain`/`seal_all_unsealed` have no reachable op and no runtime caller. | AUD-5 |

### 4.3 Data transport, types, DTOs, I/O, response format

| ID | Severity | Finding | Rec |
|---|---|---|---|
| T1 | **Medium** | **No machine-readable arg/result schemas are published.** `ops.json` carries only `name/served_by/visibility/family/mutates_state/summary`. The typed Rust DTOs exist but are never exported, so the TS side hand-mirrors every shape (and the input structs are hand-parsed with aliases/defaults — `timeout`\|`timeout_seconds`, `caller`←`caller_id` — so a naive schema export would *lie* about the accepted shape). | TS-DTO-1 |
| T2 | **Low** (doc/clarity) | **Two `protocol_version` numbers, undocumented.** Catalog/wire version is `1` (`ops.json`, `_eos_daemon_protocol_version` in `args`); response `meta.protocol_version` is hard-coded `2`. Nothing reconciles them; a TS author can't tell which governs the schemas they generated. A stale daemon doc comment claims the version field is never read — it is (into the trace record). | TS-DTO-6 |
| T3 | **Medium** | **Error taxonomy is split & stringly-typed.** `OperationFault.kind` is a free-form `String`; the daemon has a typed `ErrorKind` (9 kinds), the gateway raises its own API-level kinds as literals, and domain rejections add more. No single closed set is published, so a TS client can't exhaustively switch. | TS-DTO-2 |
| T4 | **Medium** | **No paging / streaming for large payloads.** A large file read returns whole and hard-errors at the 16 MiB cap with no window; command `poll` tails to `last_n_lines` (a byte cursor exists in the engine but isn't surfaced). | TS-DTO-5 |
| T5 | **Low** | **Hand-rolled JSON shaping (duplication smell).** `CommandResponse::to_wire_value` and `files.rs::mutation_response` strip `timings` and flatten maps imperatively instead of via serde attributes. | TS-DTO-3 |

> **Two proposals were rejected by adversarial verification** — kept here because
> they teach the boundary discipline:
> - **TS-DTO-4** "share one envelope DTO across gateway and daemon" — would link a
>   heavy box-side crate (`operation`, which pulls in `command`/`layerstack`/`nix`)
>   into the host-side gateway binary. **Violates the isolation law (G2).** The
>   hand-built duplication is deliberate and fixture-gated, not a defect.
> - **BR-7** "add `EosConfig.sandboxGatewaySocketPath`" — the premise is false
>   (no TS client exists to hold a hardcoded path). Adding the field now is
>   speculative config the repo forbids; make it configurable *when* the client
>   is built.

---

## 5. Optimization targets (prioritized)

```
                         value to the orchestrator
                          low                 high
            ┌───────────────────────────────────────────────┐
      low   │ TS-DTO-3 (serde cleanup)   │ AUD-2 (receipt)   │
   effort   │ TS-DTO-6 (version doc)     │ TS-DTO-2 (faults) │
            ├───────────────────────────────────────────────┤
            │ AUD-4/AUD-5 (retry/verify) │ AUD-1 (audit op)  │
      high  │ TS-DTO-1 (schemas: XL)     │ AUD-3 (timed drain│
            │                            │ TS-DTO-5 (paging) │
            └───────────────────────────────────────────────┘
   (the TS bridge BR-1..6 is its own track — unblocked, no sandbox change)
```

**Do-first set (best value-to-effort):** AUD-2, AUD-1, AUD-3, TS-DTO-6(doc),
TS-DTO-2 — plus the B2 envelope-nesting doc note, which costs a paragraph and
prevents a whole class of client bugs.

---

## 6. Recommendations (verified)

Each carries the verdict from adversarial verification and the **refined** form
(several first drafts had wrong premises or violated the isolation law; the
corrections are folded in). Effort: S/M/L/XL · Risk: low/med/high.

### Track A — Bridge (TypeScript side; sandbox unchanged)

| ID | Recommendation | E/R | Verdict |
|---|---|---|---|
| BR-1 | Greenfield `SandboxGatewayClient` (`node:net` UDS, one-line req/resp, fresh connection per call, Zod-validated `{status,result\|error,meta}`). Include tool→op name mapping; mark `sandbox_id` **required**. | M/low | refine |
| BR-2 | Widen `sandboxTools(client, sandboxId)`; thread through `buildAgentFactory → selectOrdinaryTools`; source `invocation_id` from `ctx.toolUseId`. | M/low | refine |
| BR-3 | Bind acquire/release at the **real** run boundary `pursuit/service.ts:324-341` (not bootstrap/agent-factory); `run.end` with `caller_id == agent_run_id` on settle/fail/interrupt; resolve caller_id granularity for multi-run pursuits. | M/med | refine |
| BR-4 | Codegen a typed TS op-contract from `ops.json` — **after** BR-1 introduces op-name strings; freshness gate is a deliberate cross-tree decision, not automatic. | S/low | refine |
| BR-5 | Arg/response adapters in `execute()`: reshape edit→`edits[]`, exec `timeout_ms`→seconds, `cwd`→`cd … &&` (never silently drop), stdin→`chars`, transcript→`last_n_lines`; **envelope-first** response branching then `result.status`. | M/low | refine |
| BR-6 | Wire `ctx.signal`→`socket.destroy()`; bounded connect timeout + `ECONNREFUSED` backoff (client-side only); classify `sandbox_unavailable` retryable / `uncertain_outcome` terminal. | S/low | refine |

### Track B — Auditability (sandbox side)

| ID | Recommendation | E/R | Verdict |
|---|---|---|---|
| **AUD-3** | **Timed background drain** in `SandboxHost::open`: a 2–5 s thread that resolves endpoints for idle sandboxes (`cached_endpoint()==None`) **before** `schedule()`, reusing the single-flight/coalesce machinery. No new op, no protocol change. | M/med | **valid as-is** |
| AUD-1 | One host-served read-only op **`sandbox.audit`** (2-segment host grammar — *not* `sandbox.audit.query`, which fails `canonical_names_follow_grammar`), `operator` visibility. Add `BuiltinOp` in `protocol/src/catalog.rs` + regenerate `ops.json`; `HostVerb::Audit`; `Engine::audit` + `SandboxHost::audit` over the private `trace_store`. Args `{trace_id?\|request_id?, since_seq?, limit?}` → `{request, events[], event_count}`. **Drop `chain_verified`** (full-chain rescan). | M/med | refine |
| AUD-2 | Server-authoritative receipt on the **daemon-forward path only**: after all host events are appended (after `record_response_persisted`), overwrite `meta.trace.event_count` from `event_count_for_trace(trace_id)`. Leave host-served verbs' `pending_host_ingest`/`0` honest. | S/low | refine |
| AUD-5 | Read-only `sandbox.audit.verify` (operator) over `verify_chain` → `{ok, entries_checked, errors, pruned_ranges}` via a new `Engine` method (update the two test impls). **Do not** bundle auto-sealing (needs signing-key config that doesn't exist). | M/low | refine |
| AUD-4 | On per-request sidecar **ingest** failure, append a bounded `pending_sidecar` marker (decoded batch bytes + ids) and a host-local recovery pass that re-feeds `ingest_trace_batch`, flipping `store→local_sqlite` on success. Leave the decode-failure branch (unrecoverable bytes) as-is. | M/med | refine |

### Track C — Transport / types / DTOs / I/O / response

| ID | Recommendation | E/R | Verdict |
|---|---|---|---|
| TS-DTO-6 | **Document the three version surfaces** (wire / catalog / envelope) in `CONTRACT.md` and **rename `meta.protocol_version` → `envelope_version`**. Fix the stale `wire/message.rs:8-10` comment. Optional daemon skew-guard against its **own** version constant. | S/low | refine |
| TS-DTO-2 | Publish a closed **`fault_kinds`** array into `ops.json` (as data, via `ops_json_document()`), unioning daemon + gateway + domain kinds, gated by `check-contract`. **No shared Rust enum across the boundary** — keep each side's enum local. | M/med | refine |
| TS-DTO-5 | Additive **byte-range** on `sandbox.file.read` (`offset`/`limit` → `{content,next_offset?,eof}`) — the one missing primitive — and **surface the existing** `read_output_since` cursor on `sandbox.command.poll` via `since_offset` → `{chunk,next_offset,complete}`. | M / S | refine |
| TS-DTO-1 | Publish per-op JSON Schemas as a drift-gated `op_schemas.json`. **Prerequisite:** convert each hand-written `parse()` to real `serde` `Deserialize` (`#[serde(alias/default)]`, fixture-pinned) so the struct *is* the wire shape, then derive `schemars`. Largest item. | L→XL / med-high | refine |
| TS-DTO-3 | Replace the imperative `timings`-strip/flatten in `CommandResponse::to_wire_value` + `files.rs::mutation_response` with declarative serde. Behavior-preserving; net-negative LOC. **Do not** remap command status onto the envelope status arm. | M/med | refine |
| ~~TS-DTO-4~~ | **Rejected** — shares a box crate into the host binary (isolation-law violation). | — | incorrect |

---

## 7. Instructions — implementation playbook

> Three independent tracks. Track A unblocks *using* the sandbox; Tracks B and C
> improve it. Each phase ends with a verification gate that must pass before the
> next. Sandbox-side commands run from `eos-sandbox/`; TS-side from
> `eos-coding-agent/`.

### Phase 0 — Documentation (do immediately, no code risk)
1. Add the **envelope-nesting rule (B2)** to `contract/PROTOCOL.md` §4 and
   `how-to-connect.md`: *branch on envelope `status` first, then `result.status`
   for command/file ops.*
2. TS-DTO-6(A): document the three version surfaces in `CONTRACT.md`; rename
   `meta.protocol_version` → `envelope_version` across `envelope.rs`,
   `gateway.rs`, `daemon/src/trace/envelope_meta.rs`, `PROTOCOL.md`, and the
   envelope tests; fix the stale `wire/message.rs:8-10` comment.
   **Gate:** `cargo xtask check-contract` · `cargo test -p operation -p gateway -p daemon`.

### Phase 1 — Build the TS bridge (Track A, ordered; sandbox unchanged)
1. **BR-1** `src/tools/sandbox/gateway-client.ts` (socket + Zod envelope).
2. **BR-2** widen `sandboxTools(client, sandboxId)`; thread the factory; one
   process-level client in `bootstrap.ts`.
3. **BR-5** arg/response adapters per the tool→op table in `how-to-connect.md` §4.
4. **BR-3** acquire/release at `pursuit/service.ts` run boundary; `run.end` +
   `release` on settle/fail/interrupt; pin caller_id granularity.
5. **BR-6** abort + connect-resilience in the client.
6. **BR-4** (optional) codegen `sandbox-ops.generated.ts` from `ops.json`.
   **Gate per step:** `pnpm run typecheck && pnpm run lint && pnpm run test`;
   full `pnpm run check` once the tool family consumes the response contract.
   **End-to-end smoke:** start the gateway (`cargo run -p gateway -- serve …`),
   run one acquire→write→exec→poll→release cycle from the agent.

### Phase 2 — Auditability (Track B; do AUD-3, AUD-1, AUD-2 first)
1. **AUD-3** timed drain in `SandboxHost::open` (host-only).
   **Gate:** `cargo test -p host --all-targets`.
2. **AUD-1** `sandbox.audit` op: edit the `declare_builtin_ops!` table in
   `protocol/src/catalog.rs`; `cargo run -p eosd -- dump-ops > crates/operation/ops.json`;
   `cargo run -p xtask -- gen-docs`; add `HostVerb::Audit` + `Engine::audit` +
   `SandboxHost::audit`; update the two test `Engine` impls.
   **Gate:** `cargo xtask check-contract` · `cargo test -p gateway -p host`.
3. **AUD-2** authoritative `event_count` on the daemon-forward path; update the
   host unit test that asserts the daemon-embedded count.
4. **AUD-5 / AUD-4** as follow-ups (verify op; per-request sidecar retry).
   **Gate (broad):** `cargo run -p e2e-test --bin e2e-runner -- --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4`.

### Phase 3 — Typed contract (Track C)
1. **TS-DTO-2** `fault_kinds` data array + `check-contract` assertion.
2. **TS-DTO-5** additive file-read window + command-poll cursor (fixture-pinned).
3. **TS-DTO-3** serde cleanup of command/file shaping (behavior-preserving).
4. **TS-DTO-1** (largest) parse()→serde refactor, then publish `op_schemas.json`;
   the TS client codegens Zod/types from it.
   **Gate:** `cargo test -p operation -p daemon -p gateway` + e2e assertions
   migrated from top-level `timings`/`workspace`/`success` to `meta`/`status`/`result`.

### Cross-cutting rules
- **Never** add a crate dependency across the host/box boundary (G2 / rejected
  TS-DTO-4). New contract surface is **data** in `ops.json`/`op_schemas.json`,
  gated by `check-contract` — not a shared compiled type.
- **Never** hand-edit `ops.json`; edit `protocol/src/catalog.rs` and regenerate via
  `eosd dump-ops`.
- Golden fixtures under `contract/fixtures/` are immutable; if a change would
  alter a fixture, the change is wrong (or needs an explicit contract bump per
  `CONTRACT.md`).

---

## 8. Appendix

### 8.1 Evidence index (load-bearing citations)

| Claim | Source |
|---|---|
| Client hop: UDS, one line/conn, half-close, 16 MiB, 30 s | `gateway/src/gateway.rs:16,499-573,679-698`; `host/src/protocol.rs:15` |
| Routing is pure catalog lookup; unknown names are rejected as `unknown_op` | `gateway.rs:249-318` |
| Two surfaces; visibility gate | `gateway.rs:222-247` |
| Envelope union + `meta` shape | `protocol/src/envelope.rs` |
| Bare adapter output auto-wrapped (the §8.2 resolution) | `daemon/src/dispatch/builtin.rs:58,95-100`; `op_adapter/mod.rs:26-32,69-78` |
| Box hop: TCP + auth token, sandbox_id stripped, recovery | `host/src/host.rs:204-264,463-905`; `protocol.rs:8-16` |
| Audit store: fail-closed, hash-chained, queryable | `host/src/trace_store.rs:111-426,708-839` |
| Background spool drains only after forward | `host/src/host.rs:537,559-661`; `trace/src/spool.rs` |
| `sandbox.trace.export` is `internal` (unreachable on both sockets) | `ops.json`; `gateway.rs:222-247` |
| Five-ID model | `host/src/host.rs:81-87,135`; `daemon/src/wire/message.rs:33-49`; `core/id.rs` |

### 8.2 The contradiction we caught (and resolved)

The catalog extractor claimed command `running` responses are *returned raw, not
enveloped*; the transport verifier claimed the opposite. Resolved first-hand:
`ExecCommand → daemon_result → ok_envelope`, and `is_operation_envelope` requires
a `meta` key (which the bare `CommandResponse` lacks), so the command value **is
wrapped** as `OperationEnvelope::ok{ result: <command wire>, meta }`. Therefore
**every** daemon response is an envelope; the command lifecycle status lives at
`result.status`. This is finding **B2** and is now the headline parsing rule in
`how-to-connect.md`.

### 8.3 Open decisions (need a human call)

| Decision | Recommendation |
|---|---|
| caller_id granularity for multi-run pursuits | Define which run owns acquire/release vs. which child runs share the sandbox and map to `run.end` scope, before BR-3. |
| Trace store strictness when unavailable | Already fail-closed for mutations; keep. Read-only ops proceed marked `degraded`. |
| Public trace lookup exposure | `operator` visibility only (AUD-1/AUD-5); user-facing client responses keep trace *refs*, not full chains. |
| Auto-sealing the audit chain | Out of scope until signing-key host config exists; track separately from AUD-5. |
| `op_schemas.json` cross-tree freshness gate | Treat any `check-contract` read of the TS tree as a deliberate coupling decision, not automatic. |

---

*Generated from a first-hand read of the connection-critical path plus a 29-agent
verification workflow over `eos-sandbox` and `eos-coding-agent`. Recommendation
verdicts and refined proposals reflect adversarial verification against the code;
the two rejected items (TS-DTO-4, BR-7) are retained as boundary-discipline
lessons.*
