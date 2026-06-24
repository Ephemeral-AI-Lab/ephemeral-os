# `sandbox-e2e-live-test` — Phase 2 Implementation Spec (Stage 1: manager surface; Stage 2 deferred)

Implementation-ready spec for **Phase 2, Stage 1 (manager surface)** of the parent
design (`docs/e2e/sandbox-e2e-live-test-spec.md`, `## Implementation Phases`).
Stage 1 adds the black-box **manager** matrix leaves (M2–M5, N1) as one leaf file
per case, the `err_kind_at` assertion helper, and the per-sandbox `exchange.jsonl`
artifact — and **nothing else**. **Stage 2 (the runtime surface — R1–R8, N2, the
`with_workspace_session` fixture, and the runtime assertion helpers) is deferred
to a separate Stage 2 spec** authored after the `sandbox-runtime` migration lands
and its CLI response shapes settle (§1 + the *Stage 2 (runtime surface) —
deferred* section). This document is **spec only**; it does not create or edit any
crate file.

The parent design is fixed; **live code wins on every conflict** and every
load-bearing fact is cited to a verified `file:line` (see the *Anchor Ledger*).
The known live-vs-parent deltas are honored here, not the parent prose: stream
routing (`error → stderr + exit 1`, `ok → stdout + exit 0`, `usage → stderr +
exit 2`); workspace root `{run_root}/work/{run_id}-{slug}` canonicalized;
`provision_sandbox` returns `(Sandbox, CallRecord)`; `request_json == None` on the
black-box path; `assertion.rs` has only `ok`/`field` today.

---

## 1. Phase boundary + two-stage statement

**This spec specifies Stage 1 only.** Phase 2 is delivered in **two stages** split
on the in-flight `sandbox-runtime` migration, and this document specifies the
Stage 1 (manager) half; the Stage 2 (runtime) half is **deferred** (see below).
Stage 1 extends the **live Phase 1 skeleton** (`src/{assertion,cli_client,config,
fixtures,gateway,lib}.rs`, `tests/support/mod.rs`, `build.rs`, `tests/{manager,
runtime}.rs`, the M1 leaf `manager/lifecycle/create_sandbox/returns_ready.rs`, and
the dormant R1 leaf `runtime/command/exec_command/one_shot.rs`) by adding: the
manager matrix leaves M2–M5 and the negative N1; the `err_kind_at` assertion
helper; and the per-sandbox `exchange.jsonl` artifact (with its `src/report.rs`
writer and the `Sandbox` exchange buffer).

The stage boundary is **binary-level**: the sole skip trigger is
`EOS_E2E_RUN_ROOT` unset, which makes `harness()` return `None`
(`support/mod.rs:7-9`) and each leaf early-return, writing nothing. A runtime leaf
run against a not-yet-migrated runtime would *fail* (`operation_failed`), never
skip — so Stage 1 keeps the runtime test binary out of its green target
(`cargo test --test manager`) rather than adding a per-leaf readiness guard.

| Stage | Delivers | Green criterion (provably) |
|-------|----------|-----------------------------|
| **Stage 1 — manager surface (this spec)** | Leaves M2–M5 (`lifecycle/{list,inspect,destroy}_sandbox`, `observability/get_observability_tree`), N1 (`routing/scope_and_dispatch/unknown_op`); the `err_kind_at` helper; `exchange.jsonl` capture wired through `Sandbox`. **Drives zero runtime ops.** | `cargo test --test manager` passes against a gateway that provisions/destroys sandboxes and serves `get_observability_tree`. |
| **Stage 2 — runtime surface (deferred)** | The runtime leaves and helpers — see *Stage 2 (runtime surface) — deferred* below. | full `cargo test` (manager **and** runtime) passes against the migrated real Docker runtime. |

### Stage 2 (runtime surface) — deferred

Stage 2 is **not specified in this document.** The `sandbox-runtime` CLI is
mid-migration, so speccing its response shapes now would build against a moving
target. Stage 2 will be authored in a separate spec **once the migration lands and
the runtime response shapes settle**, covering (by operation only):

