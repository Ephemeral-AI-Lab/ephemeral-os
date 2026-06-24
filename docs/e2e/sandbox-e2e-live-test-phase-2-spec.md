# `sandbox-e2e-live-test` — Phase 2 Implementation Spec (Full per-operation tree + assertions)

Implementation-ready spec for **Phase 2** of the parent design
(`docs/e2e/sandbox-e2e-live-test-spec.md`, `## Implementation Phases`). Phase 2
completes the black-box operation matrix (M1–M5, R1–R8, N1–N2) as one leaf file
per case, adds the four new `assertion.rs` helpers, the `with_workspace_session`
fixture, and the per-sandbox `exchange.jsonl` artifact — and **nothing else**.
This document is **spec only**; it does not create or edit any crate file.

The parent design is fixed; **live code wins on every conflict** and every
load-bearing fact is cited to a verified `file:line` (see the *Anchor Ledger*).
The known live-vs-parent deltas are honored here, not the parent prose: stream
routing (`error → stderr + exit 1`, `ok → stdout + exit 0`, `usage → stderr +
exit 2`); workspace root `{run_root}/work/{run_id}-{slug}` canonicalized;
`provision_sandbox` returns `(Sandbox, CallRecord)`; `request_json == None` on the
black-box path; `assertion.rs` has only `ok`/`field` today.

---

## 1. Phase boundary + two-stage statement

Phase 2 extends the **live Phase 1 skeleton** (`src/{assertion,cli_client,config,
fixtures,gateway,lib}.rs`, `tests/support/mod.rs`, `build.rs`, `tests/{manager,
runtime}.rs`, the M1 leaf `manager/lifecycle/create_sandbox/returns_ready.rs`, and
the dormant R1 leaf `runtime/command/exec_command/one_shot.rs`) by adding: every
remaining matrix leaf (M2–M5, R2–R8, N1–N2); the four assertion helpers
`err_kind_at`, `err_detail`, `offsets_monotonic`, `non_decreasing`; the
`with_workspace_session` RAII fixture; and the per-sandbox `exchange.jsonl`
artifact. It is delivered in **two stages** split on the in-flight
`sandbox-runtime` migration — the boundary is **binary-level** (the sole skip path
is `EOS_E2E_RUN_ROOT` unset, `support/mod.rs:7-9`, a bare `return` that writes
nothing; a runtime leaf run against a not-yet-migrated runtime would *fail*
`operation_failed`, never skip), so Stage 1 keeps the runtime test binary out of
its green target rather than adding a per-leaf readiness guard:

| Stage | Delivers | Green criterion (provably) |
|-------|----------|-----------------------------|
| **Stage 1 — manager surface** | Leaves M2–M5 (`lifecycle/{list,inspect,destroy}_sandbox`, `observability/get_observability_tree`), N1 (`routing/scope_and_dispatch/unknown_op`); the `err_kind_at` helper; `exchange.jsonl` capture wired through `Sandbox`. **Drives zero runtime ops.** | `cargo test --test manager` passes against a gateway that provisions/destroys sandboxes and serves `get_observability_tree`. |
| **Stage 2 — runtime surface** | Un-dormant R1; author R2–R8 (`command/{exec_command,write_command_stdin,read_command_lines}`, `workspace_session/{create,destroy}_workspace_session`, `layerstack/squash`), N2 (`routing/scope_and_dispatch/missing_sandbox_id`); the `with_workspace_session` fixture; the `err_detail`, `offsets_monotonic`, `non_decreasing` helpers. | full `cargo test` (manager **and** runtime) passes against the migrated real Docker runtime. |

Every R-series response-shape citation below is tagged **re-verify at Stage 2**:
R2–R8 are authored against the migrated runtime's *settled* shapes, never a moving
target. **N2 is CLI-side** (it fails at `resolve_runtime_sandbox_id`,
`request_builder.rs:84-91`, before the socket is touched, so it is technically
Stage-1-capable), but it is **grouped into Stage 2** for a clean test-binary
boundary: no leaf compiled into `tests/runtime` is green-targeted in Stage 1.

**Out of scope (Phases 3–4 — named, not designed):**

- Orchestrator bin internals: preflight, build phase, env export, the
  `--test {manager|runtime}` invocation policy, aggregation. `eos-e2e` stays the
  live Phase 0–1 print-and-exit stub; Phase 2 runs are by hand
  (`EOS_E2E_RUN_ROOT` + `cargo test`). **Phase 3.**
- `result.json`, `summary.json`, report-dir aggregation, timing/cleanup
  sub-objects. Phase 2 introduces **only** `exchange.jsonl`. The live skip path
  writes nothing and stays a bare `return`. **Phase 3.**
- Observability polling, `observability.json`, P1/P2. **Phase 4.**
- Spawn-mode gateway, label-based cleanup, `--rerun-failed-from`. **Phase 3.**
- `build.rs` changes — **none needed**; the live walker discovers each new leaf
  (Design Question 9). No new Cargo dependency — Phase 2 reuses
  `anyhow`/`serde`/`serde_json` already in `Cargo.toml`.

**Honest gates.** Stage 1 is green only against an externally started real-runtime
gateway attached via `--gateway-socket` (Open Items #1 — the shipped binary wires
`UnconfiguredRuntime`/`UnconfiguredDaemonInstaller`, `gateway/main.rs:94-100,
103-146`, which fail every `create_sandbox`). Stage 2 additionally requires the
landed `sandbox-runtime` migration. Both stages remain buildable and skip-clean
on any machine today.

---

## 2. Resulting file/folder structure

`←NEW (S1|S2)` marks files Phase 2 creates; `△` marks edited harness files. The
`build.rs`-derived module slug (path components after `<scope>` joined by `_`,
minus `.rs` — `build.rs:48-55`) is shown beside each new leaf.

