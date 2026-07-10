---
title: Runtime File Operations — read / write / edit
tags:
  - ephemeral-os
  - layerstack
  - sandbox
  - runtime
  - file
  - namespace
  - implementation-plan
status: landed
updated: 2026-07-11
---

# Runtime File Operations — read / write / edit

> **Landed design record (operation-layout exempt, 2026-07-11):** Behavioral
> contracts remain applicable. Implementation paths below describe the tree in
> which the feature landed; current ownership follows the operation-migration
> architecture.

## Goal

Add `read`, `write`, and `edit` runtime operations to the sandbox `file`
domain. They must be **signature-symmetric** to the `ephemeral-agent` local-os
tools of the same name, plus one optional argument: `workspace_session_id`,
which behaves exactly like `exec_command` — resolve that session when present,
operate against the layerstack snapshot when absent.

Target shape:

```text
file_read  --path P [--offset N] [--limit N] [--workspace-session-id ID]
file_write --path P  content     [--workspace-session-id ID]
file_edit  --path P  edits[]      [--workspace-session-id ID]

workspace_session_id present -> run the op INSIDE the session namespace (through the
                                mounted overlay the shell also writes through); DO NOT publish
workspace_session_id absent  -> read from latest snapshot; write/edit publish a layer
```

The three impls ship next to `blame` as `impl FileService` blocks:

```text
crates/sandbox-runtime/operation/src/file/service/impls/
  blame.rs   (exists)
  read.rs    (new)
  write.rs   (new)
  edit.rs    (new)
```

## Symmetry Contract

The local-os reference lives at
`ephemeral-agent/packages/ephai-agent/src/tools/workspace/local/{read,write,edit}.ts`.
Match meaning; keep the sandbox file-domain argument name `path` (the existing
`file_blame` convention) and add `workspace_session_id`.

| Op | Sandbox args | Local-os source arg |
|---|---|---|
| read | `path`, `offset?` (1-indexed), `limit?` (default 2000), `workspace_session_id?` | `file_path` |
| write | `path`, `content`, `workspace_session_id?` | `file_path` |
| edit | `path`, `edits: [{ old_string, new_string, replace_all? }]`, `workspace_session_id?` | `file_path` |

Output fields keep the local-os names that are meaningful in the sandbox; drop
host-only fields (`mtime_ms`, `previous_mtime_ms`) that a layerstack publish has
no faithful analog for.

```text
read  -> { path, content, start_line, num_lines, total_lines,
           bytes_read, total_bytes, next_offset, truncated }
write -> { type: "create" | "update", path, bytes_written }
edit  -> { type: "edit", path, edits_applied, replacements, bytes_written }
```

Read validation and text shaping also follow the local-os tool: `limit` defaults
to 2000 and must be `1..=2000`; `offset <= 1` starts at line 1; UTF-8 text drops
a leading BOM and normalizes `\r\n` / `\r` to `\n` before line windowing. Large
reads stream through the file and cap selected output bytes; they do **not**
reject a file merely because the whole file exceeds the output cap.
`MAX_OUTPUT_BYTES` is the cap on the selected read response, not the source file.
If the selected response would exceed it, return `OutputTooLarge`; `truncated`
only means more lines remain after this window. `MAX_EDIT_BYTES = 4 MiB` is the
bounded full-file cap used only by `edit`. Session `ReadFile` returns those bytes
as base64 in `RunResult.payload`, and the shared non-PTY launcher drains
`result_fd` concurrently while the child runs, rejecting encoded envelopes over
`MAX_RUNNER_RESULT_BYTES = 8 MiB`. No bytes side-channel is needed in this pass.

Intentional sandbox-specific divergence: no extension-based binary denylist.
Sandbox reads reject non-UTF-8 content at decode time. Symlinks and directories
are rejected as invalid request on both backends instead of being followed or
encoded as bytes; for namespace reads/writes/edits this includes symlink parent
components, not just the final path.

## Operation Matrix

The two axes are the operation and whether a `workspace_session_id` was given.
Every cell reuses existing runtime primitives; the layerstack changes are
classified read-window/full-file read helpers plus `amend_path`, with no
storage-format change.

| | no `workspace_session_id` | `workspace_session_id` present |
|---|---|---|
| **read** | `LayerStackService::read_current_window` over the active manifest | resolve session; run a namespace read-window against the session's mounted workspace |
| **write** | atomic overwrite of head under the writer lock (`amend_path`, last-writer-wins) as `operation:<request_id>`; blame attributed inside publish | run a namespace file write against the session's mounted workspace; **no publish** — attributed later on session capture |
| **edit** | atomic read-modify-write of head under the writer lock (`amend_path`); no OCC retry | run namespace file read → apply ordered edits → namespace file write; **no publish** |

This mirrors `exec_command`: in-session mutations stay in the session overlay and
are attributed to `workspace_session:<id>` when the session is later captured;
sessionless mutations publish immediately and are attributed to
`operation:<id>`.