- `exec_command` — un-dormant **R1** (one-shot); **R3** (in-session, observes a
  prior write); **R4** (long-running, captures a `command_session_id`).
- `write_command_stdin` (**R5**), `read_command_lines` (**R6**).
- `create_workspace_session` (**R2**), `destroy_workspace_session` (**R7a** clean /
  **R7b** busy).
- `squash` (**R8**); and **N2** (runtime op with no sandbox id — CLI-side).
- The `with_workspace_session` RAII fixture and a no-id `CliClient` verb for N2;
  the `err_detail`, `offsets_monotonic`, `non_decreasing` assertion helpers.

The pre-existing dormant R1 leaf (`tests/runtime/command/exec_command/one_shot.rs`,
Phase 1) stays on disk and dormant; Stage 2 un-dormants it. Every new runtime leaf
is an additive file the unchanged `build.rs` walker discovers, so Stage 2 needs no
structural change to the harness beyond authoring those files.

**Out of scope (Phases 3–4 — named, not designed):**

- Orchestrator bin internals: preflight, build phase, env export, the
  `--test {manager|runtime}` invocation policy, aggregation. `eos-e2e` stays the
  live Phase 0–1 print-and-exit stub; Phase 2 runs are by hand
  (`EOS_E2E_RUN_ROOT` + `cargo test`). **Phase 3.**
- `result.json`, `summary.json`, report-dir aggregation, timing/cleanup
  sub-objects. Stage 1 introduces **only** `exchange.jsonl`. The live skip path
  writes nothing and stays a bare `return`. **Phase 3.**
- Observability polling, `observability.json`, P1/P2. **Phase 4.**
- Spawn-mode gateway, label-based cleanup, `--rerun-failed-from`. **Phase 3.**
- `build.rs` changes — **none needed**; the live walker discovers each new leaf
  (Design Question 9). No new Cargo dependency — Stage 1 reuses
  `anyhow`/`serde`/`serde_json` already in `Cargo.toml`.

**Honest gates.** Stage 1 is green only against an externally started real-runtime
gateway attached via `--gateway-socket` (Open Items #1 — the shipped binary wires
`UnconfiguredRuntime`/`UnconfiguredDaemonInstaller`, `gateway/main.rs:94-100,
103-146`, which fail every `create_sandbox`). Stage 2 additionally requires the
landed `sandbox-runtime` migration. Both stages remain buildable and skip-clean
on any machine today.

---

## 2. Resulting file/folder structure

`←NEW (S1)` marks files Stage 1 creates; `△` marks Stage-1-edited harness files.
The `build.rs`-derived module slug (path components after `<scope>` joined by `_`,
minus `.rs` — `build.rs:48-55`) is shown beside each new leaf. The
`tests/runtime/**` Stage 2 leaves are **not** part of this spec (deferred, §1).

```text
crates/sandbox-e2e-live-test/
  build.rs                                  # UNCHANGED (DQ 9)
  src/
    assertion.rs                            △ +err_kind_at (S1)
    cli_client.rs                           △ +CallRecord::to_exchange_line() (S1; exchange.jsonl row)
    fixtures.rs                             △ +exchange sink on Sandbox + Harness::run_root() (S1)
    report.rs                               ←NEW (S1)   # exchange.jsonl writer ONLY (no result/summary — Phase 3)
    lib.rs                                  △ +pub mod report; (S1)
    config.rs, gateway.rs                   # UNCHANGED
    bin/eos-e2e.rs                          # UNCHANGED (Phase 0–1 stub)
  tests/
    support/mod.rs                          # UNCHANGED in Stage 1
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
        exec_command/one_shot.rs            # R1 (live Phase-1 leaf, dormant; Stage 2 un-dormants it — deferred)
      # all other tests/runtime/** leaves (R2–R8, N2) are Stage 2 — deferred (§1)
```

`report.rs` is the one new `src/` module: a single-job `exchange.jsonl` writer
(§5). `fixtures.rs`, `cli_client.rs`, `assertion.rs`, `lib.rs` are edited
additively for Stage 1. No file is moved or renamed.

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