```text
crates/sandbox-e2e-live-test/
  build.rs                                  # UNCHANGED (DQ 9)
  src/
    assertion.rs                            △ +err_kind_at (S1), +err_detail/+offsets_monotonic/+non_decreasing (S2)
    cli_client.rs                           △ +CallRecord::to_exchange_line() (S1; exchange.jsonl row)
    fixtures.rs                             △ +exchange sink on Sandbox (S1); +with_workspace_session/+WorkspaceSession (S2)
    report.rs                               ←NEW (S1)   # exchange.jsonl writer ONLY (no result/summary — Phase 3)
    lib.rs                                  △ +pub mod report; re-export WorkspaceSession
    config.rs, gateway.rs                   # UNCHANGED
    bin/eos-e2e.rs                          # UNCHANGED (Phase 0–1 stub)
  tests/
    support/mod.rs                          △ re-export WorkspaceSession (S2)
    manager.rs                              # UNCHANGED (stable 2-line root)
    manager/
      lifecycle/
        create_sandbox/returns_ready.rs     # M1 (live, Phase 1)            slug: lifecycle_create_sandbox_returns_ready
        list_sandboxes/lists_ready.rs       ←NEW (S1) M2                    slug: lifecycle_list_sandboxes_lists_ready
        inspect_sandbox/returns_record.rs   ←NEW (S1) M3                    slug: lifecycle_inspect_sandbox_returns_record
        destroy_sandbox/removes_sandbox.rs  ←NEW (S1) M5                    slug: lifecycle_destroy_sandbox_removes_sandbox
      observability/
        get_observability_tree/returns_tree.rs  ←NEW (S1) M4               slug: observability_get_observability_tree_returns_tree
      routing/
        scope_and_dispatch/unknown_op.rs    ←NEW (S1) N1                    slug: routing_scope_and_dispatch_unknown_op
    runtime.rs                              # UNCHANGED (stable 2-line root)
    runtime/
      command/
        exec_command/one_shot.rs            # R1 (live, dormant; S2 un-dormants) slug: command_exec_command_one_shot
        exec_command/in_session.rs          ←NEW (S2) R3                    slug: command_exec_command_in_session
        exec_command/long_running.rs        ←NEW (S2) R4                    slug: command_exec_command_long_running
        write_command_stdin/echoes_input.rs ←NEW (S2) R5                    slug: command_write_command_stdin_echoes_input
        read_command_lines/monotonic_offsets.rs ←NEW (S2) R6               slug: command_read_command_lines_monotonic_offsets
      workspace_session/
        create_workspace_session/host_compatible.rs ←NEW (S2) R2          slug: workspace_session_create_workspace_session_host_compatible
        destroy_workspace_session/clean.rs  ←NEW (S2) R7a                  slug: workspace_session_destroy_workspace_session_clean
        destroy_workspace_session/busy.rs   ←NEW (S2) R7b                  slug: workspace_session_destroy_workspace_session_busy
      layerstack/
        squash/after_mutation.rs            ←NEW (S2) R8                    slug: layerstack_squash_after_mutation
      routing/
        scope_and_dispatch/missing_sandbox_id.rs ←NEW (S2) N2             slug: routing_scope_and_dispatch_missing_sandbox_id
```

`report.rs` is the one new `src/` module: a single-job `exchange.jsonl` writer
(Deliverable 5). `fixtures.rs`, `cli_client.rs`, `assertion.rs`, `lib.rs`,
`support/mod.rs` are edited additively. No file is moved or renamed.

---

## 3. Per-leaf test spec table

Each leaf opens with the skip guard `let Some(h) = support::harness() else {
return };` (`support/mod.rs:7-9`) and (for sandbox-owning leaves) provisions via
`h.provision_sandbox(slug, None)` which returns `(Sandbox, CallRecord)`
(`fixtures.rs:59-88`). The RAII `Sandbox` reaps on drop (`fixtures.rs:99-107`).
`assert` = the `assertion` re-export (`support/mod.rs:3`).

### Stage 1 — manager surface (drives zero runtime ops)

| # | Path / fn | Stage | Preconditions | Invocation | Asserted fields |
|---|-----------|-------|---------------|------------|-----------------|
| M2 | `lifecycle/list_sandboxes/lists_ready.rs` · `list_sandboxes_contains_ready_sandbox` | S1 | one provisioned sandbox | `h.cli().manager("list_sandboxes", &[])` | `ok`; `/sandboxes` is array; some element has `/id == sb.id` **and** `/state == "ready"` (`list_sandboxes.rs:24-25`, `mod.rs:82-95`, `model.rs:70`) |
| M3 | `lifecycle/inspect_sandbox/returns_record.rs` · `inspect_sandbox_returns_full_record` | S1 | one provisioned sandbox | `h.cli().manager("inspect_sandbox", &["--sandbox-id", &sb.id])` | `ok`; `/id == sb.id`; `/workspace_root`, `/state`, `/daemon` present (`inspect_sandbox.rs:14-22,38-41`, `mod.rs:88-95`) |
| M4 | `observability/get_observability_tree/returns_tree.rs` · `observability_tree_lists_sandbox_and_clamps_over_limit` | S1 | one provisioned sandbox | `h.cli().manager("get_observability_tree", &["--sandbox-id", &sb.id, "--include-recent-traces", "1", "--trace-limit", "100"])`; then a second call with `--trace-limit 9999` | `ok` (both); `/sandboxes/0/sandbox_id == sb.id`; `/sandboxes/0/availability ∈ {available,partial,unavailable}`; node keys `resources`,`workspaces`,`recent_traces`,`errors` present; over-limit call also returns `ok` (clamped, not rejected) (`get_observability_tree.rs:26-67,253-300`, clamp `service.rs:518,521`) |
| M5 | `lifecycle/destroy_sandbox/removes_sandbox.rs` · `destroy_sandbox_removes_then_inspect_errors` | S1 | one provisioned sandbox | `h.cli().manager("destroy_sandbox", &["--sandbox-id", &sb.id])`; then `inspect_sandbox --sandbox-id sb.id` | first: `ok`, `/id == sb.id` (`destroy_sandbox.rs:16-24,76-78`); follow-up inspect: `err_kind_at(rec, "invalid_request", 1)` — removed id → `store.inspect` returns `MissingSandbox` (`store.rs:60-65`) → `INVALID_REQUEST` (`error.rs:53,55,64-66`). See DQ 6 note on M5's `Sandbox` drop |
| N1 | `routing/scope_and_dispatch/unknown_op.rs` · `unknown_manager_op_is_unknown_op` | S1 | gateway up (no sandbox) | `h.cli().manager("definitely_not_an_op", &[])` | `err_kind_at(rec, "unknown_op", 1)`; error rendered to **stderr** (`dispatch.rs:14`, `response.rs:25-27`, `output.rs:266-272`) |

### Stage 2 — runtime surface (**every row re-verify at Stage 2**)

