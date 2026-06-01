# Migration Plan: `task_center_runner` -> `test_runner`

Status: draft
Date: 2026-06-01
Target package: `backend/src/task_center_runner` -> `backend/src/test_runner`

Builds on:

- `docs/plans/task_center_to_workflow_REFACTOR_PLAN.md`
- `docs/plans/sandbox-rust-external-migration-PLAN.md`
- `docs/plans/sandbox-plugin-service-adversarial-plan.md`
- `docs/architecture/index.html`
- `docs/architecture/task_center_runner/`

## 0. Target state

`test_runner` is the test and benchmark harness for the Task-first agentic
framework. It is not Task, Workflow, runtime entry, or sandbox infra.

The canonical request flow under test is:

```text
user request
  -> request row + root Task(role=root, workflow_id=NULL)
  -> root agent runs directly from task.instruction
  -> root agent may call non-terminal delegate_workflow(goal)
  -> delegated workflow agents may plan/run/reduce work
  -> root agent submits submit_root_outcome
  -> request completes
```

Workflow is no longer the first-class agent framework. Agent/Task is first
class; Workflow is a persisted decomposition tool that root and executor agents
can call through `delegate_workflow(goal)`.

Sandbox infra target state is Rust-only for in-sandbox execution. The Python
`backend/src/sandbox` tree keeps only host/API/provider-side code required to
upload, launch, connect to, and expose the Rust sandbox runtime. After the Rust
parity gates pass and safe removal is confirmed, all non-host/API/provider
Python sandbox infra is removed.

## 1. Scope

### In scope

- Rename the package import surface from `task_center_runner.*` to
  `test_runner.*`.
- Rename the on-disk package directory, test paths, architecture module, docs,
  CLI commands, run labels, report schemas, and benchmark entrypoints that carry
  the runner name.
- Rewrite runner scenarios and mock-agent probes around the Task-first request
  flow, `submit_root_outcome`, and non-terminal `delegate_workflow`.
- Convert runner sandbox coverage to the Rust sandbox contract: non-login Bash
  command/session semantics, Rust PPC plugin service behavior, Rust isolated
  workspace lifecycle, and Rust-only daemon/runner paths.
- Set live concurrent sandbox runners to `3` and verify that three parallel
  live E2E lanes run without exceeding sandbox quota or leaking leases.
- Define the final pass condition for deleting Python sandbox infra.

### Out of scope

- Reopening the Task-first architecture already documented in
  `task_center_to_workflow_REFACTOR_PLAN.md`.
- Rewriting plugin implementations under `backend/src/plugins/catalog/*`.
  Plugin implementations remain payloads; the sandbox plugin dispatch/importlib
  layer moves to Rust PPC.
- Replacing the Python host API/provider boundary. Host-side launch/connect,
  `api.v1.*`, Docker provider upload, and config/bootstrap code remain Python
  unless a later plan explicitly moves them.

## 2. Evidence from the current checkout

- `backend/src/task_center_runner/core/engine.py` already calls
  `workflow.start_request(...)`, binds a `request_id`, lists tasks by request,
  and records request status. This is the right seam for the renamed harness.
- `backend/src/task_center_runner/core/stores.py` still exposes
  `create_per_test_task_center_stores()` and docstrings still describe
  TaskCenter stores. This is a required rename/semantic cleanup.
- `backend/src/task_center_runner/tests/mock/task_center/` still names the
  old correctness bucket. It should become `tests/mock/workflow/` plus root
  request tests where appropriate.
- Scenario names and comments still include old root-workflow language:
  `pipeline.initial_workflow`, `recursive_handoff_goal`,
  `request_recursive_workflow`, and background shell scenarios that call
  generic shell background tools.
- `docs/architecture/task_center_runner/` is already titled "Workflow Runner
  (Testing)" but the path, evidence metadata, CLI examples, and prose still
  point at `task_center_runner`.
- `ephemeralos.yaml` already sets `runner.sandbox_quota: 3`; `RunnerConfig`
  currently defaults `sandbox_quota` to `5`. The migration should make the
  three-runner live E2E contract explicit and tested.
- `backend/src/task_center_runner/agent/mock/tool_scripts.py` imports
  `sandbox.occ.service.AUTO_SQUASH_MAX_DEPTH` directly. Any runner import from
  Python sandbox internals is a blocker before Python sandbox removal.

## 3. Migration phases

### Phase A - Freeze the rename boundary

Goal: make the rename mechanical and auditable before changing behavior.

