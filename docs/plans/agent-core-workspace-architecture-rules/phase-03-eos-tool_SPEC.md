# Phase 03 - eos-tool Spec

Status: Draft
Date: 2026-06-09
Owner: eos-tool

## Scope

This phase rebuilds `eos-tool` as the owner of the tool framework contracts,
default tool registry construction, and concrete model-callable tool behavior.

It removes the current `eos-tool-ports` dependency by moving tool-owned
contracts into `eos-tool` and routing non-tool contracts to their real owner in
the crate-map/integration phases. It also collapses the current
one-file-per-tool command layout into family-level handlers.

Phase 03 does not own root workspace member edits, retired-crate removal, or the
final dependency DAG. Those are Phase 02 / integration responsibilities. Phase
03 owns the resulting `eos-tool` crate shape.

## Local Architecture

`eos-tool` owns:

- tool names and keys,
- tool intent and output shape,
- execution metadata facts,
- tool result DTOs,
- registered tool entries,
- tool registry,
- tool executor trait,
- hook declarations and config tokens,
- default registry construction,
- externalized tool config loading and validation (`config.rs`), keyed by
  `ToolName::ALL` and the `TerminalTool` catalog,
- concrete model-callable tool behavior,
- skill registry and skill package loading,
- `ToolRuntime`, the small executable dependency bundle passed into registry
  construction by `eos-agent-core`.

`eos-tool` does not own:

- agent-loop turn control,
- foreground tool-batch dispatch,
- pre-hook execution policy,
- `HookOutcome` or other hook-pipeline internals,
- model provider streaming,
- agent-run lifecycle rows,
- workflow state transitions,
- sandbox daemon protocol internals,
- workspace crate-map edits.

`eos-engine` receives a `ToolRegistry`, runs its own pre-hook execution pipeline,
looks up `RegisteredTool` entries, and calls the stored `ToolExecutor`. The
dependency direction is `eos-engine -> eos-tool`; `eos-tool` must not depend on
`eos-engine`.

```mermaid
flowchart LR
    AgentCore["eos-agent-core"] -->|"builds ToolRuntime"| Tool["eos-tool"]
    AgentCore -->|"build_default_registry(...)"| Registry["ToolRegistry"]
    AgentCore -->|"loop request carries registry"| Engine["eos-engine"]
    Engine -->|"get_wire(name)"| Registered["RegisteredTool"]
    Engine -->|"run hooks, then execute"| Executor["dyn ToolExecutor"]
```

## Resulting File Structure

```text
agent-core/crates/eos-tool/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── error.rs
│   ├── model.rs
│   ├── registry.rs
│   ├── config.rs
│   ├── hooks.rs
│   ├── tools.rs
│   ├── tools/
│   │   ├── sandbox.rs
│   │   ├── command.rs
│   │   ├── isolated_workspace.rs
│   │   ├── workflow.rs
│   │   ├── subagent.rs
│   │   ├── ask_advisor.rs
│   │   ├── submission/
│   │   │   ├── mod.rs
│   │   │   ├── planner.rs
│   │   │   ├── root.rs
│   │   │   ├── generator.rs
│   │   │   ├── reducer.rs
│   │   │   ├── subagent.rs
│   │   │   └── advisor.rs
│   │   ├── skills.rs
│   │   └── terminal.rs
└── tests/
    ├── registry/
    ├── sandbox/
    ├── command/
    ├── isolated_workspace/
    ├── workflow/
    ├── subagent/
    ├── ask_advisor/
    ├── submission/
    └── skills/
```

No `builtins.rs` or `builtins/` folder is required in the first target. The
built-in tool set is closed and should be represented through default registry
registration plus family handlers in `tools/`.

No first-target `catalog.rs`, `executor.rs`, `runtime.rs`, `resources.rs`,
`handles.rs`, `services.rs`, `services/`, or `hooks/` folder is allowed. Those
splits are only acceptable later if implementation proves that one file has
become materially harder to understand.