| # | Path / fn | Stage | Preconditions | Invocation | Asserted fields |
|---|-----------|-------|---------------|------------|-----------------|
| R1 | `command/exec_command/one_shot.rs` · `one_shot_exec_returns_ok_and_zero_exit` | S2 | provisioned sandbox | `h.cli().runtime(&sb.id, "exec_command", &["pwd"])` | `ok`; `/status == "ok"`; `/exit_code == 0`; no `command_session_id` (live leaf; `command_operations.rs:281,324-340`, `contract.rs:44`) |
| R2 | `workspace_session/create_workspace_session/host_compatible.rs` · `create_workspace_session_host_compatible` | S2 | provisioned sandbox | `h.cli().runtime(&sb.id, "create_workspace_session", &["--profile", "host_compatible"])` | `ok`; `/workspace_session_id` non-empty string; `/profile == "host_compatible"` (`workspace_session_operations.rs:42-51,231-236`, `model.rs:115`) |
| R3 | `command/exec_command/in_session.rs` · `session_exec_observes_prior_write` | S2 | `with_workspace_session` | exec `sh -c "echo hi > f"` in session, then exec `sh -c "cat f"` in same session | both `ok`, `/status == "ok"`; second exec's `/output` contains `hi` (state persists across session execs) — pseudocode below |
| R4 | `command/exec_command/long_running.rs` · `long_running_exec_yields_session_id` | S2 | provisioned sandbox | `h.cli().runtime(&sb.id, "exec_command", &["--yield-time-ms", "0", "cat"])` | `ok`; `/status == "running"`; `field(resp,"/command_session_id")` is non-empty string (`command_operations.rs:278-279,336-337`, `contract.rs:43`) |
| R5 | `command/write_command_stdin/echoes_input.rs` · `write_stdin_echoes_into_output` | S2 | a live `command_session_id` from `cat` (R4-shape) | `write_command_stdin --command-session-id {cmd} hello` | `ok`; `/start_offset`,`/end_offset` parse as u64; `offsets_monotonic`; `/output` reflects `hello` (`command_operations.rs:96-125,324-340`) — pseudocode below |
| R6 | `command/read_command_lines/monotonic_offsets.rs` · `read_lines_offsets_non_decreasing` | S2 | live `cat` session + one stdin write | `read_command_lines --command-session-id {cmd} --start-offset 0 --limit 100`; then re-read from prior `/end_offset` | both `ok`; first: `/command_session_id == cmd`, `offsets_monotonic`; second: `non_decreasing(prev,next,"/start_offset")` (`command_operations.rs:143-173,342-355`) — pseudocode below |
| R7a | `workspace_session/destroy_workspace_session/clean.rs` · `destroy_idle_session_succeeds` | S2 | `create_workspace_session`, no live cmds | `destroy_workspace_session --workspace-session-id {ws}` | `ok`; `/destroyed == true` (`workspace_session_operations.rs:70-90,238-243`). Created **inline** (not via `with_workspace_session`) so the explicit destroy is the asserted call — see DQ 4 |
| R7b | `workspace_session/destroy_workspace_session/busy.rs` · `destroy_busy_session_rejected` | S2 | session + a still-running `cat` started in it (`--yield-time-ms 0`) | `destroy_workspace_session --workspace-session-id {ws}` while cmd live | `err_kind_at(rec,"operation_failed",1)`; `err_detail(resp,"/active_command_session_ids")` is a non-empty array (`workspace_session_operations.rs:132-138,218-229`) — pseudocode below |
| R8 | `layerstack/squash/after_mutation.rs` · `squash_after_mutation_reports_revision` | S2 | a committed file-writing command first | run `exec_command "sh -c 'echo x > /root/eos_e2e_squash'"`; then `h.cli().runtime(&sb.id, "squash", &[])` | `ok`; `/squashed == true`; `/revision/root_hash` present and non-empty (`layerstack_operations.rs:22,59-80`) — pseudocode below |
| N2 | `routing/scope_and_dispatch/missing_sandbox_id.rs` · `runtime_op_without_sandbox_id_is_usage_error` | S2 (CLI-side) | gateway up (no sandbox) | `h.cli().manager(...)` cannot express this; call a **bare runtime op with no id** — see DQ 7 invocation note | exit `2`; error on **stderr**; `err_kind_at(rec,"invalid_request",2)`; message `"runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID"` (`request_builder.rs:84-91`, `output.rs:145-151,287-292`). Never reaches the socket |

### Stateful / conditional leaf pseudocode (mirrors the parent leaf example)

These leaves capture an id from one response and feed it into the next call —
never predicting or formatting an id (Settled Decisions). The R-series shapes are
**re-verify at Stage 2**.

```rust
// R4 → long_running.rs : capture a live command_session_id from a running `cat`.
let (sb, _create) = h.provision_sandbox("command-exec_command-long_running", None);
let rec = h.cli().runtime(&sb.id, "exec_command", &["--yield-time-ms", "0", "cat"]);
let resp = rec.response();
assert::ok(resp);
assert_eq!(assert::field(resp, "/status"), "running");                 // still running
let cmd = assert::field(resp, "/command_session_id").as_str().unwrap(); // present iff running
assert!(!cmd.is_empty());
```

```rust
// R5 → echoes_input.rs : feed the captured command_session_id into write_command_stdin.
let (sb, _create) = h.provision_sandbox("command-write_command_stdin-echoes_input", None);
let cmd = {
    let r = h.cli().runtime(&sb.id, "exec_command", &["--yield-time-ms", "0", "cat"]);
    assert::field(r.response(), "/command_session_id").as_str().unwrap().to_owned()
};
let rec = h.cli().runtime(&sb.id, "write_command_stdin", &["--command-session-id", &cmd, "hello"]);
let resp = rec.response();
assert::ok(resp);
assert::offsets_monotonic(resp);                                        // start ≤ end ≤ total
assert!(assert::field(resp, "/start_offset").is_u64());
assert!(assert::field(resp, "/output").as_str().unwrap().contains("hello"));
```