### Sessionless vs session — two backends

| | sessionless (layerstack backend) | session (namespace backend) |
|---|---|---|
| touches | layerstack service only — **no namespace, no mount, no fork** | the session's live overlay, **through the mount**, via a per-operation setns runner |
| source of truth | latest published snapshot (`head`); the host `workspace_root` bind is detached after base build (`services.rs`), so the snapshot — not any host path — is authoritative | the merged overlay view the shell also sees |
| write result | a published layer, immediately | an `upperdir` change, captured later |
| attribution | `operation:<request_id>` at publish | `workspace_session:<id>` at capture |
| sees a live session's uncommitted edits? | no — by design (isolation) | yes — its own |

The asymmetry with `exec_command` is deliberate: a sessionless *command* still
needs a temporary workspace namespace because it runs arbitrary code, but a sessionless
*file op* needs no namespace at all — the snapshot is sufficient, so it stays on
the cheap layerstack path. The namespace is entered **only** when a
`workspace_session_id` pins a live overlay whose coherence must be preserved.

## Architecture

### Why the impls take collaborators as parameters

`FileService` today owns only the append-only auditability store (`blame` +
`record_layer_publish`). Critically, `LayerStackService` **already holds**
`Arc<FileService>` so it can write blame events after each commit:

```text
LayerStackService  --owns Arc-->  FileService(audit store)
```

`read`/`write`/`edit` need the layerstack and the workspace-session service. If
`FileService` owned those back, construction would be an unbreakable cycle
(`layerstack` needs `file` at build time, so `file` cannot need `layerstack`).

Therefore the new impls receive their collaborators **by parameter**, not as
fields. `FileService` stays audit-only and does not gain `workspace_root`.
Path mapping uses the session handle's `workspace_root` when a session is
present; sessionless absolute-path mapping reads the existing layerstack
workspace binding.

```rust
impl FileService {
    pub fn read(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: ReadInput,
    ) -> Result<ReadOutput, FileOperationError>;

    pub fn write(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: WriteInput,
    ) -> Result<WriteOutput, FileOperationError>;

    pub fn edit(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: EditInput,
    ) -> Result<EditOutput, FileOperationError>;
}
```

The dispatch layer already holds every service on `SandboxRuntimeOperations`, so
it passes them through with no new wiring:

```rust
operations.file.read(
    operations.layerstack.as_ref(),
    operations.workspace_session.as_ref(),
    input,
)
```

Dependency direction stays acyclic: `file ops -> {layerstack, workspace_session}`
and `layerstack -> file(audit)`. Session file ops reach `namespace-execution`
only through the existing workspace-session/workspace-runtime edge. The
namespace file runner is a private helper, not a new top-level service and not a
`SandboxRuntimeOperations` field.

### Module layout

```text
file/
  error.rs        + FileOperationError (peer to FileError)
  mod.rs          re-export FileOperationError + DTOs
  audit.rs        (unchanged) record_layer_publish
  service.rs      + mod dto / support / namespace; re-export DTOs
  service/
    core.rs       unchanged: FileService stays auditability-store only
    store.rs      (unchanged)
    dto.rs        (new) Read/Write/Edit In & Out, EditOp
    support.rs    (new) resolve_layer_path + windowing helpers
    namespace.rs  (new) call WorkspaceSessionService::run_file_op for session ops
    impls/
      mod.rs      + mod read; mod write; mod edit;
      blame.rs    (unchanged)
      read.rs     (new) impl FileService::read
      write.rs    (new) impl FileService::write
      edit.rs     (new) impl FileService::edit
```

## Path Semantics

The sandbox protocol field is `path`, matching existing `file_blame`. `--path`
maps directly to `path`; any higher-level local-os adapter may translate
`file_path` to `path` before calling the runtime.

`path` is accepted as **either** an absolute path under the configured
workspace root **or** a repo-relative path, then normalized through `LayerPath`
(the same normalization `blame` uses, where `./src/x` == `src/x`).

```text
if path is absolute and under workspace_root -> strip prefix -> LayerPath::parse
else                                         -> LayerPath::parse(path as-is)
absolute but outside workspace_root                -> InvalidPath (LayerPath rejects leading '/')
```

The workspace root source is contextual:

```text
session present -> handler.handle.workspace_root
session absent  -> layerstack workspace binding
```

Do not store this on `FileService`.

```rust
// file/service/support.rs
pub(super) fn resolve_layer_path(
    workspace_root: &Path,
    path: &str,
) -> Result<LayerPath, FileOperationError> {
    let candidate = match Path::new(path).strip_prefix(workspace_root) {
        Ok(rel) => rel
            .to_str()
            .ok_or_else(|| FileOperationError::InvalidPath(path.to_owned()))?
            .to_owned(),
        Err(_) => path.to_owned(),
    };
    LayerPath::parse(&candidate)
        .map_err(|_| FileOperationError::InvalidPath(path.to_owned()))
}
```

## Session Filesystem Access (workspace_session_id present)

