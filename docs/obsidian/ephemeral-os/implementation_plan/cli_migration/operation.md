---
title: sandbox-manager-cli / sandbox-runtime-cli Operation Reference — Variants & Expected Output
tags:
  - ephemeral-os
  - cli
  - reference
status: reference
updated: 2026-07-03
---

# CLI Operation Reference — `sandbox-manager-cli` / `sandbox-runtime-cli`

Every CLI operation, its invocation variants, and the expected output.
This is the behavioral contract for the [[spec|CLI split migration]]:
invocations are written in the **target (post-split) form**. The same
variants exist today under the legacy single binary — outputs, error
kinds, and exit codes are identical; only the program name and space
prefix change:

| Legacy (`sandbox-cli`, today) | Target (this document) |
|---|---|
| `sandbox-manager-cli <op> [args…]` | `sandbox-manager-cli <op> [args…]` |
| `sandbox-manager-cli observability <op> [args…]` | `sandbox-manager-cli observability <op> [args…]` |
| `sandbox-runtime-cli --sandbox-id ID <op> [args…]` | `sandbox-runtime-cli --sandbox-id ID <op> [args…]` |

> [!info] Fidelity
> Field names, error `kind`s, exit codes, and messages shown in
> `verbatim quotes` are exact, taken from the source (spec files under
> `cli_definition/`, dispatch impls, `sandbox-protocol`). Concrete
> *values* — ids, ports, byte counts, timestamps, durations — are
> illustrative. JSON is pretty-printed here for readability; the CLI
> always emits **one compact JSON line**.

## Conventions

**Invocation grammar**

```sh
# operator surface: fleet lifecycle + observability
sandbox-manager-cli [GLOBAL FLAGS] OPERATION [ARGS…]
sandbox-manager-cli [GLOBAL FLAGS] observability OPERATION [ARGS…]

# agent surface: drive exactly one sandbox
sandbox-runtime-cli [GLOBAL FLAGS] [--sandbox-id ID] OPERATION [ARGS…]
```

**Global flags** — both binaries: `--gateway-socket HOST:PORT` (default
`127.0.0.1:7878`), `--gateway-auth-token TOKEN` (or
`SANDBOX_GATEWAY_AUTH_TOKEN` via the `bin/sandbox-manager-cli` /
`bin/sandbox-runtime-cli` wrappers reading `/tmp/eos-gateway.token`).
Manager-only: `--progress`. Runtime-only: `--sandbox-id ID` (fallback:
`SANDBOX_DEFAULT_ID` env / config default).

**Output contract**

| Outcome | Stream | Exit | Shape |
|---|---|---|---|
| success | stdout | `0` | raw result object (no envelope) |
| remote error | stderr | `1` | `{"error":{"kind":…,"message":…,"details":…}}` |
| transport failure | stderr | `1` | error envelope, kind `connection_error` / `protocol_error` |
| local usage error (bad op/flag/arg) | stderr | `2` | error envelope, kind `invalid_request` |
| config discovery failure | stderr | non-zero | error envelope, kind `config_error` |

> [!warning] A failed *command* is not a failed *operation*
> `exec_command` for a program that exits non-zero is still an operation
> **success**: stdout, exit `0`, with `"status": "error"` *inside* the
> result. Only protocol-level faults use the `{"error":…}` envelope.

**Enumerations**

- Sandbox `state`: `creating` `ready` `stopping` `stopped` `failed`
- Command `status`: `running` `ok` `error` `timed_out` `cancelled`
- Error `kind`s: `bad_json` `internal_error` `invalid_request`
  `operation_failed` `request_too_large` `unauthorized` `unknown_op`
  `not_found` (file ops) + CLI-local `config_error` `connection_error`
  `protocol_error`
- Id shapes: sandbox `eos-<uuid4>`, command session
  `namespace_execution_<n>`, published layer `L000001-0f1e2d3c`,
  squashed layer `S000004-1a2b3c4d`, span `d-<n>` / `np-<n>`

**Wire request** (what the CLI actually sends, for reference):

```json
{"op":"exec_command","request_id":"6f9c…","scope":{"sandbox":{"sandbox_id":"eos-7c9e…"}},"args":{"cmd":"pwd"},"_gateway_auth":"…","_stream_logs":false}
```

---

# `sandbox-manager-cli` — manager operations

## `create_sandbox`

Create the host-side record and runtime sandbox, start its daemon.

