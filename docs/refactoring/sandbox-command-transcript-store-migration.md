# Sandbox Command Transcript Store Migration

## Goal

Move command transcript storage out of workspace scratch and into the runtime
command store:

```text
/eos/commands/<command_session_id>/transcript.log
```

The command transcript is a functional command-output artifact. It must not live
under:

```text
/eos/scratch/workspace/
```

Workspace scratch is destroyed with the workspace session. Command transcripts
must remain readable after workspace destroy until the completed-command
retention policy expires.

Use `/eos/commands`, not `/eos/commnd`.

Configuration ownership must match the runtime ownership:

- Command transcript storage and completed-command retention are runtime
  settings under `runtime.commands`.
- No command transcript storage setting remains under `daemon.commands`.
- Daemon server/process settings stay under `daemon.server`; this migration only
  moves command runtime storage and retention.

## Non-Goals

- Do not make command transcripts durable history across daemon restarts.
- Do not add metadata files next to `transcript.log`.
- Do not reintroduce `command-request.json`, `runner-result.json`, `final.json`,
  or process metadata files.
- Do not store command transcripts under workspace scratch.
- Do not keep compatibility aliases for the old daemon command scratch config.

## Current State

Workspace scratch:

```text
runtime.workspace.scratch_root = /eos/scratch/workspace
/eos/scratch/workspace/sessions/<workspace_session_id>/
  upper/
  work/
```

That directory is owned by the workspace session lifecycle. Destroying the
workspace session recursively removes the session run directory.

Command transcripts currently use the daemon command scratch config:

```text
daemon.commands.scratch_root = /eos/scratch/commands
/eos/scratch/commands/<command_session_id>/transcript.log
```

That name is now wrong: command transcripts are retained command output, not
workspace scratch.

## Storage Semantics

`/eos` is the runtime-managed root. Paths under it are internal runtime storage,
not user workspace content.

`/eos/scratch` means ephemeral runtime working storage. A subsystem may use it
for writable directories, mount work dirs, transient state, and lifecycle-owned
runtime bookkeeping. It should not be used for retained command output.

Current scratch users:

- `/eos/scratch/workspace`: workspace session scratch. It contains session run
  dirs such as `sessions/<workspace_session_id>/upper` and `work`, and
  workspace manager state.
- `/eos/scratch/overlay`: overlay writable root used by overlay helpers.
- `/eos/scratch/commands`: legacy command transcript location. This migration
  removes it.

`/eos/commands` is still runtime-owned internal storage, but it is intentionally
not under `/eos/scratch`: completed command transcripts must remain readable
after workspace destroy until `completed_retention_s` expires.

## Target Config

Move command transcript configuration under the runtime config:

```yaml
runtime:
  commands:
    scratch_root: /eos/commands
    completed_retention_s: 1800
  workspace:
    scratch_root: /eos/scratch/workspace
```

Validation rules:

- `runtime.commands.scratch_root` must be absolute.
- `runtime.commands.scratch_root` must not be the filesystem root.
- `runtime.commands.scratch_root` must not be under
  `runtime.workspace.scratch_root`.
- `runtime.commands.completed_retention_s` must be greater than zero.
- No alias or fallback for `daemon.commands.scratch_root`.

The runtime command config should mirror those names:

```rust
pub struct RuntimeConfig {
    pub workspace: WorkspaceConfig,
    pub commands: CommandConfig,
}

pub struct CommandRuntimeConfig {
    pub scratch_root: PathBuf,
    pub completed_retention: Duration,
}
```

## Target Layout

```text
/eos/commands/
  <command_session_id>/
    transcript.log
```

Path rules:

- The command directory is derived only from the internally allocated
  `command_session_id`.
- Lookup paths must come from the command store record, not from user request
  path construction.
- Reject or avoid command session ids containing path separators.
- Remove the entire command directory when the completed command expires.

## Spawn Path Impact