### Required target

A live session's merged workspace exists inside the session mount namespace.
Session file operations must therefore run inside that namespace against
`handle.workspace_root`. Do not mutate `entry.upperdir` from the host.

Host-side `capture_changes` may read `upperdir` after command execution, but
that does not make host-side writes to a mounted overlay correct. The runtime
also supports still-running commands in caller-owned sessions, so turn-based
non-concurrency is not an invariant.

### Unified namespace runner (shell + file)

A session file op and `exec_command` are the **same shape**: `setns` into the
session's holder namespaces, run a body, return a result. We add the file body to
the **existing ns-runner harness** rather than building a parallel mechanism.
`read` uses a streamed text-window primitive; `edit` uses a bounded full-file read
and composes the edit in the file domain:

```text
FileRunnerOp =
  ReadWindow { rel, offset, limit, output_cap }
  ReadFile   { rel, max_bytes }        # edit only
  Write      { rel, content }

FileRunnerResult =
  ReadWindow { existed, content, start_line, num_lines, total_lines,
               bytes_read, total_bytes, next_offset, truncated }
  ReadFile   { bytes_b64, existed, total_bytes }  # service helper decodes to bytes
  Write      { existed, bytes_written }

FileRunnerError =
  NotRegular { kind }
  NotUtf8
  FileTooLarge { size, limit }       # ReadFile only
  OutputTooLarge { limit }           # ReadWindow selected output only
  Io { path, message }
```

The dispatch seam already exists. `sandbox-daemon ns-runner` selects a body by
mode flag, and every mode shares one `setns` join and one request/result
protocol — the file body is the third body next to shell and overlay-mount. Make
the runner body explicit; there is no no-flag shell default:

```text
sandbox-daemon ns-runner (--shell | --mount-overlay | --file-op) --request-fd FD --result-fd FD
  --shell         -> daemon/src/runner/shell.rs         -> runner::run           (setns → shell, interactive/PTY)
  --mount-overlay -> daemon/src/runner/mount_overlay.rs -> setns_overlay_mount   (setns → mount, request/result)
  --file-op (NEW) -> daemon/src/runner/file_op.rs       -> runner::run_file_op   (setns → file,  request/result)

enum NsRunnerOperation { Shell, MountOverlay, FileOp }
```

Rename the module-private `Run` operation to `Shell`, add `--shell` and
`--file-op` parser arms, require exactly one mode flag, dispatch to the matching
runner body, and add `spawn_file_op` using the same runner-mode spawn helper as
`spawn_overlay_mount`. `spawn_pty` passes `--shell` and no setup timeout;
`spawn_overlay_mount` passes `--mount-overlay` and `Some(setup_timeout_s)`;
`spawn_file_op` passes `--file-op` and `Some(setup_timeout_s)`. Do not add a
`RunnerWait` enum; keep the wait behavior as the existing child wait plus an
optional setup timeout on request/result runner launches. The shared
runner-mode helper must start draining `result_fd` before waiting on the child,
and must cap the encoded result envelope at `MAX_RUNNER_RESULT_BYTES`.

Runner placement is shared launch policy, not file-op logic:

```rust
pub struct RunnerPlacement {
    pub cgroup_procs_path: Option<PathBuf>,
}

impl RunnerPlacement {
    pub fn none() -> Self;
    pub fn cgroup(cgroup_procs_path: PathBuf) -> Self;
}
```

`spawn_pty`, `spawn_overlay_mount`, and `spawn_file_op` all pass a
`RunnerPlacement` into the launcher. The launcher writes the freshly spawned
ns-runner pid to `cgroup.procs` when present. `exec_command` and session file ops
derive `handler.cgroup_path.join("cgroup.procs")` in the operation/workspace
layer and pass `RunnerPlacement::cgroup`; overlay mount uses
`RunnerPlacement::none`. Do not put cgroup fields on `NamespaceTarget`, and do
not make `namespace-execution` depend on `WorkspaceSessionHandler` — it stays a
lower-level launcher crate.

Shared and unchanged:

- `setns_user_mnt` (`runner/setns/namespaces.rs`): `setns(user)` then
  `setns(mnt)` — enough for filesystem ownership and the mounted overlay view.
- `NamespaceRunnerRequest { request_id, args, workspace_root, ns_fds, … }` in,
  `RunResult { exit_code, payload }` out (`runner/protocol.rs`). The file body
  reuses `workspace_root` + `ns_fds` already in the request and needs **no PTY and
  no transcript**, so its launch is the non-interactive `mount_overlay` shape, not
  the shell shape.

New pieces, each a sibling of an existing one — note the launch path is the
**`mount_overlay` peer**, driven by the workspace `NamespaceRuntime`, not the
command engine (`WorkspaceSessionService` holds `Arc<WorkspaceRuntimeService>`, not
the exec engine):

