# Prompt: Generate the Phase 2 Implementation Spec for `sandbox-e2e-live-test`

Use this prompt to produce a single, implementation-ready spec covering **only
Phase 2 (Full per-operation tree + assertions)** of the parent design. Phase 2 is
delivered in **two stages** fixed by the in-flight `sandbox-runtime` migration, and
the spec you generate must encode that split (see *Two-stage Delivery* below).

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/e2e/sandbox-e2e-live-test-phase-2-spec.md`: a precise, build-to-green
implementation spec for Phase 2 of the live E2E runner. The output is a **spec, not
code** — detailed enough that an engineer implements it without re-deriving any
design decision: every leaf file, every `#[test]` name, every fixture call, every
asserted JSON field, every assertion-helper signature, and every load-bearing
`file:line` is named. Treat the parent spec as the fixed design and **live code as
the source of truth** (live code wins on any conflict).

**Phase 0–1 is already implemented and live.** Build *on the live Phase 1
skeleton* — `src/{config,cli_client,fixtures,gateway,assertion,lib}.rs`,
`tests/support/mod.rs`, `build.rs`, `tests/{manager,runtime}.rs`, and the two leaf
tests M1/R1. Read those files for the starting state; do **not** re-derive Phase 1
from its prose. The spec adds Phase 2's slice and nothing else.

## Two-stage Delivery (the spec MUST encode this)

The `sandbox-runtime` CLI operations (the R-series — `exec_command`,
`write_command_stdin`, `read_command_lines`, `create_workspace_session`,
`destroy_workspace_session`, `squash`) are mid-migration and cannot be driven yet.
The Phase 2 spec must **specify the complete per-operation tree** but **partition
every deliverable** into two stages, each independently verifiable:

- **Stage 1 — manager surface, green now.** Leaves M2–M5 and N1; the `err_kind_at`
  assertion helper; `exchange.jsonl` capture. Green criterion:
  `cargo test --test manager` passes against a gateway that provisions/destroys
  sandboxes and serves `get_observability_tree`. Drives **no** runtime operation.
- **Stage 2 — runtime surface, resume after the migration lands.** Un-dormant R1;
  author R2–R8 and N2; the `with_workspace_session` fixture; the `err_detail`,
  `offsets_monotonic`, `non_decreasing` helpers. Green criterion: full
  `cargo test` (manager + runtime) passes against the migrated real Docker runtime.

The split mirrors the parent spec's *Two-stage delivery during the runtime
migration* (`## Implementation Phases`) and the phases note's *Two-stage delivery
(runtime-migration gate)*. The boundary is **binary-level**: the only skip path is
`EOS_E2E_RUN_ROOT` unset (`tests/support/mod.rs:7-9`), so a runtime leaf driven
against a not-yet-migrated runtime would *fail* (`operation_failed`), never skip —
Stage 1 therefore keeps the runtime binary out of its green target rather than
adding a runtime-readiness skip guard. R2–R8 are **authored in Stage 2** against
the migrated runtime's *settled* response shapes, so the suite is not rewritten
against a moving target; every runtime response-shape citation in the spec must be
tagged **re-verify at Stage 2**.

## How To Run (multi-agent: author → verifier → finalize)

Run as two cooperating agents, not one. The **Author** drafts the spec; the
**Verifier** adversarially checks it against live code before it is accepted. This
catches stale `file:line` citations, scope leaks, and **stage-fence leaks** — a
Stage 1 deliverable that secretly drives a runtime op.