1. Create `backend/src/test_runner/` by moving `backend/src/task_center_runner/`.
2. Replace imports from `task_center_runner.*` to `test_runner.*` across source,
   tests, docs, and scripts.
3. Rename these docs and paths:
   - `docs/architecture/task_center_runner/` -> `docs/architecture/test_runner/`
   - architecture module label: `Workflow Runner (Testing)` -> `Test Runner`
   - `backend/src/task_center_runner/read.md` -> `backend/src/test_runner/read.md`
4. Rename user-facing commands:
   - `python -m task_center_runner.benchmarks.sweevo`
   - becomes `python -m test_runner.benchmarks.sweevo`
5. Rename defaults:
   - `runner.run_label: task_center_runner` -> `test_runner`
   - isolated SQLite bundle directory `task_center_runner/` -> `test_runner/`
   - report schema prefix `task_center_runner.*` -> `test_runner.*`
6. Keep a temporary import shim only if an external caller still needs one:
   `backend/src/task_center_runner/__init__.py` may raise a clear deprecation
   error or re-export `test_runner` for one short transition. The preferred final
   state has no `task_center_runner` package.

Exit gate:

```bash
rg -n "task_center_runner|TaskCenter|task_center_runner\\.performance_report" \
  backend/src backend/tests docs scripts
```

Allowed hits are limited to historical plan references and the short-lived
compatibility shim if retained.

### Phase B - Rename TaskCenter semantics inside the harness

Goal: remove old terminology without changing the runner's role.

1. Rename core objects:
   - `TaskStoreBundle` docstrings: "TaskCenter stores" -> "Task/request stores"
   - `create_per_test_task_center_stores()` ->
     `create_per_test_task_stores()`
   - test helpers and mocks that still mention `task_center_run_id` ->
     `request_id`
2. Rename test buckets:
   - `tests/mock/task_center/` -> `tests/mock/workflow/`
   - keep sandbox tests under `tests/mock/sandbox/`
   - add `tests/mock/root/` only for root-agent-only scenarios that do not
     delegate a workflow.
3. Rename scenario vocabulary:
   - `pipeline.initial_workflow` -> `pipeline.root_delegates_workflow`
   - `recursive_handoff_goal` -> `delegated_workflow_goal`
   - `request_recursive_workflow` -> `delegate_workflow`
   - "root workflow" / "child workflow" -> "root Task" /
     "delegated Workflow"
4. Update `ScenarioContext` and scenario helpers so `ctx.workflow is None` means
   "root Task context", not "entry-origin workflow".
5. Keep graph summaries workflow-specific. Root request summaries belong in a
   separate root/request section of `RunReport`.

Exit gate:

```bash
uv run pytest -q backend/src/test_runner/tests/mock/contracts
uv run pytest -q backend/src/test_runner/tests/mock/workflow
uv run pytest -q backend/tests/unit_test/test_task_center_runner
```

The last path should be renamed as part of the phase; it is listed here as the
current source anchor to migrate.

### Phase C - Adopt the root-agent-first runtime contract

Goal: make the runner test the actual production request lifecycle.

1. Add explicit root-agent mock scripting support in `ScenarioLoopRunner`.
   Prompt inspection for root must assert:
   - no ContextEngine packet
   - initial user content is the request prompt
   - root terminal is `submit_root_outcome`
   - `delegate_workflow`, `check_workflow_status`, and `cancel_workflow` are
     non-terminal tools when present
2. Add root-focused scenarios:
   - root completes directly with `submit_root_outcome`
   - root delegates one workflow, waits/checks the handle, then submits root
     outcome
   - root delegation failure is synthesized into root outcome rather than
     closing the parent task by workflow close mutation
3. Update workflow scenarios to cover executor delegation rather than terminal
   handoff. The parent task remains `RUNNING` until its own terminal submission.
4. Delete runner assumptions around:
   - synthetic root Workflow
   - `submit_workflow_handoff`
   - `WAITING_WORKFLOW`
   - close-time mutation of the parent task
5. Align architecture pages and evidence paths with current files:
   `runtime/entry.py`, `workflow/starter.py`,
   `tools/workflow/delegate_workflow.py`, and `tools/submission/root`.

Exit gate:

```bash
rg -n "submit_workflow_handoff|WAITING_WORKFLOW|root workflow|child workflow|handoff" \
  backend/src/test_runner backend/tests docs/architecture

uv run pytest -q backend/src/test_runner/tests/mock/root
uv run pytest -q backend/src/test_runner/tests/mock/workflow
uv run pytest -q backend/tests/unit_test/test_tools/test_submission_main_role_terminals.py
```

### Phase D - Convert runner sandbox coverage to Rust runtime contracts