```text
namespace-process   runner/setns/file_op.rs   setns_user_mnt then ReadWindow/ReadFile/Write at workspace_root/rel   (peer of setns_overlay_mount)
namespace-process   runner::run_file_op        entry mirroring runner::run
namespace-execution engine file-op launch      runner-mode launch with caller-supplied RunnerPlacement
sandbox-daemon      runner/file_op.rs           --file-op body + dispatch in runner/mod.rs             (peer of runner/mount_overlay.rs)
workspace runtime   NamespaceRuntime file-op    launch the runner for a resolved entry                (peer of its mount_overlay)
workspace-session   run_file_op(&handler, op)   resolve entry, delegate to the workspace runtime      (peer of resolve_session / capture)
```

The runner executes **after `setns`**, so whiteouts, opaque dirs, copy-up, and
cache coherence are the mounted overlay's job, not reimplemented in the operation
crate. The file-type policy is explicit and shared with sessionless reads:
regular files are supported; absent paths return `existed=false`; directories,
symlinks, symlink parent components, and other non-regular files are invalid
request errors. The runner must use fd-relative path walking under
`workspace_root` with no-follow parent opens, so `workspace_root/rel` never
follows a symlink out of the workspace. The `file` domain calls exactly one method
— `workspace_session.run_file_op(...)` — and never learns `setns` or overlay
detail (boundary law).

`ReadWindow` streams UTF-8 text, drops a leading BOM, normalizes `\r\n` / `\r` to
`\n`, caps selected output bytes, and does **not** reject a file merely because
the whole file is larger than the output cap. If the selected window exceeds
`MAX_OUTPUT_BYTES`, return `OutputTooLarge`; do not return partial byte-truncated
content.

`ReadFile` is for `edit`: it checks metadata before loading bytes; a regular file
larger than `max_bytes` returns `FileTooLarge` with the file size and limit.

`edit` issues one `ReadFile` and one `Write` through this path with `apply_edits`
in between. That is best-effort last-writer-wins under a concurrent in-session
writer; the final write is atomic, but there is no session-level OCC.

`Write` must still be atomic inside the namespace: inspect the existing path
without following symlinks, reject directories/symlinks/non-regular files, create
missing parent directories through the no-follow parent walk, write a
same-directory temp file, fsync it, preserve the existing mode when updating a
regular file, then `rename` over the target. The result's `existed` flag is the
pre-write regular-file existence.

The `file/service/namespace.rs` helper maps the runner's `existed=false` reads
and `FileRunnerError` values into the same `FileOperationError` variants used by
the sessionless backend. The `FileService` impls do not parse daemon stderr or
infer file type from generic I/O failures.

### Pseudo code — read / write / edit

```text
resolve_session_path(workspace_session, path, id):
    handler = workspace_session.resolve_session(id)          # Err NotFound -> WorkspaceSessionNotFound
    rel     = resolve_layer_path(handler.handle.workspace_root, path)
    return (rel, handler)

resolve_layer_target(layerstack, path):
    workspace_root = layerstack.workspace_root()
    rel = resolve_layer_path(workspace_root, path)
    return rel
```

Read:

```text
read(input):
    (offset, limit) = validate_read_window(input.offset, input.limit)  # limit 1..=2000

    if input.workspace_session_id:
        (rel, handler) = resolve_session_path(
            workspace_session, input.path, input.workspace_session_id)
        read = workspace_session.run_file_op(&handler,
            ReadWindow { rel: rel.clone(), offset, limit, output_cap: MAX_OUTPUT_BYTES })
        if read is Absent: return Err(NotFound(rel))
        if read is NotRegular(kind): return Err(NotRegular{rel, kind})
        if read is NotUtf8: return Err(NotUtf8(rel))
        if read is OutputTooLarge(limit): return Err(OutputTooLarge{rel, limit})
    else:
        rel = resolve_layer_target(layerstack, input.path)
        read = layerstack.read_current_window(rel, offset, limit, MAX_OUTPUT_BYTES)
        if read is Absent: return Err(NotFound(rel))
        if read is NotRegular(kind): return Err(NotRegular{rel, kind})
        if read is NotUtf8: return Err(NotUtf8(rel))
        if read is OutputTooLarge(limit): return Err(OutputTooLarge{rel, limit})

    return read.with_path(rel)
```

Write:

```text
write(input):
    if input.workspace_session_id:
        (rel, handler) = resolve_session_path(
            workspace_session, input.path, input.workspace_session_id)
        result = workspace_session.run_file_op(&handler,
            Write { rel: rel.clone(), content: bytes(input.content) })
        # NO publish; attributed to workspace_session:<id> later, on session capture
        return { type: result.existed ? "update" : "create",
                 path: rel, bytes_written: result.bytes_written }

    rel = resolve_layer_target(layerstack, input.path)
    owner = "operation:" + input.request_id
    result = layerstack.amend_path(rel, owner, 0, read =>    # 0: classify only, no byte load; atomic under the lock
        if read is NotRegular(kind): return Err(NotRegular{rel, kind})
        bytes(input.content))                                # last-writer-wins; no retry

    return { type: result.existed_before ? "update" : "create",
             path: rel, bytes_written: result.bytes_written }
```