Two splits are pre-authorized in the first target because the alternative is a
single file over the repo's 800-1000 LOC review-smell line, not because of
speculative growth:

- `config.rs` owns the externalized tool-config loader/validator
  (`ToolConfigSet::load_from_dir` plus the `ToolSpec` schema helpers, ~620 LOC).
  Folding it into `registry.rs` would push `registry.rs` to ~950 LOC mixing the
  registry data structure, the executor trait, `ToolRuntime`, default
  construction, *and* markdown loading. `config.rs` is a cohesive loader with its
  own error enum — a justified split, not a forbidden `catalog.rs`.
- `tools/submission/` stays a subfolder. It holds six terminal-submission DTO
  families — `root`, `planner`, `generator`, `reducer`, `subagent`, and
  `advisor` — plus a shared `lib`; flattened it is ~960 LOC of executor logic in
  one file, which the family-split rule below already covers. The families are
  keyed by the launching `AgentRunRecordKind` / `AgentType` axis (`root`
  → `Root`; `planner`/`generator`/`reducer` → `WorkflowTask`; `subagent` →
  `Subagent`; `advisor` → `Advisor`), never by a behavioral `AgentRole`. Phase
  02 removes that profile role axis, so this spec classifies launches by
  `AgentType` only.

Family files under `tools/` are the first target. A family may gain a
same-named private subfolder only when the flat file starts mixing multiple
distinct DTO families, executor bodies, shared rendering paths, and registration
logic in a way that is less clear than the split. `tools/submission/` is the one
family that qualifies on the first day and is authorized above; all other
families start flat.

## Module Collapse Plan