```text
1. Orchestrator: bootstrap once (git state; confirm the crate IS a live member and
   the Phase 1 skeleton + M1/R1 leaves exist; confirm gateway/main.rs Unconfigured*
   stubs still ship). Share results with both agents.
2. Author agent: read the parent spec + phases note + the live-code reading list,
   resolve every Design Question, and WRITE the draft to
   docs/e2e/sandbox-e2e-live-test-phase-2-spec.md, including the anchor ledger and
   the per-leaf/per-stage partition.
3. Verifier agent (blind to the Author's reasoning, given only the draft + this
   prompt + the reading list): independently re-open every file:line in the anchor
   ledger AND every other citation; flag confirmed/stale/wrong with the corrected
   fact. Then audit two fences: (a) scope — anything Phase 3–4 (orchestrator,
   aggregation, summary.json, observability polling, cleanup automation) that
   leaked in; (b) stage — any Stage 1 leaf that issues a runtime op, any Stage 2
   item mis-tagged Stage 1, and whether `cargo test --test manager` is provably
   green with zero runtime calls. Return a defect list (no rewrite).
4. Orchestrator: on any L0/L1 defect, hand draft + defects back to the Author to
   revise in place, then re-verify. Loop until zero citation defects, zero scope
   leaks, zero stage leaks.
5. Orchestrator: report the final spec path + the Verifier's clean ledger.
```

The Author and Verifier must not share scratch reasoning — the Verifier re-derives
every fact from live code so a wrong citation cannot survive by being asserted
twice. This remains **spec-only**: neither agent implements the crate.

## Source Material (read first, in full)

- Parent design spec: `docs/e2e/sandbox-e2e-live-test-spec.md`. Phase 2 is defined
  under `## Implementation Phases`; the contract it realizes is the
  `## Manager and Runtime CLI Test Matrix` (M1–M5, R1–R8, N1–N2), the ordering
  constraints just below it, the assertion strategy, the `## Test Layout and
  Fixtures` section, the `## Reproducibility, Artifacts, and Cleanup` artifact tree
  (for `exchange.jsonl`), and the `### Two-stage delivery during the runtime
  migration` overlay. Do **not** restate the whole parent spec — extract and harden
  only what Phase 2 touches.
- Phases note: `docs/e2e/sandbox-e2e-live-test-phases-note.md` →
  `## Two-stage delivery (runtime-migration gate)` and the Phase 2 entry.
- Phase 0–1 spec: `docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md` — the
  output-quality bar and the documented starting state (but **live code wins** over
  its prose where they differ).
- Repo orientation: `README.md` (component map + boundary law) and `CLAUDE.md`
  (engineering practice, build/test, conventions).

## Live Code To Verify (do not trust the parent spec's citations — confirm each)

**Live Phase 1 skeleton (the starting state Phase 2 extends — live wins over prose):**

```text
crates/sandbox-e2e-live-test/src/cli_client.rs        # CliClient::{manager,runtime}; CallRecord; carrier = exit==0 ? stdout : stderr (:72-77); request_json is None on black-box path (:12-13,:81)
crates/sandbox-e2e-live-test/src/fixtures.rs          # Harness::{get,init,cli,provision_sandbox} (:25-88); workspace root = {run_root}/work/{run_id}-{slug} canonicalized (:61-74); Sandbox{id,workspace_root} + Drop->destroy_sandbox (:94-107)
crates/sandbox-e2e-live-test/src/assertion.rs         # only ok + field exist today (:4-16) — Phase 2 adds err_kind_at / err_detail / offsets_monotonic / non_decreasing
crates/sandbox-e2e-live-test/src/config.rs            # RunConfig::from_env + run-manifest schema actually read by fixtures
crates/sandbox-e2e-live-test/tests/support/mod.rs     # skip-safe entry: harness()->Option (:7-9); the ONLY skip path
crates/sandbox-e2e-live-test/build.rs                 # walks tests/{manager,runtime}/**/*.rs; slug = path components joined by '_' minus .rs; rerun-if-changed per leaf
crates/sandbox-e2e-live-test/tests/manager.rs         # stable 2-line root: mod support; include!(manager_mods.rs)
crates/sandbox-e2e-live-test/tests/runtime.rs         # stable 2-line root: mod support; include!(runtime_mods.rs)
crates/sandbox-e2e-live-test/tests/manager/lifecycle/create_sandbox/returns_ready.rs   # M1 (Phase 1) — the leaf shape to mirror
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/one_shot.rs            # R1 (Phase 1) — dormant; Stage 2 un-dormants it
```

**Manager op contracts for M2–M5 (Stage 1):**

