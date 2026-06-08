# Phase 03 - eos-tool Spec

Status: Draft
Date: 2026-06-09
Owner: eos-tool

## Scope

This phase rebuilds `eos-tool` as the single owner of the tool framework and
concrete model-callable tool behavior.

It folds the current `eos-tool-ports` crate into `eos-tool` and collapses the
current one-file-per-tool command layout into family-level handlers.

## Local Architecture

`eos-tool` owns:

- tool names and keys,
- tool intent and output shape,
- execution metadata facts,
- tool result DTOs,
- registered tool entries,
- tool registry,
- tool executor trait,
- hook definitions and hook execution,
- concrete model-callable tool behavior,
- skill registry and skill package loading,
- sibling-facing tool services used by `eos-agent-core` and `eos-engine`.

`eos-tool` does not own:

- agent-loop turn control,
- model provider streaming,
- agent-run lifecycle rows,
- workflow state transitions,
- sandbox daemon protocol internals.

## Resulting File Structure

```text
agent-core/crates/eos-tool/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── error.rs
│   ├── model.rs
│   ├── catalog.rs
│   ├── registry.rs
│   ├── executor.rs
│   ├── hooks.rs
│   ├── hooks/
│   │   ├── background_sessions.rs
│   │   ├── workflow_depth.rs
│   │   └── sandbox_policy.rs
│   ├── tools.rs
│   ├── tools/
│   │   ├── sandbox.rs
│   │   ├── command.rs
│   │   ├── workflow.rs
│   │   ├── subagent.rs
│   │   ├── submission.rs
│   │   ├── skills.rs
│   │   ├── advisor.rs
│   │   └── terminal.rs
│   ├── services.rs
│   └── services/
│       ├── registry.rs
│       ├── sandbox.rs
│       ├── command_sessions.rs
│       ├── workflow.rs
│       ├── subagent.rs
│       ├── submission.rs
│       └── skills.rs
└── tests/
    ├── registry/
    ├── sandbox/
    ├── workflow/
    ├── subagent/
    ├── submission/
    └── skills/
```

No `builtins.rs` or `builtins/` folder is required in the first target. The
built-in tool set is closed and should be represented through `catalog.rs` plus
family handlers in `tools/`.

`services/` is not where concrete tools live. It contains only sibling-consumed
handle types such as sandbox, workflow, command-session, submission, and skill
handles.

## Module Collapse Plan

| Current pattern | Target |
| --- | --- |
| `tools/sandbox/exec_command.rs` | `catalog.rs` entry plus `tools/command.rs` handler |
| `tools/sandbox/read_file.rs` | `catalog.rs` entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/write_file.rs` | `catalog.rs` entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/edit_file.rs` | `catalog.rs` entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/multi_edit.rs` | `catalog.rs` entry plus `tools/sandbox.rs` handler |
| `tools/sandbox/write_stdin.rs` | `catalog.rs` entry plus `tools/command.rs` handler |
| `tools/workflow/*.rs` | `catalog.rs` entry plus `tools/workflow.rs` handler |
| `tools/subagent/*.rs` | `catalog.rs` entry plus `tools/subagent.rs` handler |
| `tools/submission/**/*.rs` | `catalog.rs` entry plus `tools/submission.rs` handler |
| `tools/skills/*.rs` | `catalog.rs` entry plus `tools/skills.rs` handler |
| `tools/ask_helper/*.rs` | `catalog.rs` entry plus `tools/advisor.rs` handler |
| `tools/terminal.rs` | `tools/terminal.rs` |

## Service Rules

Only sibling-consumed types may keep `Service` names.

Allowed `eos-tool` service surfaces:

| Service | Sibling consumers |
| --- | --- |
| `ToolRegistryService` or equivalent registry builder | `eos-agent-core`, `eos-engine` |
| `SandboxToolService` | `eos-agent-core` |
| `CommandSessionToolService` | `eos-engine` |
| `WorkflowToolService` | `eos-engine` |
| `SubagentToolService` | `eos-engine` |
| `SubmissionToolService` | `eos-agent-core`, `eos-agent-run` if needed |
| `SkillToolService` | `eos-agent-core` |

Rejected `Service` names:

| Pattern | Replacement |
| --- | --- |
| private tool executor resource group | `*Handles` |
| static registry config holder | `*Config` or `*Catalog` |
| hook-only private state | `HookHandles` |
| test-only helper | test fixture name |

## Public Surface

Target `lib.rs` exports only:

```rust
pub use error::ToolError;
pub use model::{ExecutionMetadata, ToolIntent, ToolKey, ToolName, ToolResult};
pub use registry::{RegisteredTool, ToolRegistry};
pub use executor::ToolExecutor;
pub use hooks::{Hook, HookOutcome};
pub use services::{
    CommandSessionToolService, SandboxToolService, SkillToolService,
    SubagentToolService, SubmissionToolService, ToolRegistryService,
    WorkflowToolService,
};
```

The exact names may change during implementation, but the surface must stay
small and owner-accurate.

## Progress Tracker

| Item | Status |
| --- | --- |
| Create `eos-tool` crate target or rename `eos-tools` | Not started |
| Fold `eos-tool-ports` model types | Not started |
| Fold registry and executor contracts | Not started |
| Move hooks into `eos-tool` | Not started |
| Split hook policy into `hooks/` family modules | Not started |
| Move concrete tool behavior into `tools/` family modules | Not started |
| Promote sibling-facing services to `services.rs` | Not started |
| Collapse sandbox command files | Not started |
| Collapse workflow/subagent/submission files | Not started |
| Collapse skill tool files | Not started |
| Remove obsolete one-file-per-tool deep tree | Not started |
| Update engine/api imports | Not started |

## Acceptance Criteria

- No `eos-tool-ports` crate remains.
- `eos-tool` has `tools.rs` and family-level `tools/` modules.
- `eos-tool` has `hooks.rs` and focused `hooks/` policy modules.
- `eos-tool` has no one-file-per-tool-command module tree.
- Every `Service` exported by `eos-tool` has at least one sibling-crate consumer.
- Private resource groups are named `Handles`, not `Service`.
- `eos-engine` imports tool framework contracts from `eos-tool`.
- `eos-agent-core` builds tool services through `eos-tool`.
- `cargo test -p eos-tool` passes.
- `cargo check -p eos-engine --all-targets` and
  `cargo check -p eos-agent-core --all-targets` pass after import updates.
- `eos-tool` final module count is at or below 32.