The Stage 2 runtime leaves (R1–R8, N2) and their stateful/conditional pseudocode
are **deferred** to the Stage 2 spec (§1).

---

## 4. `assertion.rs` delta

Today `assertion.rs` ships **only** `ok` (`:4-9`) and `field` (`:12-16`). Stage 1
adds exactly **one** helper, `err_kind_at`, defined against **live routing** —
`cli_client.rs:72-77` (`carrier = exit==0 ? stdout : stderr`, `response_json`
parsed from the carrier) and `output.rs:266-272` (`error → stderr + exit 1`,
`ok → stdout + exit 0`). Every Stage 1 expected-failure (N1, M5's follow-up
inspect) is a manager semantic error on **exit 1 / stderr**. **The parent spec's
"error may arrive on stdout (exit 1)" / "stdout (exit 1)" note is STALE**: a
carried `error` is always written to stderr, and `CallRecord::response()` reads
whichever stream the exit code selects, so the error object is reachable through
`rec.response()` regardless of stream.

```rust
use crate::cli_client::CallRecord;

/// Assert the carried response is an error with `error.kind == kind` AND
/// `rec.exit_code == exit`. Reads `rec.response()` (parsed from the carrier
/// stream, cli_client.rs:72-77); the error object is `{kind,message,details}`
/// (response.rs:41-49). Stage 1 routing: manager semantic errors → exit 1/stderr
/// (output.rs:266-272). The helper takes `exit` as a parameter, so it also covers
/// the exit-2/usage routing exercised by the deferred Stage 2 N2 leaf.
/// Used by: N1 (unknown_op,1) and M5's follow-up inspect (invalid_request,1).
pub fn err_kind_at(rec: &CallRecord, kind: &str, exit: i32);
```

`err_kind_at` is the only helper Stage 1 needs (it has no runtime
`operation_failed` body and no offsets to check). The Stage 2 runtime helpers
(`err_detail`, `offsets_monotonic`, `non_decreasing`) are **deferred** to the
Stage 2 spec (§1). The helper introduces no inline comments in production code;
doc comments on public items are allowed (CLAUDE.md).

---

## 5. `exchange.jsonl` spec

Stage 1's only new artifact. **One job:** persist, per sandbox, the ordered
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
| `request` | `CallRecord::request_json` | **always `null`** on the black-box path — the CLI writes the wire request only to the socket, never to stdio (`cli_client.rs:13` field, `:81` assignment; rationale `:7-10`); the field exists for parity |
| `response` | `CallRecord::response_json` | parsed from the carrier stream (`cli_client.rs:72-77`) |
| `exit_code` | `CallRecord::exit_code` | 0/1/2 |
| `stdout`, `stderr` | `CallRecord::{stdout,stderr}` | both streams retained |
| `latency_ms` | `CallRecord::latency_ms` | wall-clock around the spawn (`cli_client.rs:62-67`) |

**Writer ownership / keying / flush (resolved — DQ 2).** The sink is **owned by
`Sandbox`**, keyed by the runtime-assigned id, written to
`{run_root}/reports/{sb.id}/exchange.jsonl`:

- `Sandbox` gains **only** a private record buffer `RefCell<Vec<CallRecord>>` — it
  does **not** carry a `run_root`. Drop resolves the report dir via
  `Harness::get()` (already called in drop, `fixtures.rs:101`) and a tiny new
  `Harness::run_root(&self) -> &Path` accessor over the field it already holds
  (`fixtures.rs:15`), so no `PathBuf` is copied onto every `Sandbox` (prefer-less).
  `provision_sandbox` seeds the buffer with the **create `CallRecord`** it already
  holds (`fixtures.rs:76-87`) — the create record lands in *that id's* file, which
  is only possible after `/id` is read, so the buffer is seeded at construction.
- Every leaf-issued call is appended via a new `Sandbox::record(&self, rec) ->
  &Value` shim that pushes into that `RefCell` buffer (`&self`, not `&mut self`;
  the `RefCell` is sound because tests are single-threaded per `#[test]` and each
  `Sandbox` is leaf-local, so no cross-thread sharing — DQ 11). Leaves that want
  the parsed body call `sb.record(rec)`. Helper-only leaves may also keep calling
  `rec.response()` directly; recording is additive.