```text
crates/sandbox-manager/src/operation/impls/management/list_sandboxes.rs          # M2 args + /sandboxes[] record shape
crates/sandbox-manager/src/operation/impls/management/inspect_sandbox.rs         # M3 --sandbox-id arg + /id,/workspace_root,/state,/daemon
crates/sandbox-manager/src/operation/impls/management/destroy_sandbox.rs         # M5 --sandbox-id + returned /id; removed-after semantics
crates/sandbox-manager/src/operation/impls/management/get_observability_tree.rs  # M4 args (--include-recent-traces/--trace-limit/--resource-window-ms); tree keys; CLAMP-not-reject
crates/sandbox-manager/src/operation/impls/management/mod.rs                      # op-name surface + absolute-workspace-root check (:68)
```

**Routing / error contracts for N1, N2 and `err_kind_at` (Stage 1 for N1):**

```text
crates/sandbox-gateway/src/cli/output.rs              # render_response: error -> stderr+EXIT_FAILURE(1), ok -> stdout+EXIT_SUCCESS(0) (:266-272); parse/usage error -> stderr+EXIT_USAGE(2) (:84-96); invalid_request render (:287-292)
crates/sandbox-protocol/src/response.rs               # error shape {kind,message,details} (:30-49); service_error -> "operation_failed" (:20-22); unknown_op (:25-27)
crates/sandbox-manager/src/router/dispatch.rs         # (System,unknown)->unknown_op; (Sandbox,manager-owned)->invalid_request (:8-31)
crates/sandbox-gateway/src/cli/request_builder.rs     # resolve_runtime_sandbox_id -> build_error "runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID" (:84-91) => N2 is CLI-side, exit 2/stderr, kind invalid_request
```

**Runtime op contracts for R2–R8, N2 (Stage 2 — RE-VERIFY against the migrated surface):**

```text
crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs           # exec_command/write_command_stdin/read_command_lines args + yield fields (status, exit_code, start/end_offset, total_lines, command_session_id present iff still running)
crates/sandbox-runtime/operation/src/cli_definition/workspace_session_operations.rs # create/destroy_workspace_session args; /workspace_session_id, /profile, /destroyed, error.details.active_command_session_ids
crates/sandbox-runtime/operation/src/cli_definition/layerstack_operations.rs        # squash args + /squashed, /revision/root_hash
```

Confirm every call path with `rg` — actual argument sets, response field readers,
and the exact `error.details.*` keys — before relying on any line number. Tag
every R-series row **re-verify at Stage 2**.

## Scope Fence (in vs out)

**IN — Stage 1 (manager surface; green now):**

- Leaf tests, one file per case under
  `tests/manager/<family>/<operation>/<case>.rs`:
  - `lifecycle/list_sandboxes/` (M2), `lifecycle/inspect_sandbox/` (M3),
    `lifecycle/destroy_sandbox/` (M5), `observability/get_observability_tree/` (M4).
  - `routing/scope_and_dispatch/` (N1 — unknown system op).
- `src/assertion.rs`: add **`err_kind_at`** (locate the error object on the carried
  stream, assert `error.kind` AND exit code), defined against the LIVE routing
  (`cli_client.rs:72-77`, `output.rs:266-272`) — not the parent spec's stale
  "stdout (exit 1)" note.
- **`exchange.jsonl` capture** — the per-sandbox call-record artifact (resolve
  ownership/keying/flush; Design Question 2).

**IN — Stage 2 (runtime surface; after the migration lands):**

- Un-dormant R1; author leaves under `tests/runtime/<family>/<operation>/<case>.rs`:
  - `command/exec_command/` (R3 in-session, R4 long-running), `command/write_command_stdin/`
    (R5), `command/read_command_lines/` (R6).
  - `workspace_session/create_workspace_session/` (R2),
    `workspace_session/destroy_workspace_session/` (R7a clean, R7b busy).
  - `layerstack/squash/` (R8).
  - `routing/scope_and_dispatch/` (N2 — runtime op without sandbox id; CLI-side, so
    technically Stage-1-capable but grouped here for a clean binary boundary —
    state this).
