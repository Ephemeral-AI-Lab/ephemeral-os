# Spec: workspace & command operation naming refactor

Status: **approved** — ready to implement
Depends on: commit `10b70de64` (NetworkProfile / shared rename)

## Overview

Two CLI/wire naming questions plus the semantics they touch:

- **Part A** — rename the network selector arg `profile` → `network_profile` on
  `create_workspace_session`. *(recommended)*
- **Part B** — command-handle vocabulary: keep `command_session_id` vs rename to
  `namespace_execution_id`. *(recommended: keep)*
- **Semantics** — a precise, code-grounded definition of one-shot vs session for
  `exec_command`. *(no code change; glossary for the command-operations doc)*

---

## Semantics: one-shot vs session in `exec_command`

The mode is chosen by exactly one thing — whether `workspace_session_id` is
present (`exec_command.rs:110-119`). Not the network profile.

| | **session mode** (`workspace_session_id` provided) | **one-shot** (omitted) |
|---|---|---|
| Workspace | reuse existing (`resolve_workspace_session`) | create fresh (`create_one_shot_workspace_session`, `core.rs:126`) |
| Network | whatever the session was created with (`shared`/`isolated`) | hardcoded `NetworkProfile::Shared` (`core.rs:131`) |
| Creates | the caller, via `create_workspace_session` | `exec_command` itself |
| Destroys | the caller, via `destroy_workspace_session` | `exec_command`, on terminal state (`finalize_closure`, `exec_command.rs:181-191`) or start failure (`:146-149`) |
| `one_shot` flag | `false` | `true` = `workspace_session_id.is_none()` (`:119`) |
| Outlives the command? | yes (persistent) | no (ephemeral) |
| Returned handle | `command_session_id` only | `command_session_id` only (never a `workspace_session_id`) |

One-liner: **one-shot vs session = ephemeral/exec-owned vs persistent/caller-owned.**
Finalization touches only the workspace `exec_command` created itself.

A one-shot is **always terminable** via the returned `command_session_id`:
`write_command_stdin` treats `\u{3}` (Ctrl-C) or `\u{4}` (Ctrl-D) as kill input
(`write_command_stdin.rs:74-76`) → `exec.cancel()` → `killpg` SIGTERM/SIGKILL
(`pty.rs:184-186`) → terminal state → one-shot teardown. So terminal state is
reachable three ways: natural exit, `timeout_ms`, or explicit cancel. No forced
timeout or separate kill-op is required.

### Glossary — "session" means three different things

1. **workspace session** — `workspace_session_id` / `WorkspaceSessionId`. Every
   workspace is one of these; *both* modes produce a `WorkspaceSessionHandler`.
2. **command session** — wire `command_session_id`; internally
   `NamespaceExecutionId` (`namespace-execution/src/types.rs:8`). The running
   command/exec.
3. **lifecycle framing** — casual "session mode" = the persistent, caller-owned
   case.

Key correction for the doc: a one-shot workspace *is also* a `WorkspaceSession`.
So the lifecycle axis is **not** "session vs one-shot" — it is **caller-owned
(persistent) vs exec-owned (one-shot)** session. Frame it that way in docs and in
the `exec_command` description; don't imply a one-shot isn't a session.

---

## Part A — `profile` → `network_profile` on `create_workspace_session`

### Goal
After the `NetworkProfile` / `shared` rename, the *type* is clear but the
*argument* is still bare `profile` ("profile of what?"). Rename the user-facing
arg/key to `network_profile`.

### Scope decision (pick one) — recommended: **A**

| Option | What renames | Pros | Cons |
|---|---|---|---|
| **A — wire-only (recommended)** | CLI flag, input JSON key, response JSON key | Contained to one file + tests; Rust type already disambiguates internals | Wire key `network_profile` maps to Rust field `profile` (normal contract/field divergence) |
| B — wire + DTO field | A + `CreateWorkspaceRequest.profile` Rust field | DTO mirrors wire | Ripples to `core.rs`, model, and only *some* test `profile:` lines (DTO, not `WorkspaceHandle`) — easy to get wrong |
| C — everything | B + `WorkspaceHandle.profile` + observability snapshot/DB column | Fully uniform internal vocabulary | Reverses the earlier "handle field stays `profile`" decision; **needs a DB migration**; large churn |

The rest of Part A assumes **A**.

### CLI command args

`create_workspace_session` — only the network arg changes:

| | Before | After |
|---|---|---|
| Flag | `--profile PROFILE` | `--network-profile PROFILE` |
| JSON input key | `profile` | `network_profile` |
| Values | `shared` \| `isolated` (default `shared`) | *(unchanged)* |
| JSON output key | `profile` | `network_profile` |
| Usage | `create_workspace_session [--profile PROFILE]` | `create_workspace_session [--network-profile PROFILE]` |
| Examples | `--profile shared` / `--profile isolated` | `--network-profile shared` / `--network-profile isolated` |

`destroy_workspace_session` — unchanged (`--workspace-session-id ID`, `--grace-s SECONDS`).
`exec_command` — unchanged / out of scope (no profile arg; one-shot pins `shared`).

The framework already maps hyphenated flags to underscored keys (e.g.
`--workspace-session-id` → `workspace_session_id`), so `--network-profile` →
`network_profile` works the same way.

### Production changes (Option A)