**V1 — minimal (single sandbox)** → stdout, exit 0

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed
```

```json
{
  "id": "eos-7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "workspace_root": "/testbed",
  "state": "ready",
  "daemon": { "host": "127.0.0.1", "port": 40001 },
  "daemon_http": { "host": "127.0.0.1", "port": 40101 },
  "shared_base": null
}
```

**V2 — legacy flag alias** — `--workspace-root` is accepted for
`workspace_root` (create_sandbox only). Same output as V1.

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-root /testbed
```

**V3 — explicit `--count 1`** — same single-record output as V1 (a
one-element batch collapses to one record).

**V4 — batch `--count 3`** → stdout, exit 0; records share a read-only
workspace base:

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed --count 3
```

```json
{
  "sandboxes": [
    {
      "id": "eos-1af0…",
      "workspace_root": "/testbed",
      "state": "ready",
      "daemon": { "host": "127.0.0.1", "port": 40001 },
      "daemon_http": { "host": "127.0.0.1", "port": 40101 },
      "shared_base": {
        "source": "/testbed",
        "target": "/eos/shared-base/3f9d2c…",
        "root_hash": "3f9d2c81…",
        "readonly": true
      }
    },
    { "id": "eos-2b71…", "…": "…" },
    { "id": "eos-9c04…", "…": "…" }
  ]
}
```

**V5 — with `--progress`** (global flag, also accepted inside the op argv)
→ progress lines on **stderr**, final JSON on **stdout**, exit 0:

```sh
sandbox-manager-cli --progress create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed
```

```text
[progress 0.412s] pulling image ubuntu:24.04          (stderr)
[progress 3.108s] starting container eos-7c9e…        (stderr)
[progress 4.972s] daemon ready on 127.0.0.1:40001     (stderr)
[Output]                                              (stderr)
{"id":"eos-7c9e…","workspace_root":"/testbed","state":"ready",…}   (stdout)
```

**V6 — missing required arg** → stderr, exit 2 (CLI-local, nothing sent):

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04
```

```json
{"error":{"kind":"invalid_request","message":"--workspace-bind-root is required for create_sandbox","details":{}}}
```