```rust
// R6 → monotonic_offsets.rs : two reads; second starts at the first's end_offset.
let (sb, _create) = h.provision_sandbox("command-read_command_lines-monotonic_offsets", None);
let cmd = { /* start `cat`, capture command_session_id as in R5 */ };
let _ = h.cli().runtime(&sb.id, "write_command_stdin", &["--command-session-id", &cmd, "line-one"]);
let first = h.cli().runtime(&sb.id, "read_command_lines",
    &["--command-session-id", &cmd, "--start-offset", "0", "--limit", "100"]);
let fp = first.response();
assert::ok(fp);
assert_eq!(assert::field(fp, "/command_session_id"), cmd.as_str());
assert::offsets_monotonic(fp);
let end = assert::field(fp, "/end_offset").as_u64().unwrap();
let second = h.cli().runtime(&sb.id, "read_command_lines",
    &["--command-session-id", &cmd, "--start-offset", &end.to_string(), "--limit", "100"]);
let sp = second.response();
assert::ok(sp);
assert::non_decreasing(fp, sp, "/start_offset");                        // next.start ≥ prev.start
```

```rust
// R3 → in_session.rs : two execs in one workspace session; second observes the first's write.
let (sb, _create) = h.provision_sandbox("command-exec_command-in_session", None);
let ws = support::WorkspaceSession::create(h, &sb.id);                  // RAII; destroy on drop (DQ 4)
let w = h.cli().runtime(&sb.id, "exec_command",
    &["--workspace-session-id", ws.id(), "sh", "-c", "echo hi > f"]);
assert::ok(w.response());
let r = h.cli().runtime(&sb.id, "exec_command",
    &["--workspace-session-id", ws.id(), "sh", "-c", "cat f"]);
let rp = r.response();
assert::ok(rp);
assert_eq!(assert::field(rp, "/status"), "ok");
assert!(assert::field(rp, "/output").as_str().unwrap().contains("hi")); // state persisted
// ws drops -> destroy_workspace_session; sb drops -> destroy_sandbox
```

```rust
// R7b → busy.rs : destroy is rejected while a command is live in the session.
let (sb, _create) = h.provision_sandbox("workspace_session-destroy_workspace_session-busy", None);
let ws_id = {
    let r = h.cli().runtime(&sb.id, "create_workspace_session", &["--profile", "host_compatible"]);
    assert::field(r.response(), "/workspace_session_id").as_str().unwrap().to_owned()
};
let _live = h.cli().runtime(&sb.id, "exec_command",
    &["--workspace-session-id", &ws_id, "--yield-time-ms", "0", "cat"]);   // stays running
let rec = h.cli().runtime(&sb.id, "destroy_workspace_session", &["--workspace-session-id", &ws_id]);
let resp = rec.response();
assert::err_kind_at(&rec, "operation_failed", 1);
assert!(!assert::err_detail(resp, "/active_command_session_ids").as_array().unwrap().is_empty());
// no RAII WorkspaceSession here: destroy is the asserted call and is expected to be rejected
```

```rust
// R8 → after_mutation.rs : commit a layer mutation before squash so /squashed == true.
let (sb, _create) = h.provision_sandbox("layerstack-squash-after_mutation", None);
let m = h.cli().runtime(&sb.id, "exec_command",
    &["sh", "-c", "echo x > /root/eos_e2e_squash"]);                    // one-shot, terminal commit
assert::ok(m.response());
let rec = h.cli().runtime(&sb.id, "squash", &[]);
let resp = rec.response();
assert::ok(resp);
assert_eq!(assert::field(resp, "/squashed"), true);
assert!(!assert::field(resp, "/revision/root_hash").as_str().unwrap().is_empty());
```

> R3/R8 command strings (`sh -c "echo … > f"` / writing `/root/eos_e2e_squash`)
> and the working directory of the one-shot/session are **re-verify at Stage 2**:
> confirm the migrated runtime's default workspace cwd and that a one-shot exec
> commits a squashable layer. R7b relies on `--yield-time-ms 0` leaving `cat`
> running (`command_operations.rs:64-73`) and on the destroy-admission active-id
> check (`workspace_session_operations.rs:132-138`).

---

## 4. `assertion.rs` delta

Today `assertion.rs` ships **only** `ok` (`:4-9`) and `field` (`:12-16`). Phase 2
adds four helpers, defined against **live routing** — `cli_client.rs:72-77`
(`carrier = exit==0 ? stdout : stderr`, `response_json` parsed from the carrier)
and `output.rs:266-272` (`error → stderr + exit 1`, `ok → stdout + exit 0`),
usage `→ stderr + exit 2` (`output.rs:84-96,145-151,287-292`). **The parent
spec's "error may arrive on stdout (exit 1)" / "stdout (exit 1)" note is STALE**:
a carried `error` is always written to stderr, and `CallRecord::response()` reads
whichever stream the exit code selects, so the error object is reachable through
`rec.response()` regardless of stream.

```rust
use serde_json::Value;
use crate::cli_client::CallRecord;

// ── Stage 1 ───────────────────────────────────────────────────────────────────

/// Assert the carried response is an error with `error.kind == kind` AND
/// `rec.exit_code == exit`. Reads `rec.response()` (parsed from the carrier
/// stream, cli_client.rs:72-77); the error object is `{kind,message,details}`
/// (response.rs:41-49). Routing: manager semantic errors → exit 1/stderr
/// (output.rs:266-272); CLI usage/build errors → exit 2/stderr
/// (output.rs:84-96,145-151,287-292).
/// Used by: N1 (unknown_op,1), M5 follow-up inspect (invalid_request,1),
/// R7b (operation_failed,1), N2 (invalid_request,2).
pub fn err_kind_at(rec: &CallRecord, kind: &str, exit: i32);

// ── Stage 2 (re-verify at Stage 2) ─────────────────────────────────────────────

/// JSON-pointer get-or-panic rooted at `error.details` of a runtime
/// `operation_failed` response (runtime errors are uniformly operation_failed,
/// response.rs:20-22; details carry the discriminator). `err_detail(resp,"/p")`
/// ≡ `field(resp,"/error/details/p")`.
/// Used by: R7b (`/active_command_session_ids`, workspace_session_operations.rs:218-229).
pub fn err_detail<'a>(resp: &'a Value, ptr: &str) -> &'a Value;

/// Within one response assert `start_offset ≤ end_offset ≤ total_lines`, each a
/// u64 (command yield/lines value, command_operations.rs:324-355).
/// Used by: R5, R6.
pub fn offsets_monotonic(resp: &Value);

/// Across two responses assert `next[ptr] >= prev[ptr]` (both u64). Expresses the
/// cross-read offset invariant a single-response check cannot.
/// Used by: R6 (`/start_offset`).
pub fn non_decreasing(prev: &Value, next: &Value, ptr: &str);
```