`CommandProcessSpawn` keeps the same ownership shape:

```rust
pub struct CommandProcessSpawn {
    pub workspace_entry: WorkspaceEntry,
    pub transcript_path: PathBuf,
    cgroup: Option<CommandCgroup>,
    cgroup_monitor_config: CgroupMonitorConfig,
}
```

Only the value of `transcript_path` changes. `CommandProcessSpawn::prepare`
continues to derive it from the command runtime config and the internally
allocated command session id:

```text
runtime.commands.scratch_root / command_session_id / transcript.log
```

With the default config, that resolves to:

```text
/eos/commands/<command_session_id>/transcript.log
```

Do not move `transcript_path` into `WorkspaceEntry`. `WorkspaceEntry` remains
workspace launch material: workspace root, layer paths, upper/work dirs,
namespace FDs, and optional cgroup path. Command transcript storage must stay
owned by the command runtime store so that workspace destroy does not remove
retained command output.

`CommandProcess::transcript_path()` should continue returning the resolved path
from `CommandProcessSpawn`. Active and completed command records should copy
that resolved path into `CommandTranscriptStore` and
`RetainedCommandTranscript`; later reads must use the retained path rather than
reconstructing it from a workspace path or request payload.

## Lifecycle Policy

Running commands:

- Never evict running commands.
- Keep the transcript writable while the command process is active.
- Workspace destroy should not be responsible for deleting command transcripts.

Completed commands:

- On terminal completion, retain the command record and transcript.
- Set an internal expiration deadline of `completed_retention_s` after
  completion.
- `poll_command` and `read_command_lines` can read the completed command until
  expiration.
- After expiration, remove the completed command record and delete:

```text
/eos/commands/<command_session_id>/
```

Expired commands:

- `poll_command`, `read_command_lines`, `write_command_stdin`, and
  `cancel_command` should return command-not-found once the command has expired
  and has been pruned.

Daemon restart:

- Command records remain in memory only.
- On daemon startup, stale directories under `runtime.commands.scratch_root` can
  be deleted because there is no metadata file to restore completed command
  status or exit code.
- Restart durability requires a separate metadata/index design and is outside
  this migration.

## Implementation Plan

1. Config move

- Move `sandbox-runtime-config` command config from `daemon.commands` to
  `runtime.commands`.
- Keep the command root key named `scratch_root`.
- Add `runtime.commands.completed_retention_s`.
- Remove `DaemonConfig.commands` if no daemon-only command fields remain.
- Update `config/prd.yml`.
- Update config tests.
- Remove all old `daemon.commands.scratch_root` references.

2. Runtime config plumbing

- Change `sandbox-daemon/src/serve.rs` to pass:

```rust
CommandRuntimeConfig {
    scratch_root,
    completed_retention,
}
```

- Change `sandbox-runtime/operation/src/internal/services.rs` accordingly.
- Change `sandbox-runtime-command::CommandConfig` accordingly.

3. Command artifact path

- Change `CommandProcessSpawn::prepare` to create:

```text
config.scratch_root / command_session_id / transcript.log
```

- Keep the `CommandProcessSpawn` fields unchanged.
- Ensure `config.scratch_root` is sourced from `runtime.commands.scratch_root`.
- Ensure the returned `transcript_path` is exactly:

```text
/eos/commands/<command_session_id>/transcript.log
```

- Rename error context from command scratch/artifact scratch language to command
  store language where appropriate.
- Preserve start-failure cleanup of the command directory.
- Do not clean successful command directories from `CommandProcess`; retention
  pruning owns successful command cleanup.

4. Command store retention

- Add retention policy to `CommandProcessStore` or to the command operation
  service.
- Keep active commands outside pruning.
- Store an internal expiration deadline for completed command records. Do not
  expose this as transcript timeline data.
- Add a `prune_expired_completed(now)` path and call it at command operation
  boundaries, and optionally from a daemon maintenance loop.
