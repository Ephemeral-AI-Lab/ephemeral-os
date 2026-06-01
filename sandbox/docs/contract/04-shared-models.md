# 04 — Shared Verb Request/Response Data Models (FROZEN CONTRACT)

Source-of-truth pass for plan §11.2 ("Data-type contract — `shared/models.py` (Python)
↔ `eos-protocol` (Rust): two representations of the same types, kept canonically-equal
by golden fixtures (AV-1)"). Every field/type/default below is verified against the live
code; anchors are `path:line` relative to the repo root
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS`.

The Rust `eos-protocol` crate must reproduce the **wire JSON** of each verb's request
`args` and response object byte-for-canonically-equal. This document gives you that wire
JSON directly — you do not need the Python to reproduce it.

---

## 0. The three-layer trap (READ THIS FIRST)

There are **three distinct representations** and they do **not** all agree. The dataclasses
in `models.py` are the *typed front door*; the wire is a free-form `args` dict. Transcribing
only the dataclasses produces a WRONG wire contract. The layers:

1. **Request dataclass** (`backend/src/sandbox/shared/models.py`) — typed input the
   Python caller builds (e.g. `GrepRequest`). NOT serialized as-is.
2. **Wire request `args`** — what actually crosses to the daemon, built field-by-field by
   the `api/tool/<verb>.py` wrappers (`backend/src/sandbox/api/tool/`). The daemon-side
   primitives (`backend/src/sandbox/shared/tool_primitives/<verb>.py`) read fields out of
   this dict via `args.get(...)` with their **own** defaults/coercions. This is the
   contract the Rust daemon must accept and the Rust client must emit.
3. **Wire response object** — the daemon serializes a result dataclass via recursive
   `dataclasses.asdict` (see §6); the Python client re-parses it via
   `api/tool/_daemon_response_parsing.py`. The Rust daemon must emit the §6 shape; a Rust
   client must accept it.

Wherever (1) and (2) diverge (inert fields, key renames, type coercions) it is called out
inline and repeated in the risks of the returned summary.

### Routing (plan §1 line 89, authoritative)

> Verb routing stays in `eos-daemon` dispatch: `read`/`write`/`edit` fast-path → `eos-occ`;
> `shell`/`glob`/`grep` overlay execution is daemon-owned and delegates namespace work to
> `eos-runner` (the shared search/replace primitive lives in `eos-protocol`).

| Verb | Path | Crate target |
|------|------|--------------|
| `read_file` | **O(1)-snapshot / OCC fast-path** | `eos-occ` |
| `write_file` | **OCC fast-path** (guarded publish) | `eos-occ` |
| `edit_file` | **OCC fast-path** (guarded publish) | `eos-occ` |
| `shell` | **ephemeral overlay pipeline** | `eos-daemon` + `eos-runner` + `eos-overlay` |
| `glob` | **ephemeral overlay pipeline** | `eos-daemon` + `eos-runner` + `eos-overlay` |
| `grep` | **ephemeral overlay pipeline** | `eos-daemon` + `eos-runner` + `eos-overlay` |

Note: the namespace `VERB_TABLE` (`tool_primitives/__init__.py:15-21`) contains
`read_file`/`write_file`/`edit_file`/`grep`/`glob` — but that is the *overlay execution
path* used when a primitive runs inside a namespace, NOT the routing decision. When a
workspace binding exists, read/write/edit take the OCC fast path; only when they run via the
overlay do they hit the primitives here. `shell` is dispatched separately (not in
`VERB_TABLE`) at `overlay/namespace_entrypoint.py:161-181`.

There is **no standalone `search` verb.** The "search/replace primitive" named in the task
is `apply_search_replace` in `backend/src/sandbox/shared/edit_apply.py` (§5), shared by
`edit_file` (tool primitive) and OCC `path_staging.py:63`.

---

## 1. Common base types

### `Intent` — `models.py:15-20`
`str`-valued enum. The **only** enum in the verb data model.

| name | wire value |
|------|-----------|
| `READ_ONLY` | `"read_only"` |
| `WRITE_ALLOWED` | `"write_allowed"` |
| `LIFECYCLE` | `"lifecycle"` |

Serialized as its string `.value` (NOT via `asdict`) inside `ToolCallRequest.to_payload`.
Ground truth: `[('READ_ONLY','read_only'),('WRITE_ALLOWED','write_allowed'),('LIFECYCLE','lifecycle')]`.

### `SandboxCaller` — `models.py:23-48`
`@dataclass(frozen=True, kw_only=True)`. Caller identity threaded onto every audit-aware
request. All fields `str`.

| field | type | default |
|-------|------|---------|
| `agent_id` | `str` | (required) |
| `run_id` | `str` | `""` |
| `agent_run_id` | `str` | `""` |
| `task_id` | `str` | `""` |
| `task_center_run_id` | `str` | `""` |
| `task_center_task_id` | `str` | `""` |
| `task_center_attempt_id` | `str` | `""` |
| `task_center_workflow_id` | `str` | `""` |
| `task_center_request_id` | `str` | `""` |
| `tool_name` | `str` | `""` |
| `tool_id` | `str` | `""` |

`audit_fields()` (`models.py:39-48`) returns the daemon-facing envelope: it ALWAYS includes
the four required keys `{agent_id, run_id, agent_run_id, task_id}` (even when empty) and
includes any other field only if truthy. Ground truth:

```json
// SandboxCaller(agent_id="a1").audit_fields()
{"agent_id": "a1", "run_id": "", "agent_run_id": "", "task_id": ""}
// SandboxCaller(agent_id="a1", run_id="r", tool_name="read_file", tool_id="t1").audit_fields()
{"agent_id": "a1", "run_id": "r", "agent_run_id": "", "task_id": "", "tool_name": "read_file", "tool_id": "t1"}
```

### `SandboxRequestBase` — `models.py:51-60`
`@dataclass(frozen=True, kw_only=True)`. Base for all verb requests.

| field | type | default |
|-------|------|---------|
| `caller` | `SandboxCaller` | (required) |
| `description` | `str` | `""` |
| `invocation_id` | `str` | `""` |

`default_description(fallback)` returns `self.description or fallback`.
**`caller` and `description` are NOT placed on the wire by `ToolCallRequest.to_payload()`**
— they cross only through the per-verb wrappers (see §2 identity envelope) and audit.

### `SandboxResultBase` — `models.py:63-73`
`@dataclass(frozen=True, kw_only=True)`. Base for read/glob/grep results.

| field | type | default |
|-------|------|---------|
| `success` | `bool` | `True` |
| `workspace` | `Literal["ephemeral","isolated"]` | `"ephemeral"` |
| `timings` | `dict[str, float]` | `{}` |
| `conflict` | `ConflictInfo \| None` | `None` |
| `conflict_reason` | `str \| None` | `None` |
| `changed_paths` | `list[str] \| tuple[str,...]` | `[]` |
| `error` | `dict[str, object] \| None` | `None` |

### `GuardedResultBase(SandboxResultBase)` — `models.py:136-145`
Base for write/edit/shell results (OCC/overlay-guarded). Overrides + adds:

| field | type | default |
|-------|------|---------|
| `changed_paths` | `tuple[str,...]` | `()` |
| `changed_path_kinds` | `dict[str, str]` | `{}` |
| `mutation_source` | `str` | `""` |
| `status` | `str` | `""` |
| `conflict` | `ConflictInfo \| None` | `None` |
| `conflict_reason` | `str \| None` | `None` |

### `ConflictInfo` — `models.py:115-133`
`@dataclass(frozen=True, kw_only=True)`. Serializes to `{reason, conflict_file, message}`.

| field | type | default |
|-------|------|---------|
| `reason` | `str` | (required) |
| `conflict_file` | `str \| None` | `None` |
| `message` | `str` | `""` |

Constructors: `ConflictInfo.rejected(reason="rejected", message="")` →
`{reason, conflict_file=None, message}`. `ConflictInfo.overlap(path, message)` →
`{reason="aborted_overlap", conflict_file=path, message}`.

```json
// ConflictInfo.overlap(path="/w/f.txt", message="overlap")
{"reason": "aborted_overlap", "conflict_file": "/w/f.txt", "message": "overlap"}
// ConflictInfo.rejected()
{"reason": "rejected", "conflict_file": null, "message": ""}
```

### `ToolCallRequest` — `models.py:79-112` (the inner overlay-dispatch envelope)
`@dataclass(frozen=True, kw_only=True)`. NOT one of the per-verb request dataclasses; it is
the routed-invocation envelope inside the overlay/namespace pipeline.

| field | type | default |
|-------|------|---------|
| `invocation_id` | `str` | (required) |
| `agent_id` | `str` | (required) |
| `verb` | `str` | (required) |
| `intent` | `Intent` | (required) |
| `args` | `Mapping[str, object]` | (required) |
| `background` | `bool` | `False` |

`to_payload()` emits (Intent as `.value`, args copied to a plain dict):
```json
{"invocation_id":"i1","agent_id":"a1","verb":"read_file","intent":"read_only","args":{"path":"/w/f.txt"},"background":false}
```
`from_payload(payload)` is total: missing scalars → `""`, missing `intent` → `"read_only"`,
missing `args` → `{}`, missing `background` → `false`; non-Mapping `args` raises
`ValueError`. Round-trips exactly.

### `ToolCallResult` — `models.py:76`
`TypeAlias = dict[str, object]`. The overlay pipeline returns a plain dict (the
`_jsonable`/`asdict` of a result dataclass + the post-processing in §6), not a typed object.

### `RawExecResult(SandboxResultBase)` — `models.py:148-154`
One-shot raw provider exec (not a public verb, but in the model). Adds:

| field | type | default |
|-------|------|---------|
| `exit_code` | `int` | (required) |
| `stdout` | `str` | (required) |
| `stderr` | `str` | `""` |

### Identity-envelope wire helper — `api/tool/_daemon_response_parsing.py:23-30`
`daemon_request_identity_fields(request)` prepends to **every** verb's wire `args`:
```json
{"agent_id": "<caller.agent_id>", "caller": { ...audit_fields()... }}
```
plus `"invocation_id": "<...>"` ONLY when `request.invocation_id` is truthy. Each per-verb
wrapper then merges its own keys on top (`identity | {verb-specific}`).

---

## 2. `read_file` — OCC fast-path (read)

- Request dataclass `ReadFileRequest(SandboxRequestBase)` — `models.py:157-159`: adds
  `path: str` (required).
- Wire `args` built by `api/tool/read.py:26`: `identity | {"path": request.path}`.
- Daemon primitive `tool_primitives/read.py:17-28` reads `args.get("path")` via
  `required_workspace_path` (raises `ValueError("path is required")` on empty/missing).

| wire arg | type | required | notes |
|----------|------|----------|-------|
| `agent_id` + `caller` (+ `invocation_id`) | identity envelope | yes | §1 |
| `path` | `str` | yes | required, non-empty |

### Response — `ReadFileResult(SandboxResultBase)` — `models.py:162-166`
Adds `content: str` (required), `exists: bool = True`, `encoding: str = "utf-8"`.

Primitive behavior (`read.py`):
- Opens with symlink-refusing `open_no_follow` (any symlink component → `ValueError`).
- **Missing file is NOT an error**: returns `success=True, content="", exists=False`.
- Size cap `_MAX_READ_BYTES = 16 * 1024 * 1024` (16 MiB); over it raises
  `ValueError("file too large: {size} > {cap} bytes")`.
- Decode is **lenient**: `data.decode("utf-8", "replace")` (invalid bytes → U+FFFD).

Wire response (daemon `asdict` of the dataclass, §6):
```json
// content present
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":[],"error":null,"content":"hello","exists":true,"encoding":"utf-8"}
// missing file
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":[],"error":null,"content":"","exists":false,"encoding":"utf-8"}
```
Client parser `parse_read_file_result` (`_daemon_response_parsing.py:78-85`) reads only
`success`(default `False`), `exists`(default `False`), `content`, `encoding`(default
`"utf-8"`), `timings`. It does NOT read `workspace`/`conflict`/`changed_paths`/`error`
(those default on the client side). **The daemon must still emit the full §6 object.**

---

## 3. `write_file` — OCC fast-path (write, guarded publish)

- Request dataclass `WriteFileRequest(SandboxRequestBase)` — `models.py:169-173`: adds
  `path: str` (required), `content: str` (required), `overwrite: bool = True`.
- Wire `args` (`api/tool/write.py:26-31`):
  `identity | {"path", "content", "description"=default_description("write {path}"), "overwrite"}`.
- Primitive `tool_primitives/write.py:14-21`: `path = required_workspace_path(...)`;
  `content = str(args.get("content") or "")`; `overwrite = bool(args.get("overwrite", True))`.
  Note `content` coercion: a falsy content (`""`, `None`, `0`) becomes `""`.

| wire arg | type | required | default in primitive | notes |
|----------|------|----------|----------------------|-------|
| identity envelope | — | yes | — | §1 |
| `path` | `str` | yes | — | required, non-empty |
| `content` | `str` | yes | `""` | `str(args.get("content") or "")` |
| `description` | `str` | sent by wrapper | — | not read by primitive |
| `overwrite` | `bool` | sent by wrapper | `True` | `O_TRUNC` if true, else `O_EXCL` |

Write semantics (`workspace_filesystem.py:143-155 write_bytes_no_follow`): creates parent
dirs (`mkdir parents=True, exist_ok=True`); `O_WRONLY|O_CREAT`; `O_TRUNC` when
`overwrite=True` else `O_EXCL` (exclusive create, fails if exists); symlink-refusing open;
bytes are UTF-8 encoded.

### Response — `WriteFileResult(GuardedResultBase)` — `models.py:176-178`
No added fields beyond `GuardedResultBase`. Primitive returns
`WriteFileResult(changed_paths=(path,), status="ok")`.

```json
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":["/w/f.txt"],"error":null,"changed_path_kinds":{},"mutation_source":"","status":"ok"}
```
Client parser `parse_guarded_mutation_result(WriteFileResult, ...)`
(`_daemon_response_parsing.py:124-154`) reads `success`(default `False`), `changed_paths`,
`changed_path_kinds`, `mutation_source`(default `""`), `status`(default `""`), `conflict`,
`conflict_reason`, `error` (only if a `dict`), `timings`.

---

## 4. `edit_file` — OCC fast-path (edit, guarded publish)

- Request dataclass `EditFileRequest(SandboxRequestBase)` — `models.py:190-193`: adds
  `path: str` (required), `edits: tuple[SearchReplaceEdit, ...]` (required).
- `SearchReplaceEdit` — `models.py:181-187`, `@dataclass(frozen=True, kw_only=True)`:

  | field | type | default |
  |-------|------|---------|
  | `old_text` | `str` | (required) |
  | `new_text` | `str` | (required) |
  | `replace_all` | `bool` | `False` |

- Wire `args` (`api/tool/edit.py:29-40`):
  `identity | {"path", "edits": [{"old_text","new_text","replace_all"} ...], "description"=default_description("edit {path}")}`.
  Each edit is a JSON object with exactly those three keys.
- Primitive `tool_primitives/edit.py:16-43`:
  - `path = required_workspace_path(...)`.
  - `edits` must be a non-str/bytes `Sequence` of `Mapping`s else `ValueError`.
  - per edit: `old_text = str(raw.get("old_text") or "")`,
    `new_text = str(raw.get("new_text") or "")`,
    `replace_all = bool(raw.get("replace_all", False))`.
  - reads current file with **strict** UTF-8 decode (`read_bytes_no_follow(path).decode("utf-8")`
    — raises `UnicodeDecodeError` on non-UTF-8, unlike `read_file`'s lenient decode).
  - applies each edit in order via `apply_search_replace` (§5).
  - writes back UTF-8 with `write_bytes_no_follow` (default `overwrite=True`).

| wire arg | type | required | notes |
|----------|------|----------|-------|
| identity envelope | — | yes | §1 |
| `path` | `str` | yes | required, non-empty |
| `edits` | `list[{old_text:str,new_text:str,replace_all:bool}]` | yes | applied left-to-right |
| `description` | `str` | sent by wrapper | not read by primitive |

### Response — `EditFileResult(GuardedResultBase)` — `models.py:196-198`
Adds `applied_edits: int = 0`. Primitive returns
`EditFileResult(changed_paths=(path,), status="ok", applied_edits=len(edits))`.

```json
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":["/w/f.txt"],"error":null,"changed_path_kinds":{},"mutation_source":"","status":"ok","applied_edits":2}
```
Client parser uses `parse_guarded_mutation_result(EditFileResult, ..., applied_edits=strict_int_from_daemon_field(response.get("applied_edits"), default=0))`
(`api/tool/edit.py:48-54`). On an edit-conflict exception the client synthesizes
`status="aborted_overlap"`, `conflict=ConflictInfo.overlap(path, message)`,
`applied_edits=0`, `success=False` (`edit.py:56-68`) — that is a client-side path, not a
daemon response shape, but the daemon's OCC reject path produces an equivalent guarded
result with a conflict object.

---

## 5. The shared search/replace primitive — `apply_search_replace`

`backend/src/sandbox/shared/edit_apply.py:21-48`. Single source of truth for
`replace_all`/occurrence-count semantics; shared by `edit_file` and OCC `path_staging.py:63`.
Plan §1 line 89: "the shared search/replace primitive lives in `eos-protocol`."

`apply_search_replace(text: str, old: str, new: str, *, replace_all: bool) -> str` — pure;
raises `SearchReplaceError(ValueError)` (carries `.message`) on failure:

- `old` empty → `SearchReplaceError("edit anchor old_text must be non-empty")`.
- `count = text.count(old)`.
- `replace_all=True`: if `count == 0` → `SearchReplaceError("anchor not found")`; else
  `text.replace(old, new)` (every occurrence).
- `replace_all=False`: must occur **exactly once**. `count == 0` →
  `SearchReplaceError("anchor not found")`; `count > 1` →
  `SearchReplaceError("anchor occurrence count mismatch")`; `count == 1` →
  `text.replace(old, new, 1)`.

`count` uses Python `str.count` = number of **non-overlapping** occurrences of the substring
(byte-for-byte exact match on the decoded `str`, no regex). The Rust port must match this
non-overlapping substring count and the exact error message strings.

---

## 6. Daemon response serialization (how a result dataclass becomes wire JSON)

Two equivalent transforms, both recursive dataclass → object:

- OCC fast-path / dispatcher: `daemon/rpc/dispatcher.py:232-241` `_to_jsonable`:
  dataclass → `{k: _to_jsonable(v) for k,v in asdict(obj).items()}`; `list`/`tuple` → JSON
  array; `dict` → object with `str(k)` keys; everything else passthrough.
- Overlay pipeline: `overlay/namespace_entrypoint.py:259-266` `_jsonable`: identical
  transform (dataclass via `asdict`, Mapping → str-keyed object, list/tuple → array).

So: **`tuple` serializes as a JSON array**; `dict[str,float]` `timings` as a JSON object;
`None` as `null`; nested `ConflictInfo` as a nested object. No result dataclass contains an
`Enum` or `bytes` field (verified by scanning all of `models.__all__`); the only enum
(`Intent`) is request-side and is serialized via `.value`, not `asdict`.

### Overlay post-processing — `namespace_entrypoint.py:194-201`
After a primitive returns, the overlay path applies `setdefault`:
`success`→`True`, `status`→`"ok"` if success else `"error"`, `workspace`→`"ephemeral"`,
`timings`→`{}`; then injects `timings["workspace.tool_s"] = elapsed` and merges any
dispatcher timings. **Therefore overlay verbs (shell/glob/grep) carry a
`timings["workspace.tool_s"]` key on the wire** that the bare dataclass `asdict` does not
show. (Read/write/edit on the OCC fast path do not pass through this overlay block.)

### Error wire shape — `dispatcher.py:215-229` `_error_envelope`
When a handler raises, the daemon emits:
```json
{"success":false,"warnings":[],"timings":{},"error":{"kind":"<kind>","message":"<msg>","details":{}}}
```
The `error` field on a normal guarded result (`GuardedResultBase.error: dict|None`) carries
this `{kind, message, details}` shape when populated; the client parser only keeps it if it
is a `dict` (`parse_guarded_mutation_result`, `_daemon_response_parsing.py:147`).

### RPC frame — `daemon/rpc/server.py:133`
`json.dumps(response, separators=(",", ":"))` + trailing `b"\n"` (newline-delimited JSON,
compact separators, no spaces). The CAS-byte-identity gate (AV-1c) is about content
payloads, not this RPC framing.

---

## 7. `shell` — shell-string overlay pipeline; historical CP-4s argv retired

- Rust target request contract:

  | field | type | default | notes |
  |-------|------|---------|-------|
  | `command` | `str` | (required) | shell-format command string |
  | `cwd` | `str \| None` | `None` | |
  | `timeout` | `int \| None` | `None` | |
  | `stdin` | `str \| None` | `None` | **rejected** by wrapper (see below) |
  | `background` | `bool` | `False` | metadata only; engine owns bg lifecycle |

- Wire `args` (`api/tool/shell.py:29,49-56`):
  - `cwd = (request.cwd or "").strip() or "."` (so wire `cwd` is never empty/None — defaults `"."`).
  - If `request.stdin is not None` the wrapper **short-circuits before dispatch** and returns
    a `ShellResult(success=False, exit_code=1, status="error",
    conflict=ConflictInfo.rejected(reason="stdin_not_supported", message="snapshot overlay
    shell does not accept stdin"), conflict_reason=message)`. `stdin` is therefore NEVER on
    the snapshot-overlay shell wire. (Isolated-workspace exec is a different path that DOES
    support stdin via base64 — out of scope here, see `isolated_workspace/...`.)
  - Wire object: `identity | {"command": request.command, "cwd",
    "timeout_seconds": request.timeout, "description": default_description("shell")}`;
    adds `"background": true` only when `request.background` is set.
  - **Key rename: dataclass `timeout` → wire `timeout_seconds`** (value is the int as-is, or `null`).

- Daemon primitive path reads from `args` and `payload`:
  - `_shell_argv(req.args)` / Rust equivalent builds argv from `args["command"]`.
    A string `command` must be non-empty and is executed as
    `/bin/bash --noprofile --norc -c <command>`. The Rust daemon shell boundary
    rejects raw argv; historical CP-4s raw-argv evidence is no longer a live
    public shell contract. Plugin one-shot workers use the dedicated
    `plugin_service` runner verb for argv commands instead of a shell escape
    hatch.
  - `cwd = str(req.args.get("cwd") or ".")` — default `"."`.
  - `env = _string_mapping(req.args.get("env"))` — the primitive **reads `env`** even though
    no model field exists for it (str→str map; out-of-band wire key).
  - `timeout_seconds = _optional_float(req.args.get("timeout_seconds", req.args.get("timeout")))`
    — reads `timeout_seconds` first, falls back to alias `timeout`, **coerced to float**
    (dataclass type is `int`).
  - `stdout_ref`/`stderr_ref`/`policy` come from the **`payload`** (the overlay framing), NOT
    from `args`. `policy = CommandExecPolicy.from_payload(payload["policy"] if dict else {})`.
  - `stdin` is **never read** by the primitive.

| wire arg | type | source | primitive reads | notes |
|----------|------|--------|-----------------|-------|
| identity envelope | — | wrapper | — | §1 |
| `command` | `str` | wrapper | yes (via `_shell_argv`) | non-login Bash string |
| `command` | `list[str]` | historical CP-4s harness | no at daemon shell boundary | rejected; retained only as historical evidence |
| `cwd` | `str` | wrapper (`.`-default) | yes (`or "."`) | |
| `timeout_seconds` | `int \| null` | wrapper (renamed from `timeout`) | yes (→float) | alias `timeout` also accepted |
| `description` | `str` | wrapper | no | |
| `background` | `bool` | wrapper (only if true) | (engine-level) | |
| `env` | `dict[str,str]` | NOT sent by this wrapper | **yes** | inert here; reserved/other callers |
| `stdin` | — | rejected pre-dispatch | never | not on wire |

### `shell.run` signature — `tool_primitives/shell.py:17-48`
`run(command, *, workspace_root, cwd=".", env=None, timeout_seconds=None, stdout_ref,
stderr_ref, cancel_event=None, pid_recorder=None, policy=DEFAULT_COMMAND_EXEC_POLICY)`.
Delegates to `overlay/subprocess_runner.run_command_to_refs`; writes stdout/stderr to refs,
returns exit code; stdout/stderr are read back **lenient** decode
(`Path(ref).read_bytes().decode("utf-8","replace")`); `status = "ok" if exit_code==0 else "error"`.

### Response — `ShellResult(GuardedResultBase)` — `models.py:212-217`
Adds `exit_code: int` (required), `stdout: str` (required), `stderr: str = ""`,
`warnings: tuple[str,...] = ()`.

```json
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":[],"error":null,"changed_path_kinds":{},"mutation_source":"","status":"ok","exit_code":0,"stdout":"out","stderr":"","warnings":[]}
```
(On the wire from the overlay path, `timings` will also carry `workspace.tool_s` per §6.)
Client parser `parse_shell_result` (`_daemon_response_parsing.py:167-180`):
`parse_guarded_mutation_result(ShellResult, response,
exit_code=strict_int_from_daemon_field(response.get("exit_code"), default=1),
stdout=str(...), stderr=str(...), warnings=parse_path_tuple_field(...), timings=timings)`.
Note client default `exit_code=1` when daemon omits it.

---

## 8. `glob` — overlay pipeline

- Request dataclass `GlobRequest(SandboxRequestBase)` — `models.py:220-223`: adds
  `pattern: str` (required), `path: str | None = None`.
- Wire `args` (`api/tool/glob.py:26-28`): `identity | {"pattern": request.pattern}`; adds
  `"path"` **only if `request.path is not None`**.
- Primitive `tool_primitives/glob.py:20-35`:
  - `pattern = str(args.get("pattern") or "").strip()`; empty → `ValueError("pattern is required")`.
  - `root = search_root_path(args.get("path") or ".")` — **path default is `"."`** (NOT `None`;
    diverges from the dataclass default).
  - matching: `walk_dirs_no_follow` (no symlink descent), excludes any path containing
    `/.git/`, computes workspace-relative posix path, `fnmatch`/`PurePosixPath.match` against
    `pattern` (with `**/` also tried stripped), and only regular files via
    `is_regular_file_no_follow`.
  - `DEFAULT_GLOB_LIMIT = 100`: results sorted, sliced to first 100; `truncated = (matches > 100)`.

| wire arg | type | required | primitive default | notes |
|----------|------|----------|-------------------|-------|
| identity envelope | — | yes | — | §1 |
| `pattern` | `str` | yes | — | empty → error |
| `path` | `str` | optional (sent only if non-None) | `"."` | search root |

### Response — `GlobResult(SandboxResultBase)` — `models.py:226-230`
Adds `filenames: tuple[str,...] = ()`, `num_files: int = 0`, `truncated: bool = False`.

```json
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":[],"error":null,"filenames":["a.py","b.py"],"num_files":2,"truncated":false}
```
Client parser `parse_glob_result` (`_daemon_response_parsing.py:88-95`) reads
`success`(default `False`), `filenames`, `num_files`(strict-int default `0`),
`truncated`, `timings`.

---

## 9. `grep` — overlay pipeline

- Request dataclass `GrepRequest(SandboxRequestBase)` — `models.py:233-243`:

  | field | type | default |
  |-------|------|---------|
  | `pattern` | `str` | (required) |
  | `path` | `str \| None` | `None` |
  | `glob_filter` | `str \| None` | `None` |
  | `output_mode` | `str` | `"files_with_matches"` |
  | `head_limit` | `int \| None` | `None` |
  | `offset` | `int` | `0` |
  | `case_insensitive` | `bool` | `False` |
  | `line_numbers` | `bool` | `False` |
  | `multiline` | `bool` | `False` |

- Wire `args` (`api/tool/grep.py:26-39`):
  `identity | {"pattern", "output_mode", "offset", "case_insensitive", "line_numbers",
  "multiline"}`; adds `"path"` if non-None; adds `"glob_filter"` if non-None; adds
  `"head_limit"` **only if non-None**.
- Primitive `tool_primitives/grep.py:36-102` reads from `args` via `_options`:
  - `pattern = str(args.get("pattern") or "")`; empty → `ValueError("pattern is required")`.
  - `root = Path(search_root_path(args.get("path") or "."))` — **path default `"."`** (diverges from `None`).
  - `case_insensitive = bool(args.get("case_insensitive", False))`.
  - `glob_filter = str(...) if truthy else None`.
  - `output_mode = str(args.get("output_mode") or "files_with_matches")`, one of
    `{"content","files_with_matches","count"}` (`_GrepOutputMode`).
  - `line_numbers = bool(args.get("line_numbers", False))`; `multiline = bool(args.get("multiline", False))`.
  - **`head_limit` and `offset` are NOT read by the primitive** — they are placed on the wire
    by the wrapper but `grep_files` never consults them; it hardcodes the result's
    `applied_limit=None, applied_offset=0, truncated=False` (`grep.py:83-85`). They are
    wire-present, primitive-inert.

  Regex flags: always `re.MULTILINE`; `+ re.IGNORECASE` if `case_insensitive`; `+ re.DOTALL`
  if `multiline`. Pattern is a Python `re` regex (not fnmatch). Per-file byte cap
  `_MAX_FILE_BYTES = 2 * 1024 * 1024` (2 MiB) — over it the file is **silently skipped**
  (`continue`), not an error; non-UTF-8/OSError files also silently skipped. `glob_filter` is
  `fnmatch` over the workspace-relative path.

  Output assembly: `content` mode → matching lines `"{rel}:{lineno}:{line}"` (with line
  numbers) or `"{rel}:{line}"`; `count` mode → `"{rel}:{count}"` per file; both join with
  `"\n"` and append a trailing `"\n"` if non-empty. `num_lines` is nonzero only for
  `content` mode.

| wire arg | type | required | primitive default | notes |
|----------|------|----------|-------------------|-------|
| identity envelope | — | yes | — | §1 |
| `pattern` | `str` | yes | — | regex; empty → error |
| `output_mode` | `str` | sent always | `"files_with_matches"` | content/files_with_matches/count |
| `offset` | `int` | sent always | (ignored) | **inert in primitive** |
| `case_insensitive` | `bool` | sent always | `False` | |
| `line_numbers` | `bool` | sent always | `False` | |
| `multiline` | `bool` | sent always | `False` | |
| `path` | `str` | if non-None | `"."` | search root |
| `glob_filter` | `str` | if non-None | `None` | fnmatch filter |
| `head_limit` | `int` | if non-None | (ignored) | **inert in primitive** |

### Response — `GrepResult(SandboxResultBase)` — `models.py:246-256`
Adds: `output_mode: str = "files_with_matches"`, `filenames: tuple[str,...] = ()`,
`content: str = ""`, `num_files: int = 0`, `num_lines: int = 0`, `num_matches: int = 0`,
`applied_limit: int | None = None`, `applied_offset: int = 0`, `truncated: bool = False`.

```json
{"success":true,"workspace":"ephemeral","timings":{},"conflict":null,"conflict_reason":null,"changed_paths":[],"error":null,"output_mode":"content","filenames":["a.py"],"content":"a.py:1:hit\n","num_files":1,"num_lines":1,"num_matches":1,"applied_limit":null,"applied_offset":0,"truncated":false}
```
Client parser `parse_grep_result` (`_daemon_response_parsing.py:98-121`): reads
`success`(default `False`), `output_mode`(default `"files_with_matches"`), `filenames`,
`content`, `num_files`/`num_lines`/`num_matches`(strict-int default `0`),
`applied_limit`(strict-int but `None` if the field is `None`/absent),
`applied_offset`(strict-int default `0`), `truncated`, `timings`.
**`strict_int_from_daemon_field` rejects bool-as-int** (raises `TypeError`) and accepts only
`None`→default or real `int`; the Rust side must emit these counters as JSON integers, never
booleans.

---

## 10. Embedded constants (must reproduce exactly)

| constant | value | where | effect |
|----------|-------|-------|--------|
| `_MAX_READ_BYTES` | `16 * 1024 * 1024` (16 MiB) | `tool_primitives/read.py:14` | read over cap → `ValueError` |
| `_MAX_FILE_BYTES` | `2 * 1024 * 1024` (2 MiB) | `tool_primitives/grep.py:21` | grep file over cap → silently skipped |
| `DEFAULT_GLOB_LIMIT` | `100` | `tool_primitives/glob.py:17` | glob sorted, sliced to 100; `truncated` if more |
| read decode | `utf-8`, errors=`replace` | `read.py:28` | lenient |
| edit decode | `utf-8`, **strict** | `edit.py:18` | raises on non-UTF-8 |
| shell stdout/stderr decode | `utf-8`, errors=`replace` | `shell.py:45-46` | lenient |
| grep decode | `utf-8`, strict (file skipped on error) | `grep.py:59-61` | non-UTF-8 file skipped |

---

## 11. Verb → model name index

| verb | request dataclass | response dataclass | base | path class |
|------|-------------------|--------------------|------|------------|
| `read_file` | `ReadFileRequest` | `ReadFileResult` | `SandboxResultBase` | OCC fast-path (read / O(1)-snapshot) |
| `write_file` | `WriteFileRequest` | `WriteFileResult` | `GuardedResultBase` | OCC fast-path (write) |
| `edit_file` | `EditFileRequest` (+ `SearchReplaceEdit`) | `EditFileResult` | `GuardedResultBase` | OCC fast-path (edit) |
| `shell` | `ShellRequest` | `ShellResult` | `GuardedResultBase` | overlay pipeline |
| `glob` | `GlobRequest` | `GlobResult` | `SandboxResultBase` | overlay pipeline |
| `grep` | `GrepRequest` | `GrepResult` | `SandboxResultBase` | overlay pipeline |
| (search/replace) | `apply_search_replace` (free fn; `SearchReplaceError`) | — | — | shared primitive (lives in `eos-protocol`) |
| (envelope) | `ToolCallRequest` | `ToolCallResult` (= `dict`) | — | overlay dispatch |
| (raw exec) | — | `RawExecResult` | `SandboxResultBase` | provider raw exec |
| (common) | `SandboxCaller`, `SandboxRequestBase` | `SandboxResultBase`, `GuardedResultBase`, `ConflictInfo` | — | — |

Lifecycle models (`EnterIsolatedWorkspaceRequest/Result`, `ExitIsolatedWorkspaceRequest/Result`,
`LifecycleError`, `LifecycleResultBase`, `models.py:259-298`) are isolated-workspace
lifecycle, NOT file/shell/search verbs — out of scope for this doc; noted for completeness.

---

## 12. Risks / non-obvious serialization (carried to the summary)

1. **Request dataclass ≠ wire `args`.** The dataclasses are not serialized; `api/tool/*`
   builds the wire dict. A Rust author transcribing dataclasses gets it wrong. Divergences:
   `timeout`→`timeout_seconds` (shell), `path` default `None`→`"."` (glob/grep primitives),
   `cwd` default `None`→`"."` (shell), inert `head_limit`/`offset` (grep), inert `env`
   reachable by shell primitive but never sent by the wrapper, `stdin` rejected pre-dispatch.
2. **`Intent` is the only enum; serialized as its `str` value** via `to_payload` (NOT
   `asdict`). No result dataclass contains an enum or `bytes` field — all results are
   plain `asdict`.
3. **Optional fields:** `conflict: ConflictInfo|None` and `conflict_reason: str|None`
   serialize to `null` when unset; `applied_limit: int|None` is `null` when absent. Client
   parsers default-fill many fields when the daemon omits them (e.g. `success` defaults to
   `False`, shell `exit_code` to `1`) — the daemon must still emit the full §6 object.
4. **`tuple` → JSON array** everywhere (`changed_paths`, `filenames`, `warnings`); `dict` →
   object. Empty tuple → `[]`, empty dict → `{}`.
5. **Counters must be JSON integers, not booleans** — `strict_int_from_daemon_field`
   raises `TypeError` on bool; the Rust runtime must emit `num_files`/`num_lines`/etc. as ints.
6. **Decode-handler divergence is wire-visible:** `read_file`/shell decode lenient
   (`replace` → U+FFFD), but `edit_file` and `grep` decode strict — `edit_file` raises on
   non-UTF-8, `grep` silently skips the file. Same input bytes ⇒ different outcomes per verb.
7. **Overlay post-processing injects `timings["workspace.tool_s"]`** for shell/glob/grep on
   the wire (`namespace_entrypoint.py:194-201`); the bare dataclass `asdict` does not show it.
   Read/write/edit (OCC fast path) do not pass through that block.
8. **`SearchReplaceError` messages are part of the contract** — exact strings
   "edit anchor old_text must be non-empty", "anchor not found",
   "anchor occurrence count mismatch"; `count` is Python non-overlapping `str.count`.
9. **RPC framing:** newline-delimited compact JSON (`separators=(",",":")` + `\n`,
   `server.py:133`). Distinct from the AV-1c CAS byte-identity requirement (content payloads).
10. **`error` field shape** is `{kind, message, details}` (`dispatcher.py:215-228`), kept by
    the client only when it is a JSON object.