`err_kind_at` is the **only** helper Stage 1 needs (Stage 1 has no runtime
`operation_failed` body and no offsets). The three Stage-2 helpers are authored
with R2–R8. None introduce inline comments in production code; doc comments on
public items are allowed (CLAUDE.md).

---

## 5. `exchange.jsonl` spec

Phase 2's only new artifact. **One job:** persist, per sandbox, the ordered
`sandbox-cli` call records this run made against that sandbox. SRP-minimal — it is
**not** `result.json`/`summary.json` (Phase 3) and does **not** snapshot
observability (Phase 4).

**Line schema** (one JSON object per line; `\n`-terminated). A **header line**
carries `schema_version`; every subsequent line is one call record:

```text
{"schema_version":1}
{"argv":[…],"request":null,"response":{…},"exit_code":0,"stdout":"…","stderr":"","latency_ms":12}
```

Per-record fields map 1:1 from the live `CallRecord` (`cli_client.rs:11-19`):

| field | source | note |
|-------|--------|------|
| `argv` | `CallRecord::argv` | the `sandbox-cli` argv after `--gateway-socket …` (`cli_client.rs:56-60`) |
| `request` | `CallRecord::request_json` | **always `null`** on the black-box path — the CLI writes the wire request only to the socket, never to stdio (`cli_client.rs:12-13,81`); the field exists for parity |
| `response` | `CallRecord::response_json` | parsed from the carrier stream (`cli_client.rs:72-77`) |
| `exit_code` | `CallRecord::exit_code` | 0/1/2 |
| `stdout`, `stderr` | `CallRecord::{stdout,stderr}` | both streams retained |
| `latency_ms` | `CallRecord::latency_ms` | wall-clock around the spawn (`cli_client.rs:62-67`) |

**Writer ownership / keying / flush (resolved — DQ 2).** The sink is **owned by
`Sandbox`**, keyed by the runtime-assigned id, written to
`{run_root}/reports/{sb.id}/exchange.jsonl`:

- `Sandbox` gains a record buffer `Vec<CallRecord>` (private) and a `run_root:
  PathBuf` (so drop can resolve the report dir without re-reading the manifest).
  `provision_sandbox` seeds the buffer with the **create `CallRecord`** it already
  holds (`fixtures.rs:76-87`) — the create record lands in *that id's* file, which
  is only possible after `/id` is read, so the buffer is seeded at construction.
- Every leaf-issued call is appended via a new `Sandbox::record(&self, rec) ->
  &Value` shim (interior-mutable buffer via `RefCell`; tests are single-threaded
  per `#[test]`, and each `Sandbox` is leaf-local, so no cross-thread sharing —
  DQ 11). Leaves that want the parsed body call `sb.record(rec).` Helper-only
  leaves may also keep calling `rec.response()` directly; recording is additive.
- **Flush on `Drop`**, before `destroy_sandbox`: `report::write_exchange(run_root,
  &id, &records)` creates `reports/{id}/` and writes the header line then one line
  per record. Drop already fires `destroy_sandbox` (`fixtures.rs:99-107`); the
  flush is prepended. A write error in drop is swallowed (best-effort artifact,
  like the existing `let _ =` destroy).

This keeps the **one shared `CliClient`** sandbox-agnostic (no per-id state on the
client) and puts the id↔file binding on the object that owns the id.

**Sandbox-less calls (N1, N2).** N1 and N2 own **no** `Sandbox`, so they write
**no** `exchange.jsonl` — there is no id to key on, and Phase 2 deliberately adds
no run-level sink (that is the Phase 3 `RunReport`). They assert purely on the
returned `CallRecord` in-process. This is consistent with the live skip path
writing nothing; no artifact is lost that Phase 2 owns.

**`report.rs` surface (new, S1):**

```rust
use std::path::Path;
use crate::cli_client::CallRecord;

const EXCHANGE_SCHEMA_VERSION: u32 = 1;

/// Write `{run_root}/reports/{sandbox_id}/exchange.jsonl`: a `{schema_version}`
/// header line followed by one JSON object per call record. Creates the report
/// dir. Best-effort: returns `io::Result` so the caller (Sandbox::drop) can
/// swallow failures without aborting teardown.
pub fn write_exchange(run_root: &Path, sandbox_id: &str, records: &[CallRecord]) -> std::io::Result<()>;
```

`CallRecord` gains a `to_exchange_line(&self) -> Value` (or the writer builds the
object inline) producing `{argv,request,response,exit_code,stdout,stderr,
latency_ms}` — chosen so `report.rs` does not duplicate field names.

---

## 6. Fixture delta

**Stage 1: no `fixtures.rs` signature change for provisioning.** `provision_sandbox
-> (Sandbox, CallRecord)` (`fixtures.rs:59-88`) and the `Sandbox{id,
workspace_root}` shape (`:94-97`) are **unchanged** for Stage 1's behavior; the
only additive change is the `exchange.jsonl` sink described in §5 (buffer + drop
flush). M2–M5/N1 need no new provisioning capability.

**Stage 2: add `WorkspaceSession` (RAII) — DQ 4.** A second RAII guard over the
runtime workspace-session lifecycle, used by R2/R3 (and *not* by R7a/R7b, which
drive the destroy explicitly):

```rust
use crate::cli_client::CliClient;

/// RAII runtime workspace session. `create` issues
/// `runtime --sandbox-id {id} create_workspace_session --profile host_compatible`
/// and captures `/workspace_session_id` (runtime-assigned, round-tripped). On
/// drop it issues `destroy_workspace_session --workspace-session-id {id}`
/// (best-effort, idempotent), so an in-session leaf is teardown-safe on panic.
pub struct WorkspaceSession<'h> {
    cli: &'h CliClient,
    sandbox_id: String,
    workspace_session_id: String,
}

impl<'h> WorkspaceSession<'h> {
    /// Create a host_compatible session under `sandbox_id` and capture its id.
    pub fn create(h: &'h Harness, sandbox_id: &str) -> Self;   // re-verify shape at Stage 2
    pub fn id(&self) -> &str;                                  // workspace_session_id
}

impl Drop for WorkspaceSession<'_> {
    fn drop(&mut self);  // runtime destroy_workspace_session --workspace-session-id id
}
```

**Coexistence with R7a / R7b (DQ 4):**

- **R7a (clean destroy)** creates the session **inline** (not via
  `WorkspaceSession`) so the *explicit* `destroy_workspace_session` is the asserted
  call and there is no double-destroy on drop. The session is gone after the
  asserted call; no RAII guard is held.