- `src/fixtures.rs`: add **`with_workspace_session`** (RAII workspace-session over
  `create/destroy_workspace_session`) for R2/R3/R7.
- `src/assertion.rs`: add **`err_detail`** (runtime `operation_failed`
  `error.details` pointer), **`offsets_monotonic`** (within one response
  `start ≤ end ≤ total`), **`non_decreasing`** (across two responses).

**OUT (defer to Phases 3–4 — name them as out-of-scope, do not design them):**

- The orchestrator bin internals: preflight, build phase, env export, the
  `--test {manager|runtime}` invocation policy, aggregation from `result.json`,
  `summary.json`/timing/cleanup sub-objects. `eos-e2e` stays the Phase 0–1 stub;
  Phase 2 runs are by hand (`EOS_E2E_RUN_ROOT` + `cargo test`).
- `result.json` and `summary.json` writing and the report-dir aggregation contract
  (Phase 3 owns them). Phase 2 introduces **only** `exchange.jsonl`. State whether
  the skip path writes anything (live skip is a bare `return`; keep it so unless a
  Phase 2 deliverable needs otherwise).
- Observability polling, `observability.json`, P1/P2 (Phase 4).
- Spawn-mode gateway, label-based cleanup, `--rerun-failed-from` (Phase 3).
- `build.rs` changes — confirm none are needed (the generated include list already
  discovers new leaves; "add-a-test-case = add one file").

## Settled Decisions & Boundary Law (carry from the parent; do not cross)

- Black-box only: every sandbox/runtime op via `sandbox-cli` over the gateway
  socket. No test-injected `SandboxRuntime`, no manager/runtime internal-crate
  dependency, no `*_for_test` SQLite reader. Linux + Docker only.
- Sandbox ids are **runtime-assigned** — capture `/id` from the create response and
  round-trip it; for stateful chains, capture `command_session_id` /
  `workspace_session_id` from one response and feed the next call. Never predict an
  id or match a format.
- One cross-process env contract: **`EOS_E2E_RUN_ROOT`**; gateway socket, `run_id`,
  image from `{run_root}/run-manifest.json`.
- Discriminate success by **absence of the top-level `error` key**; expected
  failures via `err_kind_at` (manager semantic kinds `unknown_op` /
  `invalid_request` / `internal_error`; runtime uniformly `operation_failed` with
  detail under `error.details`). Assert field presence + type + invariants; assert
  CLI exit codes (0/1/2).
- v1 is **attach-only**; the real-runtime gateway is unshipped
  (`gateway/main.rs:94-146`). Phase 2 is buildable and skip-safe today; live green
  is gated on an externally supplied `--gateway-socket` (Stage 1) plus the landed
  runtime migration (Stage 2).

## Design Questions the Spec MUST Resolve (with live-code evidence)

1. **Stream/exit routing & `err_kind_at`.** The parent spec is self-contradictory
   ("carried error → stderr + exit 1" at one place; "error may arrive on stdout
   (exit 1)" at another). Resolve against LIVE code: `output.rs:266-272` (error →
   stderr + exit 1; ok → stdout + exit 0), usage error → stderr + exit 2
   (`:84-96`), and `cli_client.rs:72-77` (carrier = `exit==0 ? stdout : stderr`,
   `response_json` parsed from the carrier). Define `err_kind_at(rec, kind, exit)`
   to read `rec.response()` for the error object and assert `rec.exit_code == exit`
   AND `error.kind == kind`. State that the parent spec's "stdout (exit 1)" note is
   stale.
2. **`exchange.jsonl` ownership, keying, flush.** There is one shared sandbox-
   agnostic `CliClient` (`fixtures.rs`); the runtime-assigned id is known only
   *after* the create call (whose own record must land in that id's file); N1/N2
   have no sandbox. Resolve the writer's owner and lifetime (candidates: a
   per-`Sandbox` record buffer flushed to `reports/{id}/exchange.jsonl` on drop,
   seeded with the create record; a `RunReport` sink keyed by id with a bucket for
   sandbox-less calls; a `CliClient` sink handle). Pin the line schema
   (`{argv, request, response, exit_code, stdout, stderr, latency_ms}` + a
   `schema_version` header line) and note **`request` is `null`** on the black-box
   path (`cli_client.rs:12-13`). Keep it SRP and minimal.
