# Agent Collaboration and Implementation Notes

This codebase is edited across multiple agent sessions at the same time. A dirty
worktree is usually expected and should be treated as parallel agent activity,
not as a reason to stop.

## Project Context

- Python package metadata lives in `pyproject.toml`. The project supports Python
  `>=3.10`; lint/type tooling is configured for Python 3.11.
- Use `uv` for dependency management and command execution. Typical setup is
  `uv sync --extra dev`; run project commands with `uv run ...` when the virtual
  environment is not already active.
- Main backend areas are `backend/src/task_center`, `backend/src/engine`, and
  `backend/src/sandbox`.

## Project References

- TaskCenter harness and context-engine reference:
  `docs/task_center_harness_and_context_engine.html`.
- Sandbox workspace architecture reference:
  `docs/sandbox-workspace-architecture`.

## Backend Architecture

- TaskCenter is the multi-agent coding control plane. Its core idea is task
  handoff: tasks advance through persisted state rather than direct
  agent-to-agent communication.
- Do not introduce peer-to-peer agent communication or a global agent
  orchestrator. Coordination should flow through TaskCenter state, terminal
  submissions, context packets, and lifecycle reports.
- The TaskCenter state model is goal -> iteration -> attempt, with attempts
  driving planner, generator, and evaluator task roles.
- `context_engine` builds the context for different task states and harness
  phases. It supports retrying attempts, deferred iterations, and evaluation
  gates; lifecycle policy should stay in the TaskCenter handlers/managers rather
  than being hidden inside context construction.
- Sandbox is the main tool-execution environment. Docker is the default sandbox
  provider unless `EOS_SANDBOX_PROVIDER` or central config selects Daytona.
  Agents run outside the sandbox and call provider-backed sandbox APIs to
  perform file, shell, plugin, and workspace actions.
- Shared ephemeral workspace file operations use daemon-owned layer-stack plus
  OCC fast paths for `read_file`, `write_file`, and `edit_file` when a workspace
  binding exists. Shell/search/plugin-style operations use the overlay pipeline;
  write-capable overlay results publish through OCC-gated paths.
- Isolated workspace mode is an explicit `enter_isolated_workspace` /
  `exit_isolated_workspace` lifecycle. It gives an agent a persistent workspace
  for that isolated session, and its changes are discarded after exit.
- The engine loop is responsible for forcing agents to submit terminal tools that
  mark task completion and state. Those terminal results are part of TaskCenter
  context and state management, not just user-facing messages.

## Code Anchors

- TaskCenter state is grounded in `task_center/goal/state.py`,
  `task_center/iteration/state.py`, and `task_center/attempt/state.py`.
- Handoff is implemented by `submit_execution_handoff` through
  `GoalStarter.start(GoalOrigin.task(...))`; parent generator tasks move to
  `WAITING_GOAL` and resume through `GoalClosureReportRouter` plus
  `AttemptOrchestrator.apply_goal_closure_report`.
- The code does contain `AttemptOrchestrator`, but it is a per-attempt
  planner -> generator DAG -> evaluator state machine. Treat it as lifecycle
  machinery, not permission to add a global orchestration layer.
- `ContextEngine` only builds recipe-driven packets from store state. Planner,
  generator, and evaluator recipes live under `task_center/context_engine/recipes`.
- Terminal-tool enforcement lives in `engine/query/loop.py`,
  `engine/tool_call/dispatch.py`, and `tools/_framework/execution/tool_call.py`.
- Sandbox provider selection lives in `sandbox/provider/bootstrap.py` and
  `config/sections/sandbox.py`; Docker's provider implementation is under
  `sandbox/provider/docker`.
- Workspace routing lives in `sandbox/daemon/workspace_tool_dispatch.py`.
  Layer-stack/OCC services live in `sandbox/layer_stack` and `sandbox/occ`;
  overlay execution lives in `sandbox/ephemeral_workspace` and `sandbox/overlay`.
- Isolated workspace lifecycle is implemented by
  `tools/isolated_workspace`, `sandbox/host/isolated_workspace_lifecycle.py`,
  and `sandbox/isolated_workspace`. Its exit path tears down the namespace,
  releases the snapshot lease, and removes the scratch directory.

## Parallel Agent Work

- Do not revert, overwrite, or discard another agent's work unless the user
  explicitly asks for that.
- If existing changes are outside the current plan, infer the likely intent from
  file names, diffs, tests, and surrounding code, then adjust your own plan
  around that work instead of blocking. Ask only when ambiguity makes safe
  progress impossible.
- Keep your edits scoped to your task, but integrate with concurrent changes
  when needed for correctness.
- If tests fail because of another agent's in-progress work, it is acceptable to
  help fix those failures when the fix is clear and compatible with your task;
  then continue your own work.
- Before committing or staging, distinguish your intended changes from unrelated
  concurrent work unless the user explicitly asked to include everything.

## Before Coding

- State material assumptions before acting when the task or ownership boundary is
  ambiguous.
- If a request has multiple plausible interpretations, name the options and pick
  the smallest safe interpretation, or ask when guessing would risk the user's
  work.
- Push back on unnecessary complexity. Prefer the direct implementation that
  solves the stated problem.

## Implementation Style

- Write the minimum code that satisfies the request. Do not add speculative
  features, configuration, extension points, or abstractions.
- If the solution is growing large and a smaller design would solve the same
  problem, simplify before continuing.
- Avoid defensive branches for impossible states unless the surrounding codebase
  already requires that style.
- Match the existing code's style and ownership boundaries even when you would
  design greenfield code differently.

## Surgical Scope

- Touch only the files and lines needed for the user's request.
- Do not opportunistically refactor adjacent code, reformat unrelated files, or
  delete pre-existing dead code.
- Clean up imports, variables, functions, and files that your own changes made
  unused, but leave unrelated cleanup as a note unless asked.
- Every changed line should have a clear reason tied to the task, a test fix, or
  compatibility with parallel work.

## Verification

- Convert the request into concrete success criteria before or while
  implementing.
- For bugs, prefer a failing test or focused reproduction before the fix when
  practical.
- For refactors, preserve behavior and run the narrowest convincing checks before
  and after risky changes when practical.
- For multi-step tasks, keep a short plan with a verification step for each
  meaningful phase, then iterate until the criteria are met.