- **R7b (busy destroy)** also creates the session **inline** and starts a live
  `cat` in it; the asserted `destroy_workspace_session` is **expected to be
  rejected** (`operation_failed` + `active_command_session_ids`,
  `workspace_session_operations.rs:218-229`), so the session is intentionally
  still alive at scope end. The owning `Sandbox` drop destroys the whole sandbox,
  reaping the session and the live command together — no orphan. A
  `WorkspaceSession` RAII guard here would try (and fail) a redundant destroy, so
  R7b uses the inline form.
- **R2/R3** use `WorkspaceSession::create` and let drop clean up — they assert on
  creation (R2) or in-session execs (R3), not on destroy.

`with_workspace_session` is named after the parent's fixture; this spec realizes
it as the `WorkspaceSession` RAII guard + `support::WorkspaceSession` re-export
(`support/mod.rs`). It is **Stage 2 only**; Stage 1 never references it.

---

## 7. Verification & acceptance, per stage

All commands from the repo root with repo-local tools on `PATH`
(`export PATH="$PWD/bin:$PATH"`, CLAUDE.md — makes `sandbox-cli` resolve to
`bin/sandbox-cli`).

### Skip-clean (both stages, any machine, no gateway)

```sh
cargo build  -p sandbox-e2e-live-test
cargo clippy -p sandbox-e2e-live-test --all-targets        # workspace lints
cargo test   -p sandbox-e2e-live-test                      # EOS_E2E_RUN_ROOT unset
```

Pass: build + clippy exit 0; `cargo test` exits 0 with **every** leaf
early-returning (skip) and **nothing written** (`support/mod.rs:7-9`,
`fixtures.rs:25-28` cache `None`). Holds for both stages — the Stage 2 runtime
leaves compile and skip exactly like the manager leaves.

### Stage 1 — green (manager binary only)

Requires a Linux host with Docker, the image present, and an **externally started
real-runtime gateway** attached via its socket. Hand-write the manifest, then run
the **manager binary only**:

```sh
RUN_ROOT=$(mktemp -d)
cat > "$RUN_ROOT/run-manifest.json" <<'JSON'
{ "schema_version": 1,
  "gateway_socket": "/tmp/eos-real-runtime-gateway.sock",
  "run_id": "p2-s1",
  "image": "ubuntu:24.04" }
JSON
EOS_E2E_RUN_ROOT="$RUN_ROOT" cargo test -p sandbox-e2e-live-test --test manager -- --test-threads=4
```