3. **Stateful id round-trip (R3, R4→R5→R6).** Specify how a leaf captures
   `command_session_id` from R4's running-`cat` response and threads it into R5/R6,
   and how R3's second exec observes the first's write. Show the capture-and-feed as
   code-shaped pseudocode (like the parent's leaf example).
4. **`with_workspace_session` fixture (Stage 2).** Signature, the
   `create_workspace_session --profile host_compatible` call, the returned
   `/workspace_session_id`, and RAII `destroy_workspace_session` on drop — and how
   that RAII coexists with R7a (explicit clean destroy) and R7b (destroy rejected
   while a command is live → `operation_failed` + `error.details.active_command_session_ids`).
5. **Conditional fixtures (R7b busy, R8 squash-after-mutation).** Give deterministic
   command strings and ordering: how R7b keeps a command live across the destroy
   attempt (a still-running `command_session_id` from a `--yield-time-ms 0` exec),
   and how R8 commits a layer mutation (a file-writing command) before `squash` so
   `/squashed == true` and `/revision/root_hash` is non-empty.
6. **M2–M5 exact contracts.** Confirmed arg sets and response readers from the live
   impls: M2 `/sandboxes[]` contains `{id, state:"ready"}`; M3 `/id`,
   `/workspace_root`, `/state`, `/daemon`; M4 keys `resources,workspaces,recent_traces,errors`
   present, `/sandboxes/0/sandbox_id == id`, `/availability ∈ {available,partial,unavailable}`,
   and over-limit `--trace-limit 9999` is **clamped, not rejected**; M5 returned
   `/id == id` and a follow-up `inspect_sandbox` returns an `error` (removed).
7. **N1 / N2 exact contracts.** N1: `manager <unknown-op>` →
   `err_kind_at(rec, "unknown_op", 1)`, error on **stderr** (`dispatch.rs:8-31`,
   `output.rs:266-272`). N2: `runtime <op>` with no `--sandbox-id`/`--default-sandbox-id`
   → exit 2, stderr, message `"runtime operations require --sandbox-id or
   SANDBOX_DEFAULT_ID"`, kind `invalid_request` (`request_builder.rs:84-91`,
   `output.rs:287-292`). Note N2 never reaches the socket.
8. **Staging mechanics in the test tree.** Resolve how Stage 1 stays green while the
   runtime binary is dormant in a **pre-orchestrator** Phase 2: the Stage 1 green
   command is `cargo test --test manager`; R1 stays authored-but-not-run; R2–R8 are
   **not authored until Stage 2**. Confirm `tests/runtime.rs` (currently R1 only)
   still compiles and that a bare `cargo test` without `EOS_E2E_RUN_ROOT` skips
   cleanly in both stages.
9. **`build.rs` is unchanged.** Confirm the live walker + slug derivation
   (`build.rs`) discovers the new nested families (`observability`,
   `workspace_session`, `layerstack`, `routing/scope_and_dispatch`) collision-free,
   so no registry or root-file edit is needed. State the slug each new leaf yields.
10. **Workspace-root & slug provisioning at matrix scale.** Confirm
    `provision_sandbox`'s live `{run_root}/work/{run_id}-{slug}` (canonicalized,
    `fixtures.rs:61-74`) keeps every leaf's workspace root and report dir
    collision-free across the expanded matrix, and that the absolute-path check
    (`management/mod.rs:68`) is satisfied.
11. **Parallel-safety of the expanded matrix.** Confirm each leaf still owns exactly
    one sandbox and writes only under its own id, so `--test-threads=N` within a
    binary stays contention-free (no shared mutable state introduced by Phase 2).

## Required Deliverables (the generated spec must contain all of these)

1. **Phase boundary + two-stage statement** — one paragraph: what Phase 2 delivers
   over the live Phase 1 skeleton, the Stage 1 / Stage 2 partition and each stage's
   green criterion, and the explicit Phase 3–4 out-of-scope list.