Edit:

```text
edit(input):
    if input.edits is empty: return Err(NoEdits)

    if input.workspace_session_id:
        (rel, handler) = resolve_session_path(
            workspace_session, input.path, input.workspace_session_id)
        current = workspace_session.run_file_op(&handler,
            ReadFile { rel: rel.clone(), max_bytes: MAX_EDIT_BYTES })
        if !current.existed: return Err(NotFound(rel))
        text = utf8(current.bytes) else Err(NotUtf8(rel))
        (edited, replacements) = apply_edits(text, input.edits, rel)
        result = workspace_session.run_file_op(&handler,
            Write { rel: rel.clone(), content: bytes(edited) })
        # NO publish
        return { type: "edit", path: rel,
                 edits_applied: len(input.edits), replacements,
                 bytes_written: result.bytes_written }

    rel = resolve_layer_target(layerstack, input.path)
    owner = "operation:" + input.request_id
    replacements = 0
    result = layerstack.amend_path(rel, owner, MAX_EDIT_BYTES, read =>   # atomic under the writer lock
        if read is Absent: return Err(NotFound(rel))
        if read is NotRegular(kind): return Err(NotRegular{rel, kind})
        if read is TooLarge(size, limit): return Err(FileTooLarge{rel, size, limit})
        text = utf8(read.bytes) else Err(NotUtf8(rel))
        (edited, count) = apply_edits(text, input.edits, rel)
        replacements = count
        bytes(edited))                                       # re-applied to current head; no retry

    return { type: "edit", path: rel,
             edits_applied: len(input.edits), replacements,
             bytes_written: result.bytes_written }
```

Shared edit rules:

```text
apply_edits(text, edits, path):
    cur = normalize_line_endings(text)
    original = cur
    for e in edits:
        old = normalize_line_endings(e.old_string)
        new = normalize_line_endings(e.new_string)
        if old == "":  return Err(EditNotFound{path})
        if old == new: return Err(NoChanges{path})
        count = occurrences(cur, old)
        if count == 0:                      return Err(EditNotFound{path, snippet(old)})
        if count > 1 and not e.replace_all: return Err(EditNotUnique{path, count, snippet})
        if e.replace_all: cur = replace_all(cur, old, new); replacements += count
        else:             cur = replace_first(cur, old, new); replacements += 1
    if cur == original: return Err(NoChanges{path})
    return (restore_original_line_endings(cur, text), replacements)
```

## Layerstack Access (no workspace_session_id)

Two `LayerStackService` primitives keep raw `LayerStack` and its writer lock
inside the layerstack service (boundary law). Read results preserve file-type
classification; `Option<Vec<u8>>` is not enough because the projection can
distinguish absent paths, regular files, symlinks, directories, non-UTF-8 text,
oversized selected output, and oversized full-file edit inputs.

```rust
// layerstack/service/impls/{read,amend}.rs
impl LayerStackService {
    // Sessionless read: classified text-window read of the active head (shared lock).
    pub fn read_current_window(
        &self,
        rel: &LayerPath,
        offset: Option<u64>,
        limit: usize,
        output_cap: usize,
    ) -> Result<ManifestReadWindow, LayerStackServiceError>;

    // Sessionless write/edit: atomic read-modify-write of the active head under
    // ONE exclusive writer lock — read current content, run the caller's pure
    // transform, publish the resulting Write, record blame — no caller-visible
    // base, no retry. See "amend_path" below.
    pub fn amend_path<E>(
        &self,
        rel: &LayerPath,
        owner: &str,
        max_bytes: usize,
        transform: impl FnOnce(ManifestFileRead) -> Result<Vec<u8>, E>,
    ) -> Result<AmendOutcome, AmendError<E>>;

    pub fn workspace_root(&self) -> Result<PathBuf, LayerStackServiceError>;
}

pub struct AmendOutcome { pub existed_before: bool, pub bytes_written: usize }
pub enum AmendError<E> { Transform(E), LayerStack(LayerStackServiceError) }

pub enum ManifestReadWindow {
    Absent,
    Text {
        content: String,
        start_line: u64,
        num_lines: usize,
        total_lines: u64,
        bytes_read: usize,
        total_bytes: u64,
        next_offset: Option<u64>,
        truncated: bool,
    },
    NotRegular { kind: FileEntryKind },
    NotUtf8,
    OutputTooLarge { limit: usize },
}

pub enum ManifestFileRead {
    Absent,
    File { bytes: Vec<u8>, total_bytes: u64 },  // max_bytes == 0 => classify only, bytes empty, never TooLarge
    NotRegular { kind: FileEntryKind },
    TooLarge { size: u64, limit: usize },
}

pub enum FileEntryKind { Directory, Symlink, Other }
```

- `read_current_window` reads one path from the active manifest via `MergedView`
  under a shared lock and streams UTF-8 text through the same windowing helper as
  the namespace runner. Symlink/directory/other → `NotRegular`; the full file may
  exceed the response cap as long as the selected window does not.