Pass: M1–M5 + N1 green; `reports/{id}/exchange.jsonl` written per provisioned
sandbox (header line + record lines); **zero** `runtime` ops issued (provable —
no leaf compiled into `tests/manager` calls `h.cli().runtime(...)`; the only
runtime caller is `tests/runtime/**`, which `--test manager` does not build into
this binary). Gate: needs the external real-runtime gateway (Open Items #1).

### Stage 2 — green (full suite)

After the `sandbox-runtime` migration lands and the attached gateway serves the
R-series:

```sh
EOS_E2E_RUN_ROOT="$RUN_ROOT" cargo test -p sandbox-e2e-live-test -- --test-threads=4
# or focused: … --test runtime -- command_exec_command --test-threads=4
```

Pass: manager **and** runtime binaries green; R1 un-dormant; R2–R8, N2 green;
stateful chains (R4→R5→R6) round-trip `command_session_id`; R3 round-trips
`workspace_session_id`; R7b shows `active_command_session_ids`; R8 shows a
non-empty `/revision/root_hash`. Gate: needs the landed migration **and** the
real-runtime gateway.

The `run-manifest.json` schema is unchanged from Phase 1 (`config.rs:21-27`);
Phase 2 adds no manifest field.

---

## 8. Anchor ledger

Every `file:line` the spec relies on, re-opened in this run. R-series rows marked
**re-verify at Stage 2** (authored against the migrated runtime's settled shapes).

| Anchor | Verdict | Confirmed fact |
|--------|---------|----------------|
| `Cargo.toml:18` | confirmed | `"crates/sandbox-e2e-live-test"` is a workspace member (after `"xtask"` :17). |
| `Cargo.toml:73` | confirmed | `[workspace.lints.clippy]`. |
| `Cargo.toml:81-83` | confirmed | `unwrap_used="warn"` (:81), `dbg_macro="warn"` (:82), `undocumented_unsafe_blocks="deny"` (:83). |
| `crates/sandbox-e2e-live-test/Cargo.toml` | confirmed | deps = `anyhow`,`serde`,`serde_json` only; `[lints] workspace=true`. Phase 2 adds none. |
| `…/src/cli_client.rs:11-19` | confirmed | `CallRecord{argv,request_json:Option,response_json,exit_code,stdout,stderr,latency_ms}`. |
| `…/src/cli_client.rs:12-13,81` | confirmed | `request_json` is `None` on the black-box path. |
| `…/src/cli_client.rs:37-53` | confirmed | `manager(op,args)` / `runtime(sandbox_id,op,args)` verbs (reused; Stage 1 needs no new verb). |
| `…/src/cli_client.rs:62-67` | confirmed | wall-clock `latency_ms` around `Command::output()`. |
| `…/src/cli_client.rs:72-77` | confirmed | `carrier = exit==0 ? stdout : stderr`; `response_json` parsed from carrier. |
| `…/src/cli_client.rs:96-99` | confirmed | `CallRecord::response()` returns `&response_json`. |
| `…/src/fixtures.rs:25-28` | confirmed | `Harness::get` is `OnceLock<Option<Harness>>`; unset env caches `None`. |
| `…/src/fixtures.rs:59-88` | confirmed | `provision_sandbox(slug,image)->(Sandbox,CallRecord)`; one `create_sandbox`; id from `/id`. |
| `…/src/fixtures.rs:61-74` | confirmed | workspace root `{run_root}/work/{run_id}-{slug}` created + canonicalized (absolute). |
| `…/src/fixtures.rs:94-107` | confirmed | `Sandbox{id,workspace_root}`; `Drop` → `destroy_sandbox --sandbox-id id` (best-effort). |
| `…/src/assertion.rs:4-16` | confirmed | only `ok` (:4-9) and `field` (:12-16) exist today; Phase 2 adds the four helpers. |
| `…/src/config.rs:21-27` | confirmed | manifest fields `schema_version,gateway_socket,run_id,image`; schema_version 1. |
| `…/src/gateway.rs:12-26` | confirmed | `await_ready` attach-only socket readiness (no spawn). |
| `…/tests/support/mod.rs:3-9` | confirmed | re-exports `assertion` + `Harness`; `harness()->Option`; the ONLY skip path. |
| `…/build.rs:14-32` | confirmed | walks `tests/<scope>`, sorts leaves, emits `#[path] mod <slug>;` per leaf + `rerun-if-changed`. |
| `…/build.rs:48-55` | confirmed | slug = path components after scope joined by `_`, minus `.rs`; collision-free. UNCHANGED for Phase 2. |
| `…/tests/manager.rs:1-4` / `runtime.rs:1-4` | confirmed | stable 2-line roots; `include!(OUT_DIR/<scope>_mods.rs)`. UNCHANGED. |
| `sandbox-manager/.../management/list_sandboxes.rs:9,24-25` | confirmed | M2: no args; `Response::ok(records_value(...))`. |
| `sandbox-manager/.../management/inspect_sandbox.rs:14-22,38-41` | confirmed | M3: required `--sandbox-id`; `Response::ok(record_value(record))`. |
| `sandbox-manager/.../management/destroy_sandbox.rs:16-24,76-78` | confirmed | M5: required `--sandbox-id`; success returns `record_value` of removed record; rejects Creating/Stopping (:44-54). |
| `sandbox-manager/src/store.rs:60-65` | confirmed | `inspect` on a removed id → `ManagerError::MissingSandbox`; `remove` likewise (:66-70). M5 double-destroy via drop hits `MissingSandbox`, harmless (best-effort `let _ =`). |
| `sandbox-manager/src/error.rs:53,55,64-66` | confirmed | `MissingSandbox` → `INVALID_REQUEST` (:53,55); `into_response`→`Response::fault(kind,...)` (:64-66). M5 follow-up inspect kind is `invalid_request`. |
| `sandbox-manager/.../management/get_observability_tree.rs:26-67` | confirmed | M4 args: opt `--sandbox-id`, `--include-recent-traces`(def "0"), `--trace-limit`(def "20"), `--resource-window-ms`. |
| `sandbox-manager/.../management/get_observability_tree.rs:106,253-300` | confirmed | returns `{sandboxes:[…]}`; per-node keys `sandbox_id,lifecycle_state,availability,errors,daemon,resources,workspaces,recent_traces`. |
| `sandbox-manager/.../management/get_observability_tree.rs:274-281` | confirmed | `availability ∈ {available,partial,unavailable}` (else normalized to "partial"). |
| `sandbox-manager/.../management/mod.rs:68` | confirmed | absolute `workspace_root` check (`!is_absolute()` → InvalidWorkspaceRoot). |
| `sandbox-manager/.../management/mod.rs:82-95` | confirmed | `records_value`/`record_value`: `{id,workspace_root,state,daemon}`. |
| `sandbox-daemon/src/observability/service.rs:30-31,518,521` | confirmed | `MAX_TRACE_LIMIT=100`/`MAX_RESOURCE_WINDOW_MS=600000`; applied via `.min(...)` → **clamp, not reject** (M4). |
| `sandbox-manager/src/router/dispatch.rs:9-31` | confirmed | (System,unknown)→`unknown_op` (:14); (Sandbox,manager-owned)→`invalid_request` (:15-18). |
| `sandbox-protocol/src/response.rs:20-22` | confirmed | `service_error`→`operation_failed`. |
| `sandbox-protocol/src/response.rs:25-27` | confirmed | `unknown_op`→kind `unknown_op`. |
| `sandbox-protocol/src/response.rs:41-49` | confirmed | error shape `{error:{kind,message,details}}`. |
| `sandbox-protocol/src/error_kind.rs:2-3` | confirmed | `INTERNAL_ERROR="internal_error"`, `INVALID_REQUEST="invalid_request"`. |
| `sandbox-gateway/src/cli/output.rs:21-23` | confirmed | EXIT_SUCCESS=0, EXIT_FAILURE=1, EXIT_USAGE=2. |
| `sandbox-gateway/src/cli/output.rs:84-96` | confirmed | clap parse error → stderr + exit 2. |
| `sandbox-gateway/src/cli/output.rs:145-151` | confirmed | runtime sandbox-id resolution error → `render_request_error` (stderr) + EXIT_USAGE(2) — **before** the socket (N2). |
| `sandbox-gateway/src/cli/output.rs:266-272` | confirmed | `render_response`: error→stderr+EXIT_FAILURE(1); ok→stdout+EXIT_SUCCESS(0). |
| `sandbox-gateway/src/cli/output.rs:287-292` | confirmed | `render_request_error` renders kind `invalid_request` to stderr. |
| `sandbox-gateway/src/cli/request_builder.rs:84-91` | confirmed | N2: missing id → `"runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID"`. |
| `sandbox-gateway/src/gateway/main.rs:94-100,103-146` | confirmed | `default_manager_services` wires `UnconfiguredRuntime`(:97,103)/`UnconfiguredDaemonInstaller`(:98,122); create_sandbox errors `"sandbox runtime is not configured"` (:110-112). |
| `sandbox-runtime/operation/src/cli_definition/command_operations.rs:34-74` | confirmed · **re-verify at Stage 2** | exec args: opt `--workspace-session-id`, req positional `cmd`(COMMAND), opt `--timeout-ms`, opt `--yield-time-ms`. |
| `…/command_operations.rs:96-125` | confirmed · **re-verify at Stage 2** | write_command_stdin: req `--command-session-id`, req positional `stdin`(TEXT), opt `--yield-time-ms`. |
| `…/command_operations.rs:143-173` | confirmed · **re-verify at Stage 2** | read_command_lines: req `--command-session-id`, opt `--start-offset`, opt `--limit`. |
| `…/command_operations.rs:278-281` | confirmed · **re-verify at Stage 2** | Running→`Response::running`, else `Response::ok`. |
| `…/command_operations.rs:324-340` | confirmed · **re-verify at Stage 2** | yield value: `status,exit_code,start_offset,end_offset,total_lines,output,…`; `command_session_id` set iff `Some` (:336-337). |
| `…/command_operations.rs:342-355` | confirmed · **re-verify at Stage 2** | lines value: always `command_session_id` (:344) + offset fields. |
| `…/command_operations.rs:296-298` | confirmed · **re-verify at Stage 2** | command errors → `operation_failed` (no `active_command_session_ids` here). |
| `command/service/contract.rs:41-44` | confirmed · **re-verify at Stage 2** | `CommandStatus::as_str`: Running→"running"(:43), Ok→"ok"(:44). |
| `workspace_session_operations.rs:42-51,231-236` | confirmed · **re-verify at Stage 2** | R2: opt `--profile`; returns `{workspace_session_id,profile}`. |
| `workspace_session_operations.rs:70-90,238-243` | confirmed · **re-verify at Stage 2** | R7a: req `--workspace-session-id`, opt `--grace-s`; clean → `{workspace_session_id,destroyed:true}`. |
| `workspace_session_operations.rs:132-138,218-229` | confirmed · **re-verify at Stage 2** | R7b: active-command admission → `operation_failed` + `error.details.active_command_session_ids` (array). |
| `workspace/src/model.rs:113-116` | confirmed · **re-verify at Stage 2** | `WorkspaceProfile::HostCompatible→"host_compatible"`(:115), Isolated→"isolated"(:116). |
| `layerstack_operations.rs:22,59-80` | confirmed · **re-verify at Stage 2** | R8 squash: no args; returns `{squashed,revision:{manifest_version,root_hash,layer_count}|null,layer_paths,lease_release_error}`. |
| `layerstack_operations.rs:48-56` | confirmed · **re-verify at Stage 2** | squash error → `operation_failed` + `{kind}`. |

---

## 9. Conventions checklist

- **SRP / one job per unit.** New `report.rs` = `exchange.jsonl` writing only (no
  result/summary/observability). New helpers each assert one invariant. New
  `WorkspaceSession` = one runtime-session lifecycle. The `Sandbox` exchange sink
  is the id↔file binding on the object that owns the id — not on the shared
  `CliClient`. Each leaf file owns exactly one matrix case.
- **No inline comments in production code.** `src/` additions (`report.rs`,
  fixture/assertion/cli_client deltas) carry only `///`/`//!` doc comments on
  public items. Leaf tests may carry intent comments (tests only, per CLAUDE.md),
  as the live M1/R1 leaves already do.
- **Workspace deps via `dep.workspace = true`.** Phase 2 introduces **no new
  dependency**; `exchange.jsonl` uses `serde_json` + `std::fs` already present
  (`crates/sandbox-e2e-live-test/Cargo.toml`). No version is pinned in the member.
- **`#[path]` / `include!` + generated list.** Every new leaf is discovered by the
  unchanged `build.rs` walker (slugs listed in §2); root binaries stay the live
  2-line `#[path] mod support; include!(OUT_DIR/<scope>_mods.rs)`. Add-a-case =
  add-one-file; no registry edit (DQ 9).
- **Clippy lints.** No `.unwrap()`/`expect`-on-`Option` in production `src/`
  (`unwrap_used="warn"`, `Cargo.toml:81`); no `dbg!` (`:82`); no `unsafe`
  introduced, so `undocumented_unsafe_blocks` (`:83`) is moot. `RefCell` for the
  exchange buffer keeps the sink single-threaded and lock-free.
  `cargo clippy -p sandbox-e2e-live-test --all-targets` is an acceptance gate for
  both stages. Test-side `.unwrap()` in pseudocode is illustrative; leaves should
  prefer the `assertion::*` helpers, which carry the panic messages.

---

### Design-question resolution index (for the Verifier)

| DQ | Resolved in | One-line resolution |
|----|-------------|---------------------|
| 1 stream/exit & `err_kind_at` | §4 | `err_kind_at(rec,kind,exit)` reads `rec.response()` (carrier-parsed) + `rec.exit_code`; parent "stdout (exit 1)" note is STALE. |
| 2 `exchange.jsonl` owner/key/flush | §5 | Owned by `Sandbox`, keyed by runtime id, seeded with the create record, flushed on drop to `reports/{id}/exchange.jsonl`; N1/N2 write nothing. |
| 3 stateful id round-trip | §3 pseudocode | R4 captures `/command_session_id`; R5/R6 feed it; R6 re-reads from prior `/end_offset`; R3 threads `workspace_session_id`. |
| 4 `with_workspace_session` | §6 | `WorkspaceSession` RAII (create host_compatible, capture id, destroy on drop); R7a/R7b use the inline form to own the destroy. |
| 5 conditional fixtures | §3 pseudocode | R7b keeps `cat` live via `--yield-time-ms 0`; R8 writes a file one-shot before squash. |
| 6 M2–M5 contracts | §3 + ledger | Confirmed arg sets + readers; M5 follow-up inspect asserts `err_kind_at(_,"invalid_request",1)`. |
| 7 N1/N2 contracts | §3 + §4 | N1 `unknown_op`/exit1/stderr; N2 `invalid_request`/exit2/stderr, never reaches socket. |
| 8 staging mechanics | §1 + §7 | Stage 1 green = `cargo test --test manager` with zero runtime calls; R2–R8 not authored until Stage 2; bare `cargo test` skips clean both stages. |
| 9 `build.rs` unchanged | §2 + ledger | Live walker + slug derivation discovers all new nested families collision-free; slugs listed. |
| 10 workspace-root/slug at scale | §3 + ledger | `{run_root}/work/{run_id}-{slug}` per leaf stays unique (slug derived from the case path); absolute-path check (`mod.rs:68`) satisfied by canonicalize. |
| 11 parallel-safety | §5 | Each leaf owns one `Sandbox`, writes only under its own id; `RefCell` buffer is leaf-local; no shared mutable state added. |

> **Note for M5 (DQ 6) — confirmed, no re-verify.** The post-destroy
> `inspect_sandbox` kind is `invalid_request`: a removed id makes `store.inspect`
> return `ManagerError::MissingSandbox` (`store.rs:60-65`), which maps to
> `INVALID_REQUEST` (`error.rs:53,55`) via `into_response`→`Response::fault`
> (`:64-66`), rendered to stderr/exit 1 (`output.rs:266-272`). **Double-destroy is
> harmless:** M5 destroys explicitly, then the `Sandbox` RAII drop fires a second
> `destroy_sandbox` on the now-removed id (`fixtures.rs:99-107`); that call hits
> `MissingSandbox` and returns an error *response* (not a panic), swallowed by the
> drop's best-effort `let _ =` (`fixtures.rs:102-104`). M5 therefore needs no
> special teardown and leaks nothing.
