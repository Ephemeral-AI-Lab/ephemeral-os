# Phase 04 Implementation Report - Workspace-Replaced Shell Execution

**Date:** 2026-05-07
**Plan:** `three-server-phase-04-workspace-replaced-shell.md`
**Status:** implemented, unit-verified, and Daytona live-verified

## Summary

Implemented the Phase 04 guarded shell path:

- Added a runtime-local `command_exec_server` for `api.shell` and
  `api.shell_batch`.
- Routed public shell calls through command-exec before command execution.
- Prepared a leased layer-stack workspace snapshot for every guarded shell
  request.
- Replaced the assigned workspace root inside a private user and mount namespace
  so `/testbed` comes from the leased manifest while `/bin`, `/usr`, `/tmp`, and
  the rest of the sandbox filesystem remain visible.
- Captured only the workspace upperdir after command execution.
- Converted captured workspace changes into typed OCC changes and submitted
  them through `runtime.clients.occ.OCCClient`.
- Released the layer-stack lease after OCC result handling, including transient
  lowerdir cleanup for cache-disabled measurements.
- Added cache-enabled versus cache-disabled Daytona A/B coverage for the real
  command-exec shell workload.

The cache experiment did not justify keeping the persistent materialized
lowerdir cache as a shell workload optimization. The latest corrected live run
showed cache-enabled slower at all tested concurrency levels, while consuming
about 36 MB of extra lowerdir cache. Across four successful Daytona-backed runs,
the only average cache-enabled win was at concurrency 20, and it was roughly
107 ms batch / 113 ms p95, below the Phase 04 keep threshold of 250 ms or 20%.

## Files Changed

Command-exec package:

- `backend/src/sandbox/command_exec/clients.py`
  - Extended the workspace lease client protocol to carry snapshot cache policy.
- `backend/src/sandbox/command_exec/request.py`
  - Added the command-exec request shape used after runtime argument parsing.
- `backend/src/sandbox/command_exec/result.py`
  - Added command process, workspace capture, and command-exec result envelopes.
- `backend/src/sandbox/command_exec/env.py`
  - Added cwd resolution and environment construction for commands running under
    the workspace-replaced mount.
- `backend/src/sandbox/command_exec/workspace_mount.py`
  - Added workspace replacement execution.
  - Uses `unshare -Urm` when a private namespace is available.
  - Falls back to copy-backed execution only when namespace replacement is not
    available and the command does not reference the declared workspace path.
- `backend/src/sandbox/command_exec/namespace_helper.py`
  - Added the helper process executed inside the private namespace.
  - Mounts overlayfs at the assigned workspace root, runs the command, unmounts,
    and writes timing refs.
- `backend/src/sandbox/command_exec/capture/upperdir.py`
  - Added upperdir capture for writes, deletes, symlinks, and opaque dirs.
- `backend/src/sandbox/command_exec/capture/changeset.py`
  - Added conversion from captured workspace path changes to OCC changes.

Runtime and layer-stack integration:

- `backend/src/sandbox/runtime/command_exec_server.py`
  - Added command-exec server handlers for `shell` and `shell_batch`.
  - Sequences snapshot lease, workspace mount, command execution, capture, OCC
    apply, release, and result projection.
  - Supports `snapshot_cache_policy=enabled|disabled` and
    `EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY`.
  - Places overlay upper/work/run dirs under `/dev/shm/eos-command-exec/...`
    when available. Daytona's `/tmp` is Docker overlay-backed, and overlayfs
    rejected `/tmp` upper/work dirs with mount exit code 32 during live testing.
- `backend/src/sandbox/runtime/api_handlers.py`
  - Delegated `api.shell` and `api.shell_batch` to `command_exec_server`.
  - Keeps existing write/edit OCC paths unchanged.
- `backend/src/sandbox/runtime/server.py`
  - Registers shell operations against `command_exec_server`.
- `backend/src/sandbox/runtime/clients/layer_stack.py`
  - Added cache-policy-aware `prepare_workspace_snapshot`.
  - Added the cache-disabled transient lowerdir preparation path.