- `amend_path` is the whole sessionless write/edit path (next section). It derives
  `existed_before` from the classified read it performs under the lock, so `write`
  gets create-vs-update with `max_bytes == 0` (classify only — no large existing
  file is loaded just to overwrite it).
- `workspace_root` reads the existing layerstack workspace binding; it adds no
  field to `FileService`.

The underlying layerstack crate exposes classified projection read-window and
bounded full-file read primitives publicly (do not infer type from
`read_bytes_limited`) and gains `LayerStack::amend_path`, which holds
`writer_lock.exclusive()` across read → transform → resolve → commit.

### amend_path (write + edit) — atomic, no retry

Sessionless write/edit are a read-modify-write of the active head. The layerstack
already serializes publishes under a process-wide, cross-process **exclusive
writer lock** and auto-merges a changeset against head at commit time
(`resolve_publish_changes`). `amend_path` does the read-modify-write **inside that
same lock**, so head cannot move between the read and the commit:

```text
LayerStack::amend_path(rel, max_bytes, transform):   # layerstack crate
    guard  = writer_lock.exclusive()                 # one hold, process- and cross-process
    active = read_active_manifest()                  # head, under the lock
    read   = classified_read(active, rel, max_bytes) # ManifestFileRead
    new    = transform(read)?                         # caller's pure text logic; ? aborts, no commit
    resolve + commit [Write{rel, new}] with base = active   # base == head
    return { existed_before: read is File, bytes_written: len(new) }
# LayerStackService::amend_path wraps this and runs record_layer_publish(owner, origin) after commit
```

Because `base == head` (both are the manifest read under the lock),
`resolve_source_conflicts` sees the content fingerprint match, the three-way merge
never runs, and `SourceConflict` / `ManifestConflict` **cannot** occur — so there
is nothing to retry. The removed `publish_on_head` loop, its `~8` bound, and the
retry classification are all gone. Blame is preserved: the publish still diffs the
new bytes against head, attributing changed lines to this `owner` and unchanged
lines to their prior owner.

Per-op transforms (pure, in the file domain):

- `write`: reject `NotRegular`, else return `bytes(content)` — last-writer-wins,
  now atomic, so no clobber window. `existed_before` comes from `AmendOutcome`.
- `edit`: `Absent`→`NotFound`, reject `NotRegular`/`TooLarge`, UTF-8 the bytes,
  `apply_edits`, return the edited bytes. It runs against the *current* head
  content by construction — the old "re-read and re-apply on conflict" is implicit.

The transform runs under the exclusive lock, so it must be pure and fast and must
**not** call back into the layerstack (the lock is not re-entrant); `apply_edits`
is in-memory string work, so this holds. Session capture keeps using
`publish_changes` — it merges a session's divergent diff against a *pinned* base
(a genuine three-way merge, where `SourceConflict` is meaningful). `amend_path` is
strictly the sessionless RMW-of-current-head path.

## Owner Attribution & Auditability

The owner string is the existing convention documented on
`PublishChangesRequest`:

```text
sessionless write/edit -> owner = "operation:<request_id>"   (request_id from Request)
session write/edit      -> no publish now; attributed to
                           "workspace_session:<id>" when the session is captured
```

`request_id` already exists on `sandbox_protocol::Request`, so no id generation
is required. Sessionless publishes flow through `publish_changes ->
record_layer_publish`, so `file_blame` reflects the new owner with no extra work.

## Error Model

Add `FileOperationError` as a peer to the blame-only `FileError` (keep blame's
error narrow; do not overload it).

```rust
pub enum FileOperationError {
    NotFound(String),
    InvalidPath(String),
    NotUtf8(String),
    NotRegular { path: String, kind: FileEntryKind },
    FileTooLarge { path: String, size: u64, limit: usize },
    OutputTooLarge { path: String, limit: usize },
    EditNotFound { path: String, snippet: String },
    EditNotUnique { path: String, count: usize, snippet: String },
    NoEdits,
    NoChanges(String),
    WorkspaceSessionNotFound(String),
    WorkspaceSession(String),
    LayerStack(#[from] LayerStackServiceError),
    Io { path: String, source: std::io::Error },
}
```

Dispatch → `Response` mapping:

| Variant | kind |
|---|---|
| `NotFound`, `WorkspaceSessionNotFound` | `not_found` (+ details) |
| `InvalidPath`, `NotUtf8`, `NotRegular`, `FileTooLarge`, `OutputTooLarge`, `EditNotFound`, `EditNotUnique`, `NoEdits`, `NoChanges` | `invalid_request` |
| `WorkspaceSession`, `LayerStack`, `Io` | `operation_failed` |