| Current pattern | Target |
| --- | --- |
| `tools/sandbox/exec_command.rs` | `registry.rs` default entry plus `tools/command.rs` handler |
| `tools/sandbox/write_stdin.rs` | `registry.rs` default entry plus `tools/command.rs` handler |
| `tools/sandbox/read_command_progress.rs` | `registry.rs` default entry plus `tools/command.rs` handler |
| `tools/sandbox/read_file.rs` | `registry.rs` default entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/write_file.rs` | `registry.rs` default entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/edit_file.rs` | `registry.rs` default entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/multi_edit.rs` | `registry.rs` default entry plus `tools/sandbox.rs` handler |
| `tools/isolated_workspace/*.rs` | `registry.rs` default entry plus `tools/isolated_workspace.rs` handler |
| `tools/workflow/*.rs` | `registry.rs` default entry plus `tools/workflow.rs` handler |
| `tools/subagent/*.rs` | `registry.rs` default entry plus `tools/subagent.rs` handler |
| `tools/submission/<family>/*.rs` | `registry.rs` default entry plus `tools/submission/<family>.rs` handler (subfolder kept) |
| `tools/skills/*.rs` | `registry.rs` default entry plus `tools/skills.rs` handler |
| `tools/ask_helper/*.rs` | `registry.rs` default entry plus `tools/ask_advisor.rs` handler |
| `tools/terminal.rs` | `tools/terminal.rs` |
| `registry/config.rs` (markdown tool-config loader) | `config.rs` |
| `registry/spec.rs` (`ToolSpec` schema helpers) | `config.rs` |

## `eos-tool-ports` Ownership Split

Do not dump every old `eos-tool-ports` item into `eos-tool`. Move each contract
to the crate that owns its behavior or to an owner-neutral contract module.

| Current item family | Target owner |
| --- | --- |
| `ToolError` | `eos-tool/error.rs` |
| `ToolName`, `ToolKey`, `ToolIntent`, `ExecutionMetadata`, `OutputShape`, `ToolResult` | `eos-tool/model.rs` |
| `ToolRegistry`, `RegisteredTool`, `ToolExecutor`, `ToolRuntime` | `eos-tool/registry.rs` |
| `Hook` | `eos-tool/hooks.rs` |
| `HookOutcome` | engine-private hook execution internals; not exported by `eos-tool` |
| `PlannerPlan`, `PlanTask`, `PlanReducer`, `SubmissionAck` | owner-neutral workflow submission contracts, not concrete tool behavior |
| `AttemptSubmissionPort` | workflow submission contract implemented by `eos-workflow`; consumed by `eos-tool` |
| `CancelPort` | cancellation contract owned by the lifecycle/cancellation phase, not by concrete tools |
| `SystemNotification`, `NotificationSink`, background-session count/status DTOs | engine/background contracts unless a passive DTO must move to `eos-types` |

The agent-launch contracts (`AgentType`, `AgentName`, `AgentRunApi`,
`SpawnAgentRequest`, `AgentRunRecordKind`, `WorkflowTaskRole`) are **not**
`eos-tool-ports` items; they arrive from `eos-types` via the Phase 02 contract
floor (the `eos-agent-ports` split). `tools/subagent.rs` and
`tools/ask_advisor.rs` consume them to build spawn requests and select the
record kind. `eos-tool` adds no launch-class types of its own, performs no
`AgentType` validation, and references the `AgentType` launch axis only — it does
not consume the `AgentRole` behavioral axis, which Phase 02 retires.

## Runtime Rules

`eos-tool` should not export `*Service` types. It exports a small runtime
struct passed into registry construction and captured by concrete tools.

The first target uses `ToolRuntime` in `registry.rs`; it does not create
`runtime.rs`, `resources.rs`, `handles.rs`, or `services.rs`.

Allowed `ToolRuntime` fields:

| Resource | Built by | Used by |
| --- | --- | --- |
| sandbox resource | `eos-agent-core` | `tools/sandbox.rs`, `tools/isolated_workspace.rs` |
| command-session resource | `eos-engine` | `tools/command.rs` |
| workflow resource (workflow API + workflow-session registration) | `eos-agent-core` | `tools/workflow.rs` |
| agent-launch resource (`AgentRunApi`) | `eos-agent-core` | `tools/subagent.rs` (`record_kind = Subagent`), `tools/ask_advisor.rs` (`record_kind = Advisor`) |
| subagent-session registry (background tracking only) | `eos-agent-core` | `tools/subagent.rs` |
| submission resource (root + attempt submission) | `eos-agent-core`, `eos-agent-run` if needed | `tools/submission/` |
| skill resource | `eos-agent-core` | `tools/skills.rs` |
| hook-resource bundle (sandbox + workflow + subagent subset) | `eos-agent-core` | stamped onto each `RegisteredTool`'s hook field |

`run_subagent` and `ask_advisor` share one injected `AgentRunApi` handle (the
`agent-launch resource`); they differ only in the `AgentRunRecordKind`
they stamp (`Subagent` vs `Advisor`), which `eos-agent-run` maps to the required
`AgentType` (`subagent` / `advisor`) and validates against the spawned profile.
`eos-tool` owns no launch-class policy: it never matches on `AgentType`, it only
sets the record kind. The subagent-session registry stays a separate field
because a subagent is a tracked background run while an advisor is
spawned-and-awaited. The `hook-resource bundle` is a strict subset of the rows
above (see the hook rule below).

Each field is an injected handle whose **trait is defined in `eos-tool`** and
whose **concrete impl is built at the `eos-agent-core` composition root**, then
passed into the engine via `AgentLoopExecutionRequest`. This keeps the graph
acyclic:

- `eos-engine` may build the `command-session resource` because it already
  depends on `eos-tool` (`eos-engine -> eos-tool`).
- The `workflow` and `agent-launch` resources must **not** be built by
  `eos-engine`. `eos-engine` consumes `dyn WorkflowApi` and `dyn AgentRunApi`
  from `eos-types` and has no crate edge to the concrete `eos-workflow` or
  `eos-agent-run` crates. Building either impl would require such an edge:
  `eos-agent-run -> eos-engine` already exists, so an `eos-engine ->
  eos-agent-run` edge would close a cycle, and `eos-workflow` is simply
  unreachable from the engine. Only `eos-agent-core`, which depends on every
  domain crate, may build them.
- Concrete tools and engine hooks invoke these handles only through the
  `eos-tool`-defined trait in `ToolRuntime`; they never gain a crate dependency
  on `eos-workflow` or `eos-agent-run`.

Hook *declarations* live in `hooks.rs`. Hook *execution* — the pipeline and
`HookOutcome` — lives in `eos-engine`. But the stateful pre-hooks
(`RequireNoBackgroundSessions`, `DisallowNestedPlannerDeferral`) read runtime
resources at hook time: sandbox transport, workflow API, and subagent sessions,
held today by `RegisteredTool`'s hook-resource field. Those resources are a
subset of `ToolRuntime`'s fields and must be folded into `ToolRuntime`, then
stamped onto each `RegisteredTool` by the registry. They are **not**
engine-owned: `eos-engine` has no edge to `eos-workflow`, so it cannot build the
workflow API itself — the same acyclic constraint that governs `ToolRuntime`.
Only the pure policy/pattern state (the destructive-git/shell regexes, the
isolated-mode check) is engine-private; the injected handles are not.

Rejected `Service` names:

| Pattern | Replacement |
| --- | --- |
| private tool executor resource group | `ToolRuntime` |
| static registry config holder | `ToolRegistry` default entries |
| hook-only injected resources | `ToolRuntime` hook-resource bundle |
| hook-only policy/pattern state | engine-private hook policy state |
| test-only helper | test fixture name |

## Public Surface

Target `lib.rs` exports only:

```rust
pub use config::{ToolConfig, ToolConfigError, ToolConfigSet};
pub use error::ToolError;
pub use hooks::Hook;
pub use model::{ExecutionMetadata, OutputShape, ToolIntent, ToolKey, ToolName, ToolResult};
pub use registry::{
    build_default_registry, CallerScope, RegisteredTool, ToolExecutor, ToolRegistry, ToolRuntime,
};
pub use tools::terminal::{render_tool_instruction, TerminalTool, ToolInstructions};
```

The exact names may change during implementation, but the surface must stay
small and owner-accurate. `HookOutcome` is not public `eos-tool` API.

`ToolConfigSet`/`ToolConfig`/`ToolConfigError` and the `terminal` descriptors are
restored here because `eos-agent-core` and the request runtime consume them
today; dropping them silently narrows load-bearing API. The previous 11-argument
`build_default_registry_with_services` entry point is **replaced** by
`build_default_registry(config, caller, runtime)` taking one `ToolRuntime` — that
collapse is the purpose of `ToolRuntime`, not an incidental rename.

## Progress Tracker

| Item | Status |
| --- | --- |
| Confirm Phase 02 handoff has created or renamed the `eos-tool` crate | Not started |
| Move tool-owned `eos-tool-ports` contracts into `error.rs`, `model.rs`, `registry.rs`, and `hooks.rs` | Not started |
| Route non-tool `eos-tool-ports` contracts to owner crates or owner-neutral contract modules | Not started |
| Fold registry, executor trait, and default tool registration into `registry.rs` | Not started |
| Move hook declarations into `eos-tool/hooks.rs` and keep hook execution in `eos-engine` | Not started |
| Move the markdown tool-config loader and `ToolSpec` schema helpers into `config.rs` | Not started |
| Move concrete tool behavior into `tools/` family modules | Not started |
| Keep `tools/submission/` as a per-family subfolder | Not started |
| Define `ToolRuntime` in `registry.rs`, including the shared agent-launch and hook-resource fields | Not started |
| Collapse sandbox file/edit tools into `tools/sandbox.rs` | Not started |
| Collapse shell/session tools into `tools/command.rs` | Not started |
| Collapse isolated-workspace tools into `tools/isolated_workspace.rs` | Not started |
| Collapse workflow/subagent/submission files | Not started |
| Move advisor helper behavior into `tools/ask_advisor.rs` | Not started |
| Collapse skill tool files | Not started |
| Remove obsolete one-file-per-tool deep tree | Not started |
| Update engine and agent-core imports through the Phase 02 integration lane | Not started |

## Acceptance Criteria

- `eos-tool` has `tools.rs` and family-level `tools/` modules.
- `eos-tool` has `hooks.rs`.
- `eos-tool` has `config.rs` owning the externalized tool-config loader and the
  `ToolSpec` schema helpers; `registry.rs` does not absorb the loader.
- `eos-tool` has no first-target `catalog.rs`, `executor.rs`, `runtime.rs`,
  `resources.rs`, `handles.rs`, `services.rs`, `services/`, or `hooks/` folder.
- `tools/submission/` is a per-family subfolder (root, planner, generator,
  reducer, subagent, advisor, shared `lib`); it is not flattened into one
  `tools/submission.rs`.
- `eos-tool` has no one-file-per-tool-command module tree.
- `eos-tool` exports no `*Service` types.
- Private resource groups are fields on `ToolRuntime`, not `Service`.
- `HookOutcome` is not exported from `eos-tool`.
- Hook *declarations* live in `eos-tool/hooks.rs`; hook *execution* (the pipeline
  and `HookOutcome`) lives in `eos-engine/tool_call`. Phase 04 confirms this
  split (its `tool_call.rs` owns "execution glue" and it states `eos-engine` does
  not own hook *contracts*); the two specs must use "hook contracts/declarations"
  for `eos-tool` and "hook execution" for `eos-engine` consistently.
- The stateful pre-hooks' injected resources (sandbox/workflow/subagent) are
  carried by `ToolRuntime` and stamped onto `RegisteredTool`, not built by
  `eos-engine`.
- `tools/command.rs` owns `exec_command`, `write_stdin`, and
  `read_command_progress`.
- `tools/isolated_workspace.rs` owns `enter_isolated_workspace` and
  `exit_isolated_workspace`.
- `tools/ask_advisor.rs` owns `ask_advisor` and advisor prompt/result behavior;
  the name disambiguates it from `tools/submission/advisor.rs`
  (`submit_advisor_feedback`).
- `tools/subagent.rs` and `tools/ask_advisor.rs` share one injected `AgentRunApi`
  handle and differ only in the `AgentRunRecordKind` they stamp
  (`Subagent` / `Advisor`); `ToolRuntime` does not carry two separate
  `AgentRunApi` fields.
- `eos-tool` consumes `AgentType`, `AgentName`, `AgentRunApi`, `SpawnAgentRequest`,
  and `AgentRunRecordKind` from `eos-types`; it performs no `AgentType`
  validation (that stays in `eos-agent-run`) and references the `AgentType`
  launch axis only, never the retired `AgentRole` axis.
- `eos-engine` imports tool framework contracts from `eos-tool`.
- `eos-tool` has no dependency on `eos-engine`.
- `eos-agent-core` builds `ToolRuntime` through `eos-tool`.
- Non-tool contracts from the old `eos-tool-ports` crate are not hidden in
  `eos-tool` solely to avoid dependency-DAG decisions.
- `cargo test -p eos-tool` passes.
- `cargo check -p eos-engine --all-targets` and
  `cargo check -p eos-agent-core --all-targets` pass after import updates.
- `eos-tool` final module count is at or below 22, counted net of the two
  documented justified splits (`config.rs`, `tools/submission/`). The prior `16`
  target assumed no splits; it is raised, not relaxed — the discipline is "no
  empty-justification splits," not an arbitrary cap that forces an 800-1000 LOC
  file the repo treats as a review smell.
