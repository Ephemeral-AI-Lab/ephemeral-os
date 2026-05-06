# Phase 06 - Three-Server Supervision and Transport

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Start and supervise `layer-stack-server`, `occ-server`, and
`command-exec-server` during sandbox setup. Route public runtime envelopes to
the correct AF_UNIX socket and remove fallback behavior that hides server
boundary failures.

Implementation scope:

```text
start layer-stack.sock, occ.sock, and command-exec.sock during setup
add server health/readiness checks
add thin client routing by runtime envelope op
route read/status to layer-stack-server
route write/edit to occ-server
route shell to command-exec-server
teach restart handling to fence unsafe leases and staging
remove fork fallback after guarded soak
```

Out of scope:

```text
no new public API verbs except diagnostics needed for readiness
no raw exec workspace recovery implementation
no production fallback to in-process backend
```

Exit condition:

```text
setup starts all three servers, every public guarded verb has exactly one first
target server, and server crashes fail closed without corrupting layer-stack.
```

## 2. Main Data Objects

```text
RuntimeEnvelope
  op
  workspace_ref
  request_id
  actor_id
  args

ServerProcessSpec
  name
  socket_path
  module
  readiness_probe
  restart_policy

RuntimeServerStatus
  name
  pid
  socket_path
  ready
  started_at
  last_error

ThinClientRoute
  op_prefix
  socket_path
  timeout
```

## 3. File/Folder Structure Change

Target additions and updates:

```text
backend/src/sandbox/control/daemon/
|-- command.py
|-- install.py
|-- bundle.py

backend/src/sandbox/control/ops/
|-- setup.py
|-- runtime_services.py

backend/src/sandbox/runtime/
+-- supervisor.py
+-- thin_client.py
+-- server_common.py
|-- layer_stack_server.py
|-- occ_server.py
|-- command_exec_server.py

backend/tests/unit_test/test_sandbox/test_runtime/
+-- test_thin_client_routing.py
+-- test_supervisor_readiness.py
```

## 4. Workflow Demonstration

Setup:

```text
status.create_sandbox(project_dir="/testbed")
  -> provider create
  -> setup_after_create
  -> upload runtime bundle
  -> supervisor starts:
       layer-stack-server on layer-stack.sock
       occ-server on occ.sock
       command-exec-server on command-exec.sock
  -> readiness probes pass
  -> layer-stack-server bind workspace and build full base
  -> guarded API ready
```

Thin client routing:

```text
api.read_file        -> layer-stack.sock
api.write_file       -> occ.sock
api.edit_file        -> occ.sock
api.shell            -> command-exec.sock
api.workspace_status -> layer-stack.sock
```

Crash behavior:

```text
kill command-exec-server
  -> active shell calls fail
  -> occ/layer-stack remain intact

kill occ-server
  -> mutations fail closed
  -> layer-stack manifest remains valid

kill layer-stack-server
  -> reads/mutations fail closed
  -> restart reloads workspace binding and fences unresolved leases/staging
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `thin_client.py` | One in-sandbox entrypoint that routes envelopes to server sockets. |
| `layer-stack.sock` | Durable workspace state and read/status server. |
| `occ.sock` | Mutation policy and publish gate server. |
| `command-exec.sock` | Guarded shell execution server. |
| `RuntimeEnvelope` | Distinguishes guarded API calls from public `raw_exec`. |
| no fork fallback | Boundary failures must surface instead of silently mutating real `/testbed`. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime/test_thin_client_routing.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime/test_supervisor_readiness.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api -q
```

Required assertions:

- all three sockets are supervised and readiness-checked during setup
- each public guarded op routes to the expected first target server
- shell never enters OCC before command-exec
- write/edit never enter command-exec
- server restart does not leave writable real `/testbed` fallback enabled
- stale lease/staging records are fenced or cleaned on restart
