# Phase 04 - Workspace-Replaced Shell Execution

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Route guarded shell requests to `command-exec-server` first. The server prepares
a leased layer-stack workspace snapshot, replaces `/testbed` inside a private
mount namespace, runs the command with the rest of the sandbox filesystem still
visible, captures workspace upperdir changes, and submits those changes through
the OCC client boundary.

Implementation scope:

```text
add command-exec-server shell handler
add workspace replacement mount implementation
enforce cwd/env after workspace replacement
capture only assigned-workspace upperdir changes
submit captured changes through occ.client.OCCClient
release layer-stack lease after OCC result
run cache-enabled versus cache-disabled shell load comparison
```

Out of scope:

```text
no full-root capture
no Git/gitignore policy in command-exec
no direct write/edit API routing through command-exec
no production fallback that mutates real /testbed
```

Exit condition:

```text
sandbox.api.tool.shell enters command-exec-server first, the command sees
/testbed from a leased manifest while /bin and /usr remain usable, and all
/testbed writes are submitted to OCC as workspace-relative changes.
```

## 2. Main Data Objects

```text
CommandExecRequest
  request_id
  workspace_ref
  command
  cwd
  env
  timeout_seconds

WorkspaceReplacementMountSpec
  workspace_root
  lowerdir
  upperdir
  workdir
  manifest_version
  lease_id

CommandExecResult
  exit_code
  stdout
  stderr
  workspace_capture
  occ_result
  timings

WorkspaceUpperdirChange
  path
  change_kind
  bytes or tombstone
  mode/symlink metadata when supported
```

## 3. File/Folder Structure Change

Target additions and updates:

```text
backend/src/sandbox/runtime/
+-- command_exec_server.py

backend/src/sandbox/command_exec/
+-- __init__.py
+-- workspace_mount.py
+-- env.py
+-- request.py
+-- result.py
+-- capture/
|   +-- upperdir.py
|   +-- changeset.py

backend/src/sandbox/runtime/overlay_shell/
|-- capture_to_changeset.py
|-- result_envelope.py

backend/tests/unit_test/test_sandbox/test_command_exec/
+-- test_workspace_mount.py
+-- test_env_policy.py
+-- test_capture_to_occ_client.py
```

## 4. Workflow Demonstration

```text
host sandbox.api.tool.shell("pytest -q", cwd="/testbed")
  -> thin client routes api.shell to command-exec-server
  -> command-exec-server asks layer-stack for prepared snapshot
  -> layer-stack returns lease_id, manifest N, lowerdir
  -> command-exec allocates upperdir and overlayfs workdir
  -> command-exec creates private mount namespace
  -> /testbed is overmounted:
       lowerdir = leased manifest N lowerdir
       upperdir = per-command workspace upperdir
       workdir  = overlayfs internal workdir
  -> command runs with /bin, /usr, /tmp, /root still visible
  -> command-exec captures upperdir as workspace-relative changes
  -> occ.client.OCCClient.apply_changeset(changes, snapshot=N)
  -> command-exec releases lease
  -> host receives shell result plus OCC result
```

Expected behavior examples:

```text
pwd                                      -> /testbed
echo x > /testbed/out.txt                -> captured
cd /testbed && echo x > out.txt          -> captured
echo x > /tmp/outside.txt                -> not layer-stack workspace truth
python -c 'import os; print(os.path.exists("/bin/sh"))'
                                         -> true
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `command-exec-server` | Names the execution contract instead of the overlayfs implementation detail. |
| `workspace-replaced execution environment` | Describes the full sandbox filesystem with only `/testbed` replaced. |
| `workspace replacement mount` | Names the mount at `/testbed`, not the whole command environment. |
| `upperdir` and `workdir` | Valid inside mount implementation only. |
| no `overlay-server` | Avoids making the implementation detail the service boundary. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_shell.py -q
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_shell_call_isolation.py -q
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_replaced_shell_cache_ab.py -q
```

Required assertions:

- shell sees a stable leased `/testbed` manifest for the whole command
- concurrent publish after lease does not change the running shell view
- `/testbed` writes are captured and submitted to OCC
- outside-workspace writes are not published to layer-stack
- command-exec has no Git/gitignore policy branches
- command-exec imports no concrete layer-stack manager, manifest, merged view,
  OCC service, or publish internals

## 7. Cache Decision Experiment

Phase 04 must decide whether Phase 02's persistent materialized lowerdir cache
is worth keeping for real shell execution. The decision must come from a live
Daytona-backed A/B load test, not from prepare-only microbenchmarks.

Add a focused live module:

```text
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
`-- test_workspace_replaced_shell_cache_ab.py
```

The test runs the same workspace-replaced shell workload twice:

```text
cache_enabled:
  command-exec uses prepare_workspace_snapshot with reusable materialized
  lowerdirs for the latest manifest

cache_disabled:
  command-exec still prepares a lowerdir for the workspace replacement mount,
  but it treats the lowerdir as per-command transient state and removes it when
  the command lease releases
```

The disabled path is a measurement policy, not a production fallback. It must
still route through command-exec, mount the leased `/testbed` view, capture
upperdir changes, submit through `occ.client.OCCClient`, and avoid mutating the
real `/testbed`.

Default load case:

```text
workspace:
  imported `/testbed` base plus a configurable 16 MiB tracked payload

concurrency:
  1, 5, 10, 20 independent shell calls
  barrier-launched concurrent calls, not shell batching

per-call command:
  read a stable file from `/testbed`
  read a slice of the large tracked payload
  write one unique tracked output file under `/testbed`
  write one unique outside-workspace file under `/tmp`

post-run reconciliation:
  all tracked outputs are visible through public read_file
  no `/tmp` output is published to layer-stack truth
  no shell observes a manifest published after its lease was acquired
```

Each policy writes JSONL under:

```text
.omc/results/live-e2e-phase04-shell-cache-ab-<policy>-<utc>.jsonl
```

Required fields:

```text
policy                                # cache_enabled or cache_disabled
workspace_bytes
concurrency
batch_wall_ms
per_call_wall_ms
api.shell.total_s
command_exec.prepare_snapshot_s
command_exec.mount_workspace_s
command_exec.run_command_s
command_exec.capture_upperdir_s
command_exec.occ_apply_s
command_exec.release_snapshot_s
layer_stack.snapshot_cache.hit
layer_stack.snapshot_cache.materialize_s
materialized_lowerdirs_peak
cache_bytes_peak
cache_bytes_after_release
df_kb_available_before
df_kb_available_after
success_count
conflict_count
published_workspace_paths
outside_workspace_paths_not_published
```

Comparison summary:

```text
.omc/results/live-e2e-phase04-shell-cache-ab-summary-<utc>.jsonl
```

The summary must report, for each concurrency:

```text
cache_enabled_p50_wall_ms
cache_enabled_p95_wall_ms
cache_enabled_batch_wall_ms
cache_disabled_p50_wall_ms
cache_disabled_p95_wall_ms
cache_disabled_batch_wall_ms
absolute_p95_ms_saved
relative_p95_saved_percent
absolute_batch_ms_saved
relative_batch_saved_percent
extra_cache_bytes_peak
keep_cache_recommendation
```

Decision bar:

```text
Keep the persistent cache only if the cache-enabled run is meaningfully better
on the shell workload:

  p95 wall or batch wall improves by at least 20 percent
  OR p95 wall or batch wall improves by at least 250 ms

and correctness results are identical between policies.

If the observed win is only prepare-scale, for example roughly 60 ms total wall
time, remove or disable the persistent cache and keep transient lowerdir
construction only.
```