- **Flush on `Drop`**, before `destroy_sandbox`: drop takes the `Harness` handle
  it already obtains (`fixtures.rs:101`), reads `harness.run_root()`, then calls
  `report::write_exchange(harness.run_root(), &self.id, &records)` which creates
  `reports/{id}/` and writes the header line then one line per record. Drop already
  fires `destroy_sandbox` (`fixtures.rs:99-107`); the flush is prepended. A write
  error in drop is swallowed (best-effort artifact, like the existing `let _ =`
  destroy).

This keeps the **one shared `CliClient`** sandbox-agnostic (no per-id state on the
client) and puts the id↔file binding on the object that owns the id.

**Sandbox-less calls (N1).** N1 owns **no** `Sandbox`, so it writes **no**
`exchange.jsonl` — there is no id to key on, and Stage 1 deliberately adds no
run-level sink (that is the Phase 3 `RunReport`). N1 asserts purely on the returned
`CallRecord` in-process. This is consistent with the live skip path writing
nothing; no artifact is lost that Stage 1 owns. (The Stage 2 N2 leaf is likewise
sandbox-less and writes nothing — deferred, §1.)

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
only additive change is the `exchange.jsonl` sink described in §5 (the
`RefCell<Vec<CallRecord>>` buffer on `Sandbox`, the `Harness::run_root()` accessor,
and the drop-time flush). M2–M5 and N1 need no new provisioning capability, and
**Stage 1 needs no new `CliClient` verb** — M2–M5/N1 use the existing
`manager(op,args)` (`cli_client.rs:37-41`).

**Stage 2 fixtures are deferred.** The `with_workspace_session` RAII fixture and a
no-id `CliClient` verb for N2 belong to the runtime surface and are specified in
the Stage 2 spec (§1), not here.

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
`fixtures.rs:25-28` cache `None`). The dormant R1 leaf and the live M1 leaf skip
exactly like the new Stage 1 leaves.

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

### Stage 2 verification — deferred

Deferred to the Stage 2 spec (§1), gated on the landed `sandbox-runtime` migration
plus the real-runtime gateway.

The `run-manifest.json` schema is unchanged from Phase 1 (`config.rs:21-27`);
Stage 1 adds no manifest field.

---

## 8. Anchor ledger

Every `file:line` this **Stage 1** spec relies on, re-opened in this run. The
runtime (R-series) and N2 anchors move to the Stage 2 spec (§1) and are not listed
here.