`edit` mirrors local-os semantics: every `old_string` must be found; it must be
unique unless `replace_all` is set; empty edit arrays and no-op edits are
rejected; edits apply in array order with local-os line-ending normalization.
Classified edit/full-file read `TooLarge` maps to
`FileOperationError::FileTooLarge`, not to the catch-all `LayerStack` variant.
Read-window output cap failures map to `OutputTooLarge`; they are not evidence
that the source file itself is too large. `truncated` is only line-window
pagination state, not byte-cap truncation.

## Operation catalog, CLI projection, and runtime registry

The merged semantic catalog owns the three `OperationSpec` values and the
expanded `file` family in
`crates/sandbox-operations/catalog/src/runtime/file.rs`. The CLI argument
spellings and usage live in
`crates/sandbox-cli/src/projection/runtime.rs`. Runtime dispatchers are wired
through
`crates/sandbox-runtime/operation/src/operations/registry/file_operations.rs`.

```text
sandbox-runtime-cli --sandbox-id ID file_read  --path FILE [--offset N] [--limit N] [--workspace-session-id ID]
sandbox-runtime-cli --sandbox-id ID file_write --path FILE --content TEXT [--workspace-session-id ID]
sandbox-runtime-cli --sandbox-id ID file_edit  --path FILE --edits JSON   [--workspace-session-id ID]
```

The protocol/request field is `path`; `--path` maps directly to it.

The merged contract declares `edits` as `ArgKind::JsonArray`; the registry
accepts **both** a real JSON array (the programmatic agent path,
`request.args.edits`) and a JSON string (CLI ergonomics). `request_id` is read
from `request.request_id`; `workspace_session_id` is parsed exactly as
`exec_command` does (empty ⇒ `None`).

## File-by-File Change Plan

> **Historical landed change plan (operation-layout exempt, 2026-07-11):**
> Service and namespace paths below record the implementation footprint at
> landing time. Operation ownership is translated to the current merged
> catalog/projection/registry layout.

```text
EDIT sandbox-runtime-layerstack              LayerStack::amend_path (exclusive-lock read→transform→publish); expose classified projection read-window + bounded file read
NEW  layerstack/service/impls/{read,amend}.rs read_current_window() + amend_path() (atomic RMW under writer lock; blame after commit)
EDIT layerstack/service/core.rs              expose workspace_root() via binding
EDIT layerstack/service/impls/mod.rs         + mod read;

EDIT file/service/core.rs                    unchanged constructor/signature
EDIT file/error.rs                           + FileOperationError
NEW  file/service/dto.rs                     Read/Write/Edit In & Out, EditOp
NEW  file/service/support.rs                 resolve_layer_path + text windowing
NEW  file/service/namespace.rs               call WorkspaceSessionService::run_file_op for session ops; decode ReadFile bytes_b64
NEW  file/service/impls/read.rs              impl FileService::read
NEW  file/service/impls/write.rs             impl FileService::write
NEW  file/service/impls/edit.rs              impl FileService::edit
EDIT file/service/impls/mod.rs               + mod read/write/edit;
EDIT file/service.rs                         + mod dto/support/namespace; re-export DTOs
EDIT file/mod.rs                             re-export FileOperationError + DTOs

# unified ns-runner: explicit mode flags; no no-flag shell default
NEW  namespace-process  runner/setns/file_op.rs   setns_user_mnt + ReadWindow/ReadFile + atomic no-follow Write at workspace_root/rel
EDIT sandbox-runtime/Cargo.toml                    add base64 workspace dep for decoding ReadFile payloads
EDIT namespace-process  runner/{mod,setns}.rs     + run_file_op entry (peer of run_setns); add base64 workspace dep for ReadFile payloads
NEW  namespace-execution engine file-op launch    runner-mode launch with caller-supplied RunnerPlacement
EDIT namespace-execution launcher                 spawn_pty passes --shell; add RunnerPlacement + spawn_file_op; shared runner-mode helper drains result_fd concurrently and caps result bytes; no RunnerWait enum
NEW  sandbox-daemon      runner/file_op.rs         --file-op body (peer of runner/mount_overlay.rs)
EDIT sandbox-daemon      runner/mod.rs             Run -> Shell; add --shell/--file-op arms; require exactly one mode flag
EDIT workspace          NamespaceRuntime + WorkspaceRuntimeService  file-op launch (peer of mount_overlay)
NEW  workspace_session  service/impls/run_file_op.rs  resolve entry, delegate to workspace runtime
EDIT services.rs                             no FileService::open signature change
EDIT crates/sandbox-operations/catalog/src/runtime/file.rs                  FILE_READ/WRITE/EDIT semantic specs + routes
EDIT crates/sandbox-cli/src/projection/runtime.rs                           FILE_READ/WRITE/EDIT CLI projection
EDIT crates/sandbox-runtime/operation/src/operations/registry/file_operations.rs dispatch + register

NEW  crates/sandbox-runtime/operation/tests/file_operations.rs  four-quadrant coverage (see Verification)
```

`FileService::open` does not grow an argument.

## Verification

Build and unit checks:

```sh
cargo build
cargo test -p sandbox-runtime
cargo clippy --all-targets
cargo fmt
```