Goal: make the runner a Rust-sandbox validation harness, not a Python sandbox
regression harness.

1. Runtime selection:
   - run sandbox suites with `EOS_SANDBOX_RUNTIME=rust`
   - keep `EOS_SANDBOX_PROVIDER=docker` explicit for local live E2E
   - fail early if the Rust binary upload/signature/protocol pin is missing
2. Command/session tools:
   - replace old generic background shell scenarios with typed command/session
     coverage from Phase 3T:
     `exec_command`, PTY stdin/progress/cancel, non-login Bash, process-tree
     cleanup, and active-only session controls
   - remove references to `shell(background=True)`,
     `check_background_task_result`, `wait_background_tasks`, and generic shell
     background cancellation from model-facing assertions
3. Plugin service:
   - route plugin scenarios through Rust PPC service operations
   - assert isolated-mode plugin blocking
   - assert read-only service refresh does not publish
   - assert write/self-managed callbacks publish through the same daemon OCC
     writer/storage lock as primary publishes
4. Isolated workspace:
   - run tests against `eosd ns-holder` + `eosd ns-runner` setns mode
   - assert enter rejects active sandbox-bound background work
   - assert exit drains/cancels active work and releases leases/scratch
   - assert no plugin/LSP operations while isolated mode is active
5. Remove runner imports from Python sandbox internals. The runner may call
   public tools/API helpers, but must not import from these Python implementation
   packages:
   - `sandbox.daemon`
   - `sandbox.overlay`
   - `sandbox.occ`
   - `sandbox.layer_stack`
   - `sandbox.ephemeral_workspace`
   - `sandbox.isolated_workspace`
   - `sandbox.shared`

Exit gate:

```bash
EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q backend/src/test_runner/tests/mock/sandbox

rg -n "from sandbox\\.(daemon|overlay|occ|layer_stack|ephemeral_workspace|isolated_workspace|shared)|import sandbox\\.(daemon|overlay|occ|layer_stack|ephemeral_workspace|isolated_workspace|shared)" \
  backend/src/test_runner backend/tests/unit_test/test_test_runner
```

### Phase E - Make three concurrent live E2E runners the standard

Goal: prove the runner can execute three live sandbox lanes in parallel without
resource leakage or false serialization.

1. Add an explicit config field unless `sandbox_quota` is intentionally reused:
   `runner.live_e2e.concurrent_sandbox_runners: 3`.
2. Set the default and repository config to `3`. If `sandbox_quota` remains the
   backing setting, change `RunnerConfig.sandbox_quota` default from `5` to `3`
   and document that it is the live E2E runner cap.
3. Gate fixture provisioning with a semaphore of size `3` so tests cannot
   accidentally overrun the configured sandbox cap.
4. Add a smoke command for parallel live execution:

```bash
EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q -n 3 backend/src/test_runner/tests/mock/sandbox/project_build
```

5. Add a teardown assertion that all three lanes release:
   sandbox leases, daemon invocations, PTY/session handles, plugin services, and
   isolated workspace holders.

Exit gate:

```bash
EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q -n 3 backend/src/test_runner/tests/mock/sandbox
```

If the full sandbox suite is too expensive for every PR, define a fixed
three-lane smoke subset and keep the full command as the cutover gate.

### Phase F - Benchmarks and Rust migration gates

Goal: attach the runner cutover to measured Rust sandbox evidence.

Required benchmark/script lanes:

```bash
uv run python backend/scripts/build_upload_eosd_docker.py --arch amd64
uv run python backend/scripts/bench_sandbox_e2e.py
uv run python backend/scripts/bench_rust_daemon_phase2.py
uv run python backend/scripts/bench_rust_daemon_phase3.py
uv run python backend/scripts/bench_rust_daemon_phase3t_pty.py
uv run python backend/scripts/bench_rust_daemon_phase3t_av7_parity.py
uv run python backend/scripts/bench_plugin_refresh_strategies.py
```

Required Rust lanes:

```bash
cd sandbox
cargo fmt --all --check
cargo test --workspace --all-targets
cargo test -p eos-plugin
cargo test -p eos-daemon plugin
```

Required live parity claims:

- CP-4t non-login Bash command/session gate passes.
- CP-4/CP-5 contention gates pass against Rust shell/session and plugin PPC.
- AV-3 cancellation/session cleanup passes under live load.
- AV-4 audit pull loses zero records under CP-4 load.
- AV-7 forward/back on-disk parity passes.
- AV-9 isolated workspace lifecycle parity passes.
- AV-10 plugin parity passes for read-only, write-allowed, and self-managed
  modes.