**V7 — relative workspace root** → stderr, exit 1 (manager rejects):

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root testbed
```

```json
{"error":{"kind":"invalid_request","message":"invalid workspace root: testbed","details":{}}}
```

**V8 — empty image / zero count** → stderr, exit 1, kind
`invalid_request` (manager-side `InvalidImage` / `InvalidSandboxCount`).
`--count abc` fails earlier, CLI-local exit 2:
`"--count must be an unsigned integer"`.

**V9 — runtime/provider failure** (Docker down, image pull failure) →
stderr, exit 1, kind `internal_error`.

## `destroy_sandbox`

**V1 — success** → stdout, exit 0 (record now `stopped`, endpoints cleared):

```sh
sandbox-manager-cli destroy_sandbox --sandbox-id eos-7c9e6679-7425-40de-944b-e07fc1f90ae7
```

```json
{
  "id": "eos-7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "workspace_root": "/testbed",
  "state": "stopped",
  "daemon": null,
  "daemon_http": null,
  "shared_base": null
}
```

**V2 — unknown sandbox** → stderr, exit 1:

```json
{"error":{"kind":"invalid_request","message":"sandbox not found: eos-nonexistent","details":{}}}
```

**V3 — bare invocation** → the CLI prints the operation help instead of
dispatching (required arg missing, no argv at all):

```sh
sandbox-manager-cli destroy_sandbox
```

## `list_sandboxes`

**V1 — empty registry** → stdout, exit 0: `{"sandboxes":[]}`

**V2 — populated (mixed states)** → stdout, exit 0:

```sh
sandbox-manager-cli list_sandboxes
```

```json
{
  "sandboxes": [
    {
      "id": "eos-7c9e…", "workspace_root": "/testbed", "state": "ready",
      "daemon": { "host": "127.0.0.1", "port": 40001 },
      "daemon_http": { "host": "127.0.0.1", "port": 40101 },
      "shared_base": null
    },
    {
      "id": "eos-2b71…", "workspace_root": "/testbed", "state": "stopped",
      "daemon": null, "daemon_http": null, "shared_base": null
    }
  ]
}
```

**V3 — trailing junk** → stderr, exit 2:
`"unexpected positional argument for list_sandboxes: foo"`.

## `inspect_sandbox`

**V1 — ready sandbox** → stdout, exit 0 (single record, same shape as
`list_sandboxes` entries):

```sh
sandbox-manager-cli inspect_sandbox --sandbox-id eos-7c9e…
```

**V2 — batch-created sandbox** → record includes the populated
`shared_base` object.

**V3 — unknown id** → stderr, exit 1:
`{"error":{"kind":"invalid_request","message":"sandbox not found: …"}}`

## `checkpoint_squash`

Squash the sandbox's published layers and live-remount sessions. The
manager forwards one `squash_layerstack` to the daemon and returns the
daemon response **verbatim**.

**V1 — blocks squashed, old layers reclaimed** → stdout, exit 0:

```sh
sandbox-manager-cli checkpoint_squash --sandbox-id eos-7c9e…
```

```json
{
  "manifest_version": 4,
  "squashed_blocks": [
    {
      "squashed_layer_id": "S000004-1a2b3c4d",
      "replaced_layer_ids": ["L000001-0f1e2d3c", "L000002-9a8b7c6d", "L000003-5e4f3a2b"],
      "replaced_layers": "reclaimed"
    }
  ]
}
```

**V2 — squashed but old layers still leased** → stdout, exit 0;
`replaced_layers: "leased"` and `blocked_reasons` present:

```json
{
  "manifest_version": 5,
  "squashed_blocks": [
    {
      "squashed_layer_id": "S000005-77aa02e1",
      "replaced_layer_ids": ["L000004-4cc10b9f"],
      "replaced_layers": "leased",
      "blocked_reasons": ["pinned:lease_holder_not_swept"]
    }
  ]
}
```

**V3 — no-op (nothing squashable)** → stdout, exit 0:

```json
{ "manifest_version": 2, "squashed_blocks": [] }
```

**V4 — sessions failed live remount** → stdout, exit 0;
`faulty_sessions` key present only when non-empty:

```json
{
  "manifest_version": 6,
  "squashed_blocks": [ { "squashed_layer_id": "S000006-…", "replaced_layer_ids": ["…"], "replaced_layers": "reclaimed" } ],
  "faulty_sessions": [
    { "session_id": "ws-3", "class_detail": "remount_failed", "lease_errors": ["…"] }
  ]
}
```

**V5 — sandbox stopped** → stderr, exit 1:

```json
{"error":{"kind":"invalid_request","message":"invalid state transition for eos-2b71…: stopped -> ready","details":{}}}
```

**V6 — unknown sandbox** → stderr, exit 1: `"sandbox not found: eos-nonexistent"` (the e2e fault probe).

**V7 — daemon-side squash failure** → stderr, exit 1, kind
`operation_failed` (daemon message forwarded verbatim).

## `snapshot` — hidden manager op

`cli: None`: in the dispatch table but not the help catalog.

**V1 — typed directly** → stderr, exit 2 (not in the CLI catalog):

```sh
sandbox-manager-cli snapshot
```

```json
{"error":{"kind":"invalid_request","message":"unknown operation: snapshot","details":{}}}
```

**V2 — reached properly** → via `sandbox-manager-cli observability snapshot`
*without* `--sandbox-id` (see observability section).

---

# `sandbox-runtime-cli` — runtime operations

All runtime ops require a sandbox id and are forwarded to that sandbox's
daemon.

**Sandbox-id resolution variants** (apply to every runtime op):

| Variant | Command shape | Result |
|---|---|---|
| explicit | `sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command pwd` | normal dispatch |
| env/config default | `SANDBOX_DEFAULT_ID=eos-7c9e…` + `sandbox-runtime-cli exec_command pwd` | normal dispatch |
| none | `sandbox-runtime-cli exec_command pwd` | stderr exit 2: `"runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID"` |
| empty | `--sandbox-id ""` | stderr exit 2: `"runtime sandbox id must be non-empty"` |
| unknown sandbox | any op | stderr exit 1: `"sandbox not found: <id>"` (`invalid_request`) |
| sandbox stopped | any op | stderr exit 1: `"invalid state transition for <id>: stopped -> ready"` |
| no daemon endpoint | any op | stderr exit 1: `"sandbox daemon unavailable for <id>"` |
| manager op typed here | `sandbox-runtime-cli --sandbox-id X list_sandboxes` | stderr exit 2: `"unknown operation: list_sandboxes"` (per-binary catalogs) |

## `exec_command`

Start a shell command. Without `--workspace-session-id` it runs in a
one-shot ephemeral shared-network workspace, destroyed at terminal state.

**V1 — quick command, one-shot workspace** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command pwd
```

