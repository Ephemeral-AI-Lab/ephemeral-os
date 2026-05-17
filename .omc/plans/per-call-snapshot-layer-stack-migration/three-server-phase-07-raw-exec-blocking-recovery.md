# Phase 07 - Raw Exec Workspace Blocking and Recovery

**Status:** deferred; do not implement in the current migration wave
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`
**Live checkout basis:** 2026-05-08 audit of `backend/src/sandbox`

## Decision

Defer Phase 07. Do not add raw-exec workspace blocking, recovery APIs, or real
workspace scanner modules as part of the current phase sequence.

The current implementation already protects normal workspace operations by
routing guarded `read_file`, `write_file`, `edit_file`, and `shell` through the
runtime/layer-stack/OCC path. Public `raw_exec` remains the provider/runtime
escape hatch for setup, status/control, diagnostics, and live-test probes.

This means the active contract is:

```text
guarded workspace APIs:
  use layer-stack/OCC and do not treat real /testbed as normal workspace truth

public raw_exec:
  remains unguarded provider/runtime execution
  must not be used by agent-visible write/edit/read/shell workflows

Phase 07 raw_exec blocking and recovery:
  parked design topic
  no file additions
  no test additions
  no live gate in the current migration wave
```

## Current Checkout Audit

Implemented now:

```text
backend/src/sandbox/api/tool/raw_exec.py
  existing public raw provider exec primitive
  delegates straight to get_adapter(sandbox_id).exec(...)
  has no workspace policy and no layer-stack context

backend/src/sandbox/control/ops/setup.py
  existing post-create/post-start setup hook
  uploads runtime bundle, runs ensure_git, calls api.ensure_workspace_base,
  then requires api.runtime.ready

backend/src/sandbox/control/daemon/command.py
  existing guarded runtime transport
  sends typed runtime envelopes through provider adapter exec directly
  does not route guarded write/edit/read/shell through public raw_exec

backend/src/sandbox/runtime/handlers/
  existing per-verb read/write/edit/shell entrypoints
  read/write/edit use handlers._common for path classification
  shell_handler delegates to runtime.command_exec_server

backend/src/sandbox/runtime/command_exec_server.py
  existing guarded shell pipeline
  prepares a workspace snapshot, runs workspace-replaced command execution,
  captures upperdir changes, and submits typed changes through OCCClient

backend/src/sandbox/runtime/layer_stack_handlers.py
  existing api.ensure_workspace_base, api.build_workspace_base(reset=True),
  api.workspace_binding, snapshot prepare/release

backend/src/sandbox/layer_stack/workspace_base.py
  existing full workspace base builder with active/base root hashes

backend/src/sandbox/runtime/health_handlers.py
  existing api.runtime.ready diagnostics
```

Not implemented and not part of this deferred phase:

```text
backend/src/sandbox/control/ops/runtime_services.py
backend/src/sandbox/api/tool/raw_exec_policy.py
backend/src/sandbox/layer_stack/workspace_recovery.py
backend/src/sandbox/layer_stack/workspace_scanner.py
backend/tests/unit_test/test_sandbox/test_api/test_raw_exec_workspace_policy.py
backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_recovery.py
```

Existing tests already prove the most important current invariant:

```text
backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py
  test_read_file_in_workspace_returns_layer_stack_bytes mutates real workspace
  after base build and verifies read_file still returns layer-stack bytes

backend/tests/unit_test/test_sandbox/test_api/test_raw_exec.py
  proves raw_exec delegates directly to the registered adapter
```

## Why Deferred

Raw-exec blocking needs an additional policy surface that is not currently
owned by the runtime:

```text
raw_exec has only sandbox_id, command, cwd, timeout
raw_exec does not receive layer_stack_root or workspace_root
setup and live-test probes intentionally use raw_exec outside guarded APIs
classifying arbitrary shell text safely requires an explicit fail-closed policy
```

Adding that policy now would broaden the current migration beyond the active
workspace replacement and OCC paths. It would also require caller-intent changes
for setup/recovery/diagnostic commands so binding-missing behavior is not
misclassified.

## Deferred Scope

If Phase 07 is revived later, start from this bounded scope:

```text
add explicit raw_exec intent:
  unknown | pre_base_setup | liveness_probe | outside_workspace

add conservative raw_exec policy:
  default unknown calls fail closed when binding status cannot be determined
  setup callers explicitly mark pre_base_setup
  liveness probes explicitly mark liveness_probe
  workspace-targeting or unclassified commands reject after base build

add diagnostic scanner only:
  deterministic real /testbed inventory
  no layer-stack publish side effects

add explicit rebuild-base recovery only if needed:
  wrapper around existing api.build_workspace_base(reset=True)
  report old/new binding and scan details
  no implicit background reconciliation
```

Still out of scope if revived:

```text
no control/ops/runtime_services.py
no raw_exec wrapper for guarded write/edit/read/shell
no automatic divergence state machine
no full-root versioning
no rebase unless a concrete product workflow requires preserving real /testbed drift
```

## Current Pass Bar

Phase 07 contributes no current implementation pass bar. The active pass bar
stays with the already implemented guarded workspace routes:

```text
read_file returns layer-stack bytes for in-workspace paths
write_file and edit_file publish through OCCClient
shell enters runtime.command_exec_server and submits captured changes through OCCClient
guarded runtime envelopes do not call public raw_exec
public raw_exec remains available for setup/status/control/debug/live-test probes
```