| Anchor | Verdict | Confirmed fact |
|--------|---------|----------------|
| `Cargo.toml:18` | confirmed | `"crates/sandbox-e2e-live-test"` is a workspace member (after `"xtask"` :17). |
| `Cargo.toml:73` | confirmed | `[workspace.lints.clippy]`. |
| `Cargo.toml:81-83` | confirmed | `unwrap_used="warn"` (:81), `dbg_macro="warn"` (:82), `undocumented_unsafe_blocks="deny"` (:83). |
| `crates/sandbox-e2e-live-test/Cargo.toml` | confirmed | deps = `anyhow`,`serde`,`serde_json` only; `[lints] workspace=true`. Stage 1 adds none. |
| `…/src/cli_client.rs:11-19` | confirmed | `CallRecord{argv,request_json:Option,response_json,exit_code,stdout,stderr,latency_ms}`. |
| `…/src/cli_client.rs:13,81` | confirmed | `request_json: Option<Value>` field (:13); assigned `None` (:81) — `request` is `null` on the black-box path. |
| `…/src/cli_client.rs:37-41` | confirmed | `manager(op,args)` verb — the only verb Stage 1 uses (M2–M5, N1); reused, no new verb added. |
| `…/src/cli_client.rs:62-67` | confirmed | wall-clock `latency_ms` around `Command::output()`. |
| `…/src/cli_client.rs:72-77` | confirmed | `carrier = exit==0 ? stdout : stderr`; `response_json` parsed from carrier. |
| `…/src/cli_client.rs:96-99` | confirmed | `CallRecord::response()` returns `&response_json`. |
| `…/src/fixtures.rs:15` | confirmed | `Harness` already holds `run_root: PathBuf` — the new `Harness::run_root()` accessor exposes it so `Sandbox::drop` resolves the report dir without a per-`Sandbox` field (D4). |
| `…/src/fixtures.rs:25-28` | confirmed | `Harness::get` is `OnceLock<Option<Harness>>`; unset env caches `None`. |
| `…/src/fixtures.rs:101` | confirmed | `Sandbox::drop` calls `Harness::get()` — drop already has a `Harness` handle to read `run_root` from (D4). |
| `…/src/fixtures.rs:59-88` | confirmed | `provision_sandbox(slug,image)->(Sandbox,CallRecord)`; one `create_sandbox`; id from `/id`. |
| `…/src/fixtures.rs:61-74` | confirmed | workspace root `{run_root}/work/{run_id}-{slug}` created + canonicalized (absolute). |
| `…/src/fixtures.rs:94-107` | confirmed | `Sandbox{id,workspace_root}`; `Drop` → `destroy_sandbox --sandbox-id id` (best-effort). |
| `…/src/assertion.rs:4-16` | confirmed | only `ok` (:4-9) and `field` (:12-16) exist today; Stage 1 adds `err_kind_at`. |
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
| `sandbox-gateway/src/cli/output.rs:266-272` | confirmed | `render_response`: error→stderr+EXIT_FAILURE(1); ok→stdout+EXIT_SUCCESS(0). |
| `sandbox-gateway/src/gateway/main.rs:94-100,103-146` | confirmed | `default_manager_services` wires `UnconfiguredRuntime`(:97,103)/`UnconfiguredDaemonInstaller`(:98,122); create_sandbox errors `"sandbox runtime is not configured"` (:110-112). |

---

## 9. Conventions checklist

- **SRP / one job per unit.** New `report.rs` = `exchange.jsonl` writing only (no
  result/summary/observability). The new `err_kind_at` helper asserts one
  invariant (kind + exit). The `Sandbox` exchange sink is the id↔file binding on
  the object that owns the id — not on the shared `CliClient`. Each leaf file owns
  exactly one matrix case.
- **No inline comments in production code.** `src/` additions (`report.rs`,
  fixture/assertion/cli_client deltas) carry only `///`/`//!` doc comments on
  public items. Leaf tests may carry intent comments (tests only, per CLAUDE.md),
  as the live M1/R1 leaves already do.
- **Workspace deps via `dep.workspace = true`.** Stage 1 introduces **no new
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
| 2 `exchange.jsonl` owner/key/flush | §5 | Owned by `Sandbox`, keyed by runtime id, seeded with the create record, flushed on drop to `reports/{id}/exchange.jsonl`; N1 (sandbox-less) writes nothing. |
| 3 stateful id round-trip | — | **Deferred to Stage 2** (runtime `command_session_id`/`workspace_session_id` round-trips). |
| 4 `with_workspace_session` | — | **Deferred to Stage 2** (runtime workspace-session RAII fixture). |
| 5 conditional fixtures | — | **Deferred to Stage 2** (R7b live-command / R8 squash-after-mutation fixtures). |
| 6 M2–M5 contracts | §3 + ledger | Confirmed arg sets + readers; M5 follow-up inspect asserts `err_kind_at(_,"invalid_request",1)`. |
| 7 N1 contract (N2 deferred) | §3 + §4 | N1 `unknown_op`/exit1/stderr. N2 (`invalid_request`/exit2/stderr) is **deferred to Stage 2**. |
| 8 staging mechanics | §1 + §7 | Stage 1 green = `cargo test --test manager` with zero runtime calls; the runtime leaves are deferred to Stage 2; bare `cargo test` skips clean. |
| 9 `build.rs` unchanged | §2 + ledger | Live walker + slug derivation discovers all new manager families collision-free; slugs listed. |
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