New integration test
`crates/sandbox-runtime/operation/tests/file_operations.rs` must cover:

```text
read  sessionless      -> content from the snapshot; offset/limit windowing; bytes/next/truncated fields
read  session          -> namespace write is visible on a subsequent read
read  missing          -> NotFound on both backends, never empty content
read  text normalize   -> BOM and CRLF/CR normalize before windowing
read  large file       -> returns the requested line window without loading/rejecting the whole file
read  output cap       -> selected output over MAX_OUTPUT_BYTES returns OutputTooLarge; truncated only means next_offset
read  validation       -> limit 0 and limit >2000 are invalid_request
write sessionless      -> type=create then type=update; file_blame owner = operation:<id>
write session          -> lands in the session overlay; NOT visible via a sessionless snapshot read
write atomic session   -> parent dirs created, regular-file mode preserved on update, no partial direct write
edit  sessionless      -> replacements; EditNotFound / EditNotUnique errors
edit  session          -> read-modify-write against the live overlay; concurrent in-session writers are last-writer-wins
edit  session large    -> MAX_EDIT_BYTES ReadFile result drains without deadlock; encoded result over MAX_RUNNER_RESULT_BYTES fails
file  not-regular      -> symlink, directory, and special file rejected on both backends
path  accept-both      -> absolute-under-workspace-root and repo-relative resolve equal
path  reject escape    -> absolute outside root, `..`, empty, and NUL paths rejected
path  parent symlink   -> symlink parent components are rejected on both backends
amend atomicity        -> concurrent sessionless writes/edits to one path serialize; no lost update; no retry
session concurrency    -> file_write during a running in-session command is visible in that namespace
runner placement       -> exec_command and session file ops pass session cgroup.procs; overlay mount passes none
```

Live sandbox checks are smoke-only for this pass. Use `sandbox-cli`, rebuild the
gateway first, and run only the 15 cases in [[test-case#Live Smoke Checklist]]
-- five each for `file_read`, `file_write`, and `file_edit`.

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
```

Live smoke pass criteria:

```text
all 15 smoke cases pass
both sessionless layerstack and session namespace backends are exercised
the transcript is saved with the milestone evidence
```

## Safety Rules

- `read` is a pure read; it never mounts, never publishes, never mutates.
- Session write/edit run inside the target session namespace; never host-write
  `upperdir`, never touch a lower layer, never another session, never the shared
  base.
- Session write/edit are unavailable until the namespace file-op runner exists;
  host-side `upperdir` mutation is not an acceptable fallback.
- Sessionless write/edit go through `amend_path` only — atomic read-modify-write
  under the layerstack exclusive writer lock, with publish + blame inside — so
  every published line is attributed and auditable by `file_blame`.
- Path resolution rejects `..` and absolute paths outside the workspace root
  (`LayerPath` invariants); namespace runner path walking also rejects symlink
  parent components with fd-relative no-follow opens, so no path escapes the
  repo tree.
- `FileService` stays `&self`; no new locks. Layerstack concurrency is handled by
  its existing exclusive writer lock; sessionless write/edit are atomic under that
  lock (`amend_path`), with no retry loop in the file domain.
- The blame/`record_layer_publish` edge is untouched; no existing publish
  behavior changes.

## Non-Goals

- No host-side overlay merge/write implementation in the file service.
- No `delete`/`move`/`stat`/`ls` operations in this pass — only `read`, `write`,
  `edit`.
- No new field on `SandboxRuntimeOperations` and no new top-level service.
- No change to the local-os tools; this is the sandbox side of the symmetry.
- No binary-file handling beyond a UTF-8 guard (`NotUtf8`); no image/PDF policy.
- No symlink following in this pass. Symlinks are rejected consistently on both
  backends until a workspace-confined symlink-following policy is designed.

## Open Risks

- **Namespace file runner scope.** Keep it to `ReadWindow`, `ReadFile`, and
  `Write` only. Do not add delete/move/stat/list until those operations exist.
- **Whiteout fidelity.** Session ops delegate to the mounted overlay namespace;
  sessionless ops delegate to the layerstack projection. Do not duplicate
  whiteout logic in the operation crate. The projection must expose classified
  path results so directories/symlinks are rejected instead of being misread.
- **Amend lock-hold time.** `amend_path` holds the layerstack exclusive writer
  lock across read → transform → commit, so concurrent sessionless writers (and
  session captures) serialize behind it. The transform is in-memory string work
  (µs), comparable to the merge path that already reads under the lock, so this is
  acceptable for turn-based use; there is no retry and no lost update.
- **Session edit concurrency.** A session edit is not an atomic compare-and-swap
  against arbitrary concurrent processes inside the same live namespace. It is a
  read-file + atomic-write pair, so the final write is last-writer-wins if another
  in-session writer changes the file between those steps. This is acceptable for
  the first file-op pass because shell commands already have normal filesystem
  race semantics; do not document it as OCC.