2. **Resulting file/folder structure** — the `tests/` tree and `src/` deltas after
   Phase 2, each leaf tagged `←NEW (S1|S2)`, each edited harness file tagged `△`,
   with the `build.rs`-derived module slug beside each new leaf.
3. **Per-leaf test spec table** — one row per leaf (M2–M5, R1–R8, N1–N2):
   `{ path, #[test] fn name, stage, fixtures/preconditions, invocation, asserted
   fields }`, faithful to the parent matrix. For the stateful/conditional leaves
   (R3, R4→R5→R6, R7a/b, R8) add code-shaped pseudocode mirroring the parent spec's
   leaf example.
4. **`assertion.rs` delta** — signatures + one-line contract for `err_kind_at` (S1)
   and `err_detail` / `offsets_monotonic` / `non_decreasing` (S2), each naming the
   leaf(s) that use it, defined against the live routing.
5. **`exchange.jsonl` spec** — line schema + `schema_version` header, the resolved
   writer ownership/keying/flush, the `request == null` note, and the sandbox-less
   (N1/N2) handling.
6. **Fixture delta** — `with_workspace_session` (S2) signature, calls, and RAII;
   confirmation that `provision_sandbox` and `Sandbox` need no change for Stage 1.
7. **Verification & acceptance, per stage** — exact commands and pass criteria:
   bare `cargo test` skips clean (both stages); Stage 1 `cargo test --test manager`
   green; Stage 2 full `cargo test` green; the hand-written `run-manifest.json` +
   `EOS_E2E_RUN_ROOT` recipe; and the honest gate notes (Stage 1 needs the external
   real-runtime gateway, Open Items #1; Stage 2 additionally needs the landed
   runtime migration).
8. **Anchor ledger** — a table of every `file:line` the spec relies on with a
   `confirmed`/`corrected` verdict, R-series rows additionally marked
   **re-verify at Stage 2**.
9. **Conventions checklist** — SRP/one-job-per-unit; no inline comments in
   production code (test intent comments allowed); workspace deps via
   `dep.workspace = true`; `#[path]`/`include!` + generated list; clippy lints
   (`unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks`).

## Ground Rules

- Live code wins over both the parent spec and the Phase 0–1 spec; cite `file:line`
  for every load-bearing fact and verify it before relying on it. The known
  live-vs-parent deltas (stream routing; `{run_root}/work/{run_id}-{slug}`;
  `provision_sandbox` returning `(Sandbox, CallRecord)`; `request_json == None`;
  `assertion.rs` having only `ok`/`field` today) must be honored, not the prose.
- **Spec only** — do not implement the crate; produce the document.
- **Prefer less** — add no module, field, fixture, helper, or artifact Phase 2 does
  not need. Do not introduce `result.json`/`summary.json`/observability plumbing
  (Phase 3–4). Reuse the existing `CliClient::{manager,runtime}` verbs;
  Stage 1 needs no new CLI verb.
- **Hold the stage fence** — no Stage 1 leaf may issue a runtime op; every R-series
  response-shape decision is deferred to the settled migrated surface and tagged
  re-verify. `cargo test --test manager` must be provably green with zero runtime
  calls.
- Be honest about the gates: Stage 1 is green only against an external real-runtime
  gateway; Stage 2 is blocked until the `sandbox-runtime` migration lands. Say so
  where it matters; do not paper over it.

## Output

Write the result to `docs/e2e/sandbox-e2e-live-test-phase-2-spec.md`. Lead with the
phase boundary + two-stage statement, then the resulting tree, then the per-leaf
table (Stage 1 leaves first, then Stage 2), then the `assertion.rs` / `exchange.jsonl`
/ fixture deltas, then per-stage verification, then the anchor ledger. Prefer tables
and signature blocks over prose.

> This generator generalizes to Phases 3–4: swap the phase contract, the live
> anchors, and the in/out + stage scope. Phase 2 is the first phase split by the
> runtime-migration gate, so the manager/runtime stage fence and the
> `exchange.jsonl` ownership decision are its distinctive load-bearing calls.