- `backend/src/sandbox/layer_stack/stack_manager.py`
  - Added manifest, cache policy, and transient lowerdir metadata to
    `PrepareWorkspaceSnapshotResult`.
- `backend/src/sandbox/runtime/layer_stack_handlers.py`
  - Drops command-exec service cache when layer-stack state is reset.
- `backend/src/sandbox/control/daemon/bundle.py`
  - Added `sandbox/command_exec` to the sandbox runtime bundle.

Tests:

- `backend/tests/unit_test/test_sandbox/test_command_exec/test_workspace_mount.py`
- `backend/tests/unit_test/test_sandbox/test_command_exec/test_env_policy.py`
- `backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py`
- `backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py`
- `backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_replaced_shell_cache_ab.py`

## Runtime Workflow

```text
host sandbox.api.tool.shell(...)
  -> runtime op api.shell
  -> sandbox.runtime.command_exec_server.shell
  -> LayerStackClient.prepare_workspace_snapshot(cache_policy=...)
  -> leased manifest N and lowerdir
  -> allocate command upperdir/workdir/run refs under /dev/shm when available
  -> unshare -Urm python -m sandbox.command_exec.namespace_helper
  -> namespace helper overmounts /testbed:
       lowerdir = leased manifest lowerdir
       upperdir = per-command upperdir
       workdir  = per-command overlayfs workdir
  -> command runs with full sandbox filesystem except replaced /testbed
  -> helper unmounts /testbed and writes stdout/stderr/timings refs
  -> command-exec captures workspace upperdir changes
  -> workspace_changes_to_occ_changes(...)
  -> OCCClient.apply_changeset(..., snapshot=leased_manifest, atomic=True)
  -> release layer-stack lease
  -> drop transient lowerdir when cache policy is disabled
  -> return public shell result with changed_paths, status, conflict, timings
```

## Exit Criteria

| Criterion | Result |
|---|---|
| `sandbox.api.tool.shell` enters command-exec first | `api.shell` and `api.shell_batch` now delegate to `sandbox.runtime.command_exec_server`; public shell unit tests pass. |
| Command sees leased `/testbed` manifest while `/bin` and `/usr` remain usable | Daytona A/B command checks `/bin/sh`, reads `/testbed/stable.txt`, and reads `/testbed/payload.bin` inside private namespace. |
| `/testbed` writes are captured and submitted to OCC | Live A/B writes unique tracked outputs, asserts `changed_paths`, and reconciles through public `read_file`. |
| Outside-workspace writes are not published to layer-stack truth | Live A/B writes `/tmp/eos-phase04-...` and verifies public `read_file` does not see it as workspace truth. |
| Cache-disabled path is not a fallback that mutates real `/testbed` | Cache-disabled uses the same command-exec, workspace replacement, upperdir capture, and OCC path, with transient lowerdir cleanup after release. |
| Command-exec package has no concrete layer-stack manager, OCC service, or Git policy imports | `backend/tests/unit_test/test_sandbox/test_import_fence.py` passes. The runtime server remains the composition layer. |
| Runtime bundle contains command-exec code | `test_bundle_upload.py` covers required `sandbox/command_exec` bundle files. |

## Cache A/B Decision

Latest corrected Daytona-backed artifact:

```text
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190942Z.jsonl
```

Latest corrected run:

| concurrency | cache enabled batch ms | cache enabled p95 ms | cache disabled batch ms | cache disabled p95 ms | batch ms saved | p95 ms saved | extra cache bytes peak | keep cache |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 1160.268 | 1160.037 | 1071.686 | 1071.480 | -88.582 | -88.557 | 36,047,318 | false |
| 5 | 1476.009 | 1471.494 | 1220.336 | 1212.118 | -255.673 | -259.376 | 36,047,348 | false |
| 10 | 2489.029 | 2481.828 | 1437.692 | 1430.826 | -1051.337 | -1051.002 | 36,047,498 | false |
| 20 | 5405.834 | 5392.926 | 5145.287 | 5136.814 | -260.547 | -256.112 | 36,047,798 | false |

Four successful Daytona-backed A/B summaries were generated:

```text
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T185345Z.jsonl
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T185952Z.jsonl
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190615Z.jsonl
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190942Z.jsonl
```

Four-run average:

| concurrency | samples | cache enabled batch avg ms | cache disabled batch avg ms | batch ms saved avg | cache enabled p95 avg ms | cache disabled p95 avg ms | p95 ms saved avg |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 4 | 1134 | 1067 | -66 | 1133 | 1067 | -66 |
| 5 | 4 | 1487 | 1205 | -281 | 1482 | 1199 | -284 |
| 10 | 4 | 2476 | 1468 | -1008 | 2467 | 1449 | -1019 |
| 20 | 4 | 5217 | 5324 | 107 | 5202 | 5315 | 113 |

Decision:

```text
Do not keep the persistent lowerdir cache as a shell workload optimization.
The cache-disabled transient lowerdir path is the better default for this
measured command-exec workload.
```

Rationale:

- Latest corrected run: cache-enabled was slower for every tested concurrency.
- Four-run average: cache-enabled was slower for concurrency 1, 5, and 10.
- Four-run average at concurrency 20 showed only a small cache-enabled win:
  about 107 ms batch / 113 ms p95.
- The observed concurrency 20 win is below both decision bars:
  250 ms absolute improvement and 20 percent relative improvement.
- Cache-enabled consumed about 36 MB extra lowerdir cache in the latest
  corrected run; cache-disabled returned to 0 cache bytes after release.
- Correctness matched between policies in all summary rows.

## Verification

Latest Daytona-backed live A/B verification:

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_replaced_shell_cache_ab.py -q -s
```

Result: `1 passed, 1 warning in 97.64s`.

Focused unit and import verification:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py backend/tests/unit_test/test_sandbox/test_import_fence.py -q
```

Result: `50 passed, 1 warning in 1.03s`.

Ruff:

```bash
uv run ruff check backend/src/sandbox/command_exec backend/src/sandbox/runtime/command_exec_server.py backend/src/sandbox/runtime/api_handlers.py backend/src/sandbox/runtime/clients/layer_stack.py backend/src/sandbox/runtime/layer_stack_handlers.py backend/src/sandbox/control/daemon/bundle.py backend/tests/unit_test/test_sandbox/test_command_exec backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py backend/tests/unit_test/test_sandbox/test_import_fence.py backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_replaced_shell_cache_ab.py
```

Result: `All checks passed`.

Mypy:

```bash
uv run mypy --config-file backend/mypy.ini backend/src/sandbox/command_exec backend/src/sandbox/runtime/command_exec_server.py backend/src/sandbox/runtime/clients/layer_stack.py
```

Result: `Success: no issues found in 12 source files`.

Whitespace check:

```bash
git diff --check
```

Result: passed.

## Live Debug Notes

Two Daytona-specific issues were found and fixed during implementation:

- The first live A/B run failed because the runtime bundle did not include the
  new `sandbox.command_exec` package. The bundle now includes it, and
  `test_bundle_upload.py` asserts the required files are present.
- Overlayfs rejected private namespace mounts when command upper/work dirs were
  allocated under `/tmp` in the Daytona image. A raw probe showed `/tmp` is on a
  Docker overlay filesystem, while `/dev/shm` is tmpfs. The implementation now
  allocates command-exec upper/work/run dirs under `/dev/shm/eos-command-exec`
  when available.

## Notes

- This phase intentionally does not route public `write_file` or `edit_file`
  through command-exec.
- The command-exec package remains focused on request/env/mount/capture/result
  mechanics. Runtime composition owns concrete layer-stack and OCC service
  wiring.
- The live A/B test uses independent concurrent shell calls at concurrency
  1, 5, 10, and 20. It does not batch multiple shell operations into one shell
  process.
- The `.omc/results/` JSONL artifacts are local evidence artifacts. The latest
  corrected policy artifacts are:

```text
.omc/results/live-e2e-phase04-shell-cache-ab-cache_enabled-20260506T190858Z.jsonl
.omc/results/live-e2e-phase04-shell-cache-ab-cache_disabled-20260506T190942Z.jsonl
.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190942Z.jsonl
```