| # | File:line | Item | Before | After |
|---|---|---|---|---|
| 1 | `…/cli_definition/workspace_session_operations.rs:30` | usage | `[--profile PROFILE]` | `[--network-profile PROFILE]` |
| 2 | `…/workspace_session_operations.rs:33-34` | examples | `--profile shared` / `--profile isolated` | `--network-profile …` |
| 3 | `…/workspace_session_operations.rs:41` | ArgSpec name (input key) | `"profile"` | `"network_profile"` |
| 4 | `…/workspace_session_operations.rs:46` | ArgCliSpec flag | `Some("--profile")` | `Some("--network-profile")` |
| 5 | `…/workspace_session_operations.rs:139` | parser key read | `optional_string("profile")` | `optional_string("network_profile")` |
| 6 | `…/workspace_session_operations.rs:199` | response output key | `"profile": …as_str()` | `"network_profile": …as_str()` |

Arg description (line 43) already reads "Network profile: …" — **unchanged**.
No changes to `model.rs`, `core.rs`, or any Rust field under Option A.

### Test changes (Option A)

| File:line | Change |
|---|---|
| `operation/tests/workspace_session.rs:252,285` | response assertion key `"profile"` → `"network_profile"` |
| `operation/tests/workspace_session.rs:277` | input `json!({ "profile": "isolated" })` → `"network_profile"` |
| `operation/tests/workspace_session.rs:301-303` | invalid-arg inputs `{"profile": …}` → `{"network_profile": …}` |
| `sandbox-gateway/tests/gateway_cli.rs:215-219` | input `["--profile","isolated"]` → `["--network-profile","isolated"]`; expect `json!({"network_profile":"isolated"})`; rename test fn `…maps_profile_flag` → `…maps_network_profile_flag` |

`CreateWorkspaceRequest { profile: NetworkProfile::… }` and `WorkspaceHandle` /
snapshot `profile:` fields in tests stay as-is under Option A (Rust fields, not
the wire key).

### Out of scope (separate follow-up)
- **Observability/status output + DB column** (`sandbox-daemon/src/observability/service.rs:248,478`,
  `sandbox-observability` `profile TEXT` column) — a *different* command and a
  persisted column; renaming needs a schema migration. **Consequence:** after
  Part A, `create_workspace_session` emits `network_profile` while the
  observability/status surface still emits `profile`. Reconcile as a follow-up.

---

## Part B — command-handle vocabulary (`command_session_id`)

The wire names the running-command handle `command_session_id`; internally it is
`NamespaceExecutionId` (renamed for SRP in `9bf29ec5c`). Question: should the
wire adopt the internal name too?

### Wire surface (where `command_session_id` appears)

| File:line | Use |
|---|---|
| `command_operations.rs:97,101` | `write_command_stdin` arg name + `--command-session-id` flag |
| `command_operations.rs:144,148` | `read_command_lines` arg name + flag |
| `command_operations.rs:227,246` | parse → `NamespaceExecutionId(...)` |
| `command_operations.rs:272` | error-detail key |
| `command_operations.rs:297` | `exec_command` response key |
| 6 test files | wire assertions (`operation/tests/*`, `gateway_cli.rs`, `observability/tests/schema.rs`) |

### Decision — recommended: **Keep**

| Option | Pros | Cons |
|---|---|---|
| **Keep `command_session_id` (recommended)** | User-facing clarity ("command session" reads naturally); parallel to `workspace_session_id`; always qualified on the wire, so no real ambiguity; internal `NamespaceExecutionId` stays an implementation detail | Wire word ≠ internal type name (already true and fine) |
| Rename → `namespace_execution_id` | End-to-end vocabulary match with the internal type | Leaks internal jargon to users; breaking wire change across 5 sites + 6 test files + flag `--namespace-execution-id`; "namespace execution" is worse for CLI/AI readers |

Rationale: the wire is **not** ambiguous — `workspace_session_id` and
`command_session_id` are always qualified and parallel. The internal SRP rename
to `NamespaceExecutionId` does not obligate the wire to adopt internal
vocabulary. The only genuine fix is the **lifecycle framing** (see Semantics),
not the handle name.

If Rename is chosen anyway: flip `command_session_id` → `namespace_execution_id`
across the 5 wire sites and 6 test files, flag → `--namespace-execution-id`.
Breaking, pre-release. Not recommended.

---

## Compatibility
Part A is a breaking wire change for `create_workspace_session`
(`profile` → `network_profile`); no alias, consistent with dropping
`host_compatible`. Part B (Keep) is non-breaking. Pre-release; acceptable.

## Verification (for whatever lands)
- `cargo fmt`
- `cargo build` (no warnings)
- `cargo clippy --all-targets` (changed crates)
- `cargo test -p sandbox-runtime -p sandbox-gateway -p sandbox-runtime-workspace`
- Manual: `sandbox-cli runtime create_workspace_session --network-profile isolated`
  → `{"workspace_session_id": …, "network_profile": "isolated"}`.

## Resolved decisions
1. **Part A scope** — **A (wire-only)**.
2. **Part B** — **keep `command_session_id`** (it fits the command-operation semantics; internal `NamespaceExecutionId` stays an implementation detail). No code change.
3. **Observability** — **excluded** (`profile` output/column left unchanged).
4. **`exec_command` description** — **yes**: add the caller-owned/exec-owned framing and the one-shot-terminable-via-`command_session_id` (Ctrl-C/Ctrl-D) note.