- Delete expired command directories with best-effort `remove_dir_all`.

5. Workspace lifecycle separation

- Keep workspace scratch at:

```text
/eos/scratch/workspace/sessions/<workspace_session_id>/
```

- Keep command transcripts outside that tree.
- Workspace destroy should remove only the workspace session run directory. It
  should not remove `/eos/commands/<command_session_id>`.
- Command records must still keep `workspace_session_id` for ownership and
  mismatch checks.

6. Documentation cleanup

- Update runtime docs to use command store terminology.
- Remove stale mentions of `/eos/scratch/commands` and
  `daemon.commands.scratch_root`.
- Keep `transcript.log` documented as retained command output, not logging.

## Test Plan

Config:

- `runtime.commands.scratch_root` must be absolute.
- `runtime.commands.scratch_root` must not be `/`.
- `runtime.commands.scratch_root` must not be under
  `runtime.workspace.scratch_root`.
- `runtime.commands.completed_retention_s` must be greater than zero.
- Old `daemon.commands.scratch_root` is rejected.
- `daemon.commands` is rejected once no daemon-only command fields remain.

Command process:

- `CommandProcessSpawn::prepare("cmd_7", ...)` creates:

```text
<scratch_root>/cmd_7/transcript.log
```

- With the default config, `CommandProcessSpawn::prepare("cmd_7", ...)`
  resolves:

```text
/eos/commands/cmd_7/transcript.log
```

- `CommandProcessSpawn::workspace_entry` is unchanged and does not gain a
  transcript path.
- `CommandProcess::transcript_path()` returns the resolved path from
  `CommandProcessSpawn`.
- Active command records copy the resolved path into `CommandTranscriptStore`.
- Completed command records copy the same resolved path into
  `RetainedCommandTranscript`.
- Start-failure cleanup removes `<scratch_root>/cmd_7`.

Retention:

- Completed command remains readable before retention expiry.
- Expired completed command is pruned from memory.
- Expired completed command directory is deleted from disk.
- Active command is not pruned even if retention time has elapsed.
- After pruning, `poll_command` and `read_command_lines` return command-not-found.

Workspace lifecycle:

- Destroying a workspace session removes:

```text
/eos/scratch/workspace/sessions/<workspace_session_id>/
```

- Destroying a workspace session does not remove:

```text
/eos/commands/<command_session_id>/
```

Stale scans:

```sh
rg -n "daemon\\.commands\\.scratch_root|/eos/scratch/commands" \
  crates/sandbox-runtime/command crates/sandbox-runtime/operation \
  crates/sandbox-daemon crates/sandbox-runtime/config config docs/refactoring
```

Expected command scratch hits should be `runtime.commands.scratch_root` only.

Focused verification:

```sh
cargo fmt --check
cargo test -p sandbox-runtime-config --test unit -- --nocapture
cargo test -p sandbox-runtime-command --test unit process -- --nocapture
cargo test -p sandbox-runtime --test exec_command -- --nocapture
cargo test -p sandbox-runtime --test command_transcript_rows -- --nocapture
cargo check -p sandbox-daemon --tests
cargo check -p sandbox-runtime --tests
git diff --check
```

## Acceptance Criteria

- No command transcript is stored under `/eos/scratch/workspace`.
- The default command transcript path is:

```text
/eos/commands/<command_session_id>/transcript.log
```

- `daemon.commands.scratch_root` no longer exists.
- `runtime.commands.scratch_root` and `runtime.commands.completed_retention_s`
  are required config fields.
- Completed command transcripts are retained until `completed_retention_s`
  expires.
- Expired completed command records and transcript directories are removed.
- Workspace destroy does not delete retained command transcripts.
- Running commands are never evicted by completed-command retention.
- `CommandProcessSpawn::transcript_path` remains the resolved command transcript
  path, now rooted at `runtime.commands.scratch_root`.