All benchmark reports must state the benchmark category boundary. Raw mount-init
speedups must not be presented as end-to-end shell/tool speedups.

### Phase G - Remove Python sandbox infra

Goal: satisfy the final pass condition.

This phase starts only after Phases A-F are green and Rust is the default
sandbox runtime.

Allowed Python sandbox paths after removal:

- `backend/src/sandbox/api/`
- `backend/src/sandbox/host/`
- `backend/src/sandbox/provider/`
- `backend/src/sandbox/provider/bootstrap.py`
- `backend/src/config/sections/sandbox.py`
- protocol fixtures, runtime-artifact pinning, and host-side upload/signature
  verification code required by the Rust daemon

Removal candidates:

- `backend/src/sandbox/daemon/`
- `backend/src/sandbox/overlay/`
- `backend/src/sandbox/occ/`
- `backend/src/sandbox/layer_stack/`
- `backend/src/sandbox/shared/`
- `backend/src/sandbox/ephemeral_workspace/`
- `backend/src/sandbox/isolated_workspace/`
- Python daemon launch/thin-client/runtime-bundle/chunked-upload paths that the
  Rust plan marks as Phase 5 cutover removals
- Python sandbox plugin importlib dispatch under `ephemeral_workspace/plugin/`

Pass condition:

```bash
rg -n "sandbox\\.(daemon|overlay|occ|layer_stack|shared|ephemeral_workspace|isolated_workspace)" \
  backend/src backend/tests docs/architecture docs/plans

find backend/src/sandbox -maxdepth 2 -type d | sort
```

After confirmed safe removal, `backend/src/sandbox` contains only host/API/
provider/config/protocol support for the Rust sandbox. No test runner code may
import deleted Python sandbox implementation modules.

## 4. Verification matrix

Run after each relevant phase:

```bash
uv run ruff check backend/src/test_runner backend/tests/unit_test/test_test_runner
uv run pytest -q backend/tests/unit_test/test_test_runner
uv run pytest -q backend/src/test_runner/tests/mock/contracts
uv run pytest -q backend/src/test_runner/tests/mock/root
uv run pytest -q backend/src/test_runner/tests/mock/workflow
```

Run for sandbox cutover:

```bash
EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q -n 3 backend/src/test_runner/tests/mock/sandbox

EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q backend/tests/live_e2e_test/sandbox/plugin

EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust \
uv run pytest -q backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ
```

Run final inventory:

```bash
rg -n "task_center_runner|TaskCenter|submit_workflow_handoff|WAITING_WORKFLOW" \
  backend/src backend/tests docs

rg -n "sandbox\\.(daemon|overlay|occ|layer_stack|shared|ephemeral_workspace|isolated_workspace)" \
  backend/src/test_runner backend/tests/unit_test/test_test_runner
```

## 5. Risk register

| Risk | Mitigation |
| --- | --- |
| Mechanical rename hides behavior changes | Phase A is rename-only; semantic changes start in Phase B/C. |
| Compatibility shims become permanent | Add a removal date and grep gate; preferred final state has no shim. |
| Runner still imports Python sandbox internals | Add import-fence tests before Phase G. |
| Three parallel lanes overrun Docker resources | Back the runner with an explicit semaphore/cap of `3`; assert teardown. |
| Plugin parity passes only for LSP | Add a non-LSP dummy service parity case before claiming generic plugin service support. |
| Rust rollback becomes unsafe after durable publish | Require AV-7 forward/back on-disk parity before write-phase cutover or Python removal. |
| Docs drift after rename | Refresh `docs/architecture/test_runner/*`, evidence paths, and search index in the same phase as the rename. |

## 6. Cutover checklist

- `backend/src/test_runner` exists; `backend/src/task_center_runner` is gone or
  contains only a temporary explicit compatibility shim.
- CLI works: `uv run python -m test_runner.benchmarks.sweevo --help`.
- Root-agent scenarios cover direct completion and delegated workflow
  completion.
- Workflow scenarios cover executor `delegate_workflow` without terminal
  handoff or parent close mutation.
- Sandbox scenarios run with `EOS_SANDBOX_RUNTIME=rust`.
- Three parallel live E2E lanes pass with no resource leaks.
- Runner has no imports from deleted Python sandbox infra modules.
- Rust sandbox CP/AV gates in Phase F are green.
- Python sandbox infra removal inventory is reviewed and deletion is confirmed
  safe.
- Final pass condition holds: all non-host/API/provider Python sandbox infra is
  removed from `backend/src/sandbox`.