```json
{
  "status": "ok",
  "exit_code": 0,
  "wall_time_seconds": 0.041,
  "command_total_time_seconds": 0.041,
  "start_offset": 0,
  "end_offset": 1,
  "total_lines": 1,
  "original_token_count": 3,
  "output": "/workspace\n"
}
```

No `command_session_id`: the command reached terminal state within the
initial yield, so the ephemeral workspace is already gone.

**V2 — command fails (non-zero exit)** → **stdout, exit 0** — an
operation success carrying `status:"error"`:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command "ls /does-not-exist"
```

```json
{
  "status": "error",
  "exit_code": 2,
  "wall_time_seconds": 0.038,
  "command_total_time_seconds": 0.038,
  "start_offset": 0,
  "end_offset": 1,
  "total_lines": 1,
  "original_token_count": 12,
  "output": "ls: cannot access '/does-not-exist': No such file or directory\n"
}
```

**V3 — still running after the yield** → stdout, exit 0;
`status:"running"` and a `command_session_id` for follow-up:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command --yield-time-ms 0 "sleep 30"
```

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 0.002,
  "command_total_time_seconds": 0.002,
  "start_offset": 0,
  "end_offset": 0,
  "total_lines": 0,
  "original_token_count": 0,
  "output": "",
  "command_session_id": "namespace_execution_7"
}
```

**V4 — inside a persistent session** (state persists across commands in
the same session's mounted workspace):

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command --workspace-session-id ws-1 "echo hi > /workspace/x.txt"
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command --workspace-session-id ws-1 "cat /workspace/x.txt"
```

Second call → `"status":"ok"`, `"output":"hi\n"`.

**V5 — timeout** → stdout, exit 0, `status:"timed_out"`:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command --timeout-ms 100 "sleep 5"
```

```json
{ "status": "timed_out", "exit_code": null, "…": "…", "output": "" }
```

**V6 — unknown workspace session** → stderr, exit 1, kind
`operation_failed`.

**V7 — parse errors** → stderr, exit 2 (CLI-local):
missing positional → `"COMMAND is required for exec_command"`;
`--timeout-ms fast` → `"--timeout-ms must be an unsigned integer"`;
`--shell bash` → `"unknown flag for exec_command: --shell"`.

## `write_command_stdin`

**V1 — feed a line to an interactive command** → stdout, exit 0; returns
the bounded output yield after the write:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… exec_command --yield-time-ms 0 "python3 -i"
sandbox-runtime-cli --sandbox-id eos-7c9e… write_command_stdin --command-session-id namespace_execution_7 "print(6*7)
"
```

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 0.251,
  "command_total_time_seconds": 3.417,
  "start_offset": 2,
  "end_offset": 4,
  "total_lines": 4,
  "original_token_count": 9,
  "output": ">>> print(6*7)\n42\n",
  "command_session_id": "namespace_execution_7"
}
```

**V2 — with `--yield-time-ms 2000`** — waits up to 2 s for output after
the write; same shape.

**V3 — Ctrl-C (`\x03`) terminates** → stdout, exit 0; command reaches
`cancelled` with exit code 130:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… write_command_stdin --command-session-id namespace_execution_7 $'\x03'
```

```json
{ "status": "cancelled", "exit_code": 130, "…": "…" }
```

**V4 — Ctrl-D (`\x04`) sends EOF** — an interactive shell/REPL exits
cleanly: `status:"ok"`, `exit_code:0`.

**V5 — unknown/finished session** → stderr, exit 1:

```json
{"error":{"kind":"operation_failed","message":"…","details":{"command_session_id":"namespace_execution_99"}}}
```

## `read_command_lines`

Stable line-offset paging over a command session's rendered transcript.

**V1 — first page** → stdout, exit 0 (window fields describe the slice;
`status`/`exit_code` reflect the session *now*):

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… read_command_lines --command-session-id namespace_execution_7 --start-offset 0 --limit 100
```

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 0.0,
  "command_total_time_seconds": 12.03,
  "start_offset": 0,
  "end_offset": 100,
  "total_lines": 342,
  "original_token_count": 780,
  "output": "…first 100 transcript lines…",
  "command_session_id": "namespace_execution_7"
}
```

**V2 — tail from the previous window** — pass the last response's
`end_offset` as `--start-offset`; defaults: offset 0, limit 200 (max 1000).

**V3 — finished command** — same shape with `status:"ok"` (or
`error`/`timed_out`/`cancelled`) and the final `exit_code`.

