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
```

Required assertions:

- shell sees a stable leased `/testbed` manifest for the whole command
- concurrent publish after lease does not change the running shell view
- `/testbed` writes are captured and submitted to OCC
- outside-workspace writes are not published to layer-stack
- command-exec has no Git/gitignore policy branches
- command-exec imports no concrete layer-stack manager, manifest, merged view,
  OCC service, or publish internals