**V4 — offset past the end** → stdout, exit 0; empty `output`,
`start_offset == end_offset`.

**V5 — unknown session** → stdout, exit 0 with `status:"error"` (reads
never fault the protocol; the error is in-band).

## `create_workspace_session`

**V1 — default (shared network)** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… create_workspace_session
```

```json
{ "workspace_session_id": "ws-1", "network_profile": "shared" }
```

**V2 — isolated network namespace**:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… create_workspace_session --network-profile isolated
```

```json
{ "workspace_session_id": "ws-2", "network_profile": "isolated" }
```

**V3 — invalid profile** → stderr, exit 1:

```json
{"error":{"kind":"invalid_request","message":"network_profile must be one of shared or isolated","details":{}}}
```

## `destroy_workspace_session`

**V1 — success** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… destroy_workspace_session --workspace-session-id ws-1
```

```json
{ "workspace_session_id": "ws-1", "destroyed": true, "evicted_upperdir_bytes": 8192 }
```

**V2 — with teardown grace** — `--grace-s 2.5` (float); same output.

**V3 — active commands still running** → stderr, exit 1:

```json
{
  "error": {
    "kind": "operation_failed",
    "message": "workspace session has active command sessions",
    "details": { "active_command_session_ids": ["namespace_execution_3", "namespace_execution_5"] }
  }
}
```

**V4 — negative grace** → stderr, exit 1:
`"grace_s must be non-negative"` (`invalid_request`).
`--grace-s abc` fails CLI-local, exit 2: `"--grace-s must be a finite number"`.

**V5 — unknown session** → stderr, exit 1, kind `operation_failed`.

## `file_read`

**V1 — whole file from the published snapshot** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… file_read --path README.md
```

```json
{
  "path": "README.md",
  "content": "# EphemeralOS Sandbox\n…",
  "start_line": 1,
  "num_lines": 91,
  "total_lines": 91,
  "bytes_read": 3187,
  "total_bytes": 3187,
  "next_offset": null,
  "truncated": false
}
```

**V2 — window** (`--offset` is 1-indexed; `--limit` default 2000, max 2000):

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… file_read --path src/main.rs --offset 20 --limit 40
```

```json
{
  "path": "src/main.rs",
  "content": "…lines 20-59…",
  "start_line": 20,
  "num_lines": 40,
  "total_lines": 210,
  "bytes_read": 1490,
  "total_bytes": 7803,
  "next_offset": 60,
  "truncated": true
}
```

**V3 — inside a live session** — `--workspace-session-id ws-1` reads the
session's mounted workspace (sees uncaptured writes) instead of the
snapshot. Same shape.

**V4 — not found** → stderr, exit 1:

```json
{"error":{"kind":"not_found","message":"…","details":{"path":"missing.txt"}}}
```

**V5 — bad limit** → stderr, exit 1:
`"limit must be between 1 and 2000"` (`invalid_request`); non-UTF-8 file →
`invalid_request`; unknown session → `not_found` with
`details.workspace_session_id`.

## `file_write`

**V1 — create (publishes one layer, attributed `operation:<request_id>`)**
→ stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… file_write --path notes.txt --content 'hello'
```

```json
{ "type": "create", "path": "notes.txt", "bytes_written": 5 }
```

**V2 — overwrite existing** → same shape, `"type": "update"`.

**V3 — into a live session** — `--workspace-session-id ws-1`: the write
lands in the session workspace (attributed on capture, no immediate
layer). Same output shape.

**V4 — empty content** — `--content ''` writes a zero-byte file:
`"bytes_written": 0`.

**V5 — errors** — missing `--content` → CLI-local exit 2
(`"--content is required for file_write"`); invalid path →
`invalid_request`, exit 1; unknown session → `not_found`, exit 1; storage
failure → `operation_failed`, exit 1.

## `file_edit`

Ordered exact-string replacements; `--edits` is a JSON array of
`{old_string, new_string, replace_all?}`.

**V1 — single edit** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… file_edit --path notes.txt \
  --edits '[{"old_string":"hello","new_string":"goodbye"}]'
```

```json
{ "type": "edit", "path": "notes.txt", "edits_applied": 1, "replacements": 1, "bytes_written": 7 }
```

**V2 — ordered multi-edit with `replace_all`** — replacements can exceed
edits:

```sh
… --edits '[{"old_string":"foo","new_string":"bar","replace_all":true},{"old_string":"baz","new_string":"qux"}]'
```

```json
{ "type": "edit", "path": "src/lib.rs", "edits_applied": 2, "replacements": 5, "bytes_written": 8123 }
```

**V3 — in a live session** — add `--workspace-session-id ws-1`; same shape.

**V4 — validation errors** → stderr, exit 1, kind `invalid_request`:

| Trigger | Message |
|---|---|
| `old_string` absent from file | edit-not-found (`EditNotFound`) |
| `old_string` ambiguous, no `replace_all` | edit-not-unique (`EditNotUnique`) |
| `--edits '[]'` | no edits (`NoEdits`) |
| edit produces identical content | no changes (`NoChanges`) |
| `--edits '{"not":"array"}'` | `"edits must be a JSON array"` |
| `--edits '[{"new_string":"x"}]'` | `"edits[0].old_string must be a string"` |
| `--edits '[{"old_string":"a","new_string":"b","replace_all":"yes"}]'` | `"edits[0].replace_all must be a boolean"` |

**V5 — file not found** → stderr, exit 1, kind `not_found`,
`details:{path}`.

## `file_blame`

Per-line ownership tiled from the latest publish-auditability event.

**V1 — mixed-owner file** → stdout, exit 0:

```sh
sandbox-runtime-cli --sandbox-id eos-7c9e… file_blame --path notes.txt
```

```json
{
  "path": "notes.txt",
  "ranges": [
    { "start_line": 1, "line_count": 2, "owner": "original" },
    { "start_line": 3, "line_count": 1, "owner": "operation:6f9c2e10-…" },
    { "start_line": 4, "line_count": 5, "owner": "workspace_session:ws-1" }
  ]
}
```

Owner vocabulary: `workspace_session:<id>` | `operation:<request_id>` |
`original` | `unknown`.

**V2 — no record for path** (never published, or unparsable path) →
stderr, exit 1:

```json
{"error":{"kind":"not_found","message":"no auditability record for path: ghost.txt","details":{"path":"ghost.txt"}}}
```

---

# `sandbox-manager-cli observability` — observability views

Sandbox-scoped views are rewritten to the daemon-private op
`get_observability` with the operation name as the `view` arg. `snapshot`
without `--sandbox-id` becomes the manager's hidden aggregate `snapshot`.

**Space-level error variants**

| Trigger | Result |
|---|---|
| non-`snapshot` op without `--sandbox-id` | stderr exit 2: `"observability operations require --sandbox-id"` |
| `--sandbox-id ""` | stderr exit 2: `"--sandbox-id must be non-empty"` |
| daemon observability unconfigured | stderr exit 1: `internal_error` `"daemon observability is not configured"` |
| `--window-ms 700000` (cap 600000) | stderr exit 1: `invalid_request` `"window_ms exceeds max (600000)"` |

## `snapshot`

**V1 — one sandbox (daemon view)** → stdout, exit 0:

```sh
sandbox-manager-cli observability snapshot --sandbox-id eos-7c9e…
```

```json
{
  "sandbox_id": "eos-7c9e…",
  "lifecycle_state": "ready",
  "availability": "available",
  "sampled_at_unix_ms": 1751500000000,
  "errors": [],
  "daemon": { "daemon_pid": 1234, "runtime_dir": "/eos/runtime/daemon" },
  "resources": {
    "latest": {
      "ts": 1751500000000,
      "sample_delta_ms": 5000,
      "metrics": { "cpu_usec": 120000, "mem_cur": 5242880, "mem_max": 134217728, "disk_bytes": 40960, "files": 12 },
      "deltas": { "cpu_usec": 800 }
    },
    "history": []
  },
  "workspaces": [
    {
      "workspace_id": "ws-1",
      "lifecycle_state": "active",
      "network_profile": "shared",
      "layers": { "base_root_hash": "3f9d2c81…", "layer_count": 3 },
      "namespace_fd_count": 4,
      "resources": { "latest": null, "history": [] },
      "active_namespace_executions": [
        { "namespace_execution_id": "namespace_execution_7", "operation": "exec_command", "lifecycle_state": "running" }
      ]
    }
  ],
  "stack": { "layer_count": 3, "layers_bytes": 3145728, "active_leases": 1 }
}
```

`availability` is `available` or `partial` (then `errors` lists what
failed).

**V2 — aggregate across the fleet (no `--sandbox-id`)** → routed to the
manager; only `ready` sandboxes with endpoints are queried (fan-out 8,
1500 ms per-daemon timeout). Unreachable sandboxes ride along as
`unavailable` nodes:

```sh
sandbox-manager-cli observability snapshot
```

```json
{
  "sandboxes": [
    { "sandbox_id": "eos-7c9e…", "lifecycle_state": "ready", "availability": "available", "…": "…" },
    {
      "sandbox_id": "eos-2b71…",
      "lifecycle_state": "stopped",
      "availability": "unavailable",
      "sampled_at_unix_ms": null,
      "errors": ["sandbox lifecycle state is stopped"],
      "daemon": { "host": "127.0.0.1", "port": 40001, "daemon_pid": null, "runtime_dir": null },
      "resources": { "latest": null, "history": [] },
      "workspaces": []
    }
  ]
}
```

## `trace`

**V1 — most recent root trace (default `--trace-id last`)** → stdout, exit 0:

```sh
sandbox-manager-cli observability trace --sandbox-id eos-7c9e…
```

```json
{
  "view": "trace",
  "trace": "req-7f3",
  "spans": [
    {
      "span": { "ts": 1751500000000, "trace": "req-7f3", "span": "d-11", "parent": null, "name": "operation.exec_command", "dur_ms": 44.1, "status": "completed", "attrs": {} },
      "offset_ms": 0.0,
      "children": [
        {
          "span": { "ts": 1751500000001, "trace": "req-7f3", "span": "d-12", "parent": "d-11", "name": "command.exec", "dur_ms": 42.5, "status": "completed", "attrs": { "exit_code": 0 } },
          "offset_ms": 1.2,
          "children": [],
          "events": [
            { "offset_ms": 0.8, "event": { "ts": 1751500000001, "trace": "req-7f3", "parent": "d-12", "name": "lease.acquired", "attrs": { "revision": 3 } } }
          ]
        }
      ],
      "events": []
    }
  ]
}
```

Span `status` ∈ `completed | error | cancelled | timed_out`.

**V2 — specific trace** — `--trace-id req-7f3`; same shape.

**V3 — missing `--sandbox-id`** → stderr, exit 2 (space rule above).

## `events`

**V1 — everything, newest first**:

```sh
sandbox-manager-cli observability events --sandbox-id eos-7c9e…
```

```json
{
  "view": "events",
  "events": [
    { "ts": 1751500002400, "trace": "req-7f4", "parent": "d-19", "name": "lease.released", "attrs": {} },
    { "ts": 1751500000001, "trace": "req-7f3", "parent": "d-12", "name": "lease.acquired", "attrs": { "revision": 3 } }
  ]
}
```

(`parent` omitted for parentless events.)

**V2 — filter by exact name** — `--name lease.acquired`.
**V3 — newest N** — `--last-n 20`.
**V4 — since timestamp** — `--since-ms 1751500000000`.
**V5 — combined** — `--name lease.acquired --since-ms … --last-n 5`; filters
AND together.

## `cgroup`

**V1 — sandbox scope, default window (60 s)**:

```sh
sandbox-manager-cli observability cgroup --sandbox-id eos-7c9e…
```

```json
{
  "view": "cgroup",
  "scope": "sandbox",
  "series": [
    { "ts": 1751499995000, "sample_delta_ms": null, "metrics": { "cpu_usec": 119200, "mem_cur": 5183488 }, "deltas": {} },
    { "ts": 1751500000000, "sample_delta_ms": 5000, "metrics": { "cpu_usec": 120000, "mem_cur": 5242880 }, "deltas": { "cpu_usec": 800 } }
  ]
}
```

First in-window sample has `sample_delta_ms: null`; only counters
(`cpu_usec`) get `deltas`. Metric keys: `cpu_usec` `mem_cur` `mem_max`
`mem_max_unlimited` `cgroup_available` `cgroup_error` `disk_bytes` `files`
`disk_truncated`.

**V2 — one workspace** — `--scope ws-1`.
**V3 — custom window** — `--window-ms 300000`; `> 600000` → the
`window_ms exceeds max` error above.

## `layerstack`

**V1 — stack inventory + trend** → stdout, exit 0:

```sh
sandbox-manager-cli observability layerstack --sandbox-id eos-7c9e…
```

```json
{
  "view": "layerstack",
  "manifest_version": 3,
  "root_hash": "3f9d2c81…",
  "active_lease_count": 1,
  "total_bytes": 3145728,
  "layers": [
    { "layer_id": "L000001-0f1e2d3c", "bytes": 1048576, "leased_by_workspaces": 0, "booked_by": ["L000002-9a8b7c6d"] },
    { "layer_id": "L000002-9a8b7c6d", "bytes": 2097152, "leased_by_workspaces": 1, "booked_by": [] }
  ],
  "trend": [
    { "ts": 1751500000000, "layer_count": 3, "layers_bytes": 3145728, "active_leases": 1 }
  ]
}
```

**V2 — one workspace's view** — `--workspace-id ws-7` switches shape to
the mount projection:

```json
{
  "view": "layerstack",
  "workspace": "ws-7",
  "mounts": [
    { "layer_id": "L000001-0f1e2d3c", "shared_with": ["ws-1"] },
    { "layer_id": "L000002-9a8b7c6d", "shared_with": [] }
  ],
  "upper_bytes": 8192
}
```

**V3 — unknown workspace** → stderr, exit 1:
`invalid_request` `"unknown workspace: ws-99"`.

---

# Help & usage variants

| Invocation | Behavior |
|---|---|
| `sandbox-manager-cli` / `sandbox-manager-cli help` | rendered manager catalog help: family summaries + `sandbox-manager-cli OPERATION` usage + one line per visible op + pointer to the `observability` space |
| `sandbox-manager-cli help create_sandbox` | full operation help: description, args (required/optional, defaults), usage line, examples, `related:` ops |
| `sandbox-manager-cli help creat_sandbox` | fuzzy search with did-you-mean suggestions |
| `sandbox-manager-cli observability` / `… observability help [OP]` | observability catalog / operation help (`sandbox-manager-cli observability OPERATION` usage lines) |
| `sandbox-runtime-cli` / `sandbox-runtime-cli help [OP]` | runtime catalog / operation help (usage shows `--sandbox-id`: `sandbox-runtime-cli --sandbox-id ID OPERATION`) |
| `sandbox-manager-cli destroy_sandbox` (required args, empty argv) | prints that operation's help instead of dispatching |
| an op literally named `help` on the wire | CLI-local error: `"help is reserved and cannot be used as an operation name"` |

Hidden ops (`cli: None`) never appear in help output: manager `snapshot`,
runtime `squash_layerstack`.

**Local parse-error catalog** (all → stderr, exit 2, kind `invalid_request`):

| Trigger | Message |
|---|---|
| unknown op | `unknown operation: frobnicate` |
| unknown flag | `unknown flag for exec_command: --shell` |
| flag without value | `--limit requires a value` |
| duplicate flag | `--path was provided more than once` |
| non-integer for Integer arg | `--limit must be an unsigned integer` |
| non-float for Float arg | `--grace-s must be a finite number` |
| stray positional | `unexpected positional argument for list_sandboxes: foo` |
| missing required | `--sandbox-id is required for destroy_sandbox` |

---

# Cross-cutting failures

| Scenario | Stream/exit | Envelope |
|---|---|---|
| gateway not running | stderr / 1 | `{"error":{"kind":"connection_error","message":"gateway connection failed: Connection refused (os error 61)",…}}` |
| malformed gateway reply / oversized line | stderr / 1 | kind `protocol_error` |
| bad or missing auth token | stderr / 1 | kind `unauthorized` (from the gateway) |
| request over `MAX_REQUEST_BYTES` | stderr / 1 | kind `request_too_large` |
| non-JSON on the raw wire | stderr / 1 | kind `bad_json` |
| config discovery failure | stderr / non-zero | kind `config_error` |
| manager panic / join failure | stderr / 1 | kind `internal_error` |

---

# Appendix — wire-only operations (no CLI name)

| Op | Scope | Who calls it | Notes |
|---|---|---|---|
| `squash_layerstack` | sandbox | manager's `checkpoint_squash` | daemon-local squash + remount sweep; response returned verbatim to the CLI |
| `get_observability` | sandbox | CLI observability rewrite | `args.view` selects the daemon view; unknown view → `invalid_request` `"unsupported observability view: X"`; missing view → `"observability request requires a view"` |
| `snapshot` (manager) | system | CLI `observability snapshot` (no id) | hidden aggregate |
| `sandbox_daemon_ready` | sandbox | `sandbox-provider-docker` readiness probe | never user-visible |
| unknown op on the raw wire | — | — | `{"error":{"kind":"unknown_op","message":"unknown operation","details":{}}}`; a manager op sent with sandbox scope → `invalid_request` `"manager operation requires system scope"` |
