# Agent Collaboration and Implementation Notes

This codebase is edited across multiple agent sessions at the same time. A dirty
worktree is usually expected and should be treated as parallel agent activity,
not as a reason to stop.

## Project Context

- Rust is the main implementation language and the default target for new
  backend behavior. Treat the top-level `agent-core/` and `sandbox/` Rust
  workspaces as the primary project roots.
- `agent-core/` owns the Rust agent control plane: runtime entry, task/workflow
  state, engine/query loop, tool framework, provider clients, sandbox host API,
  config, audit, skills, and plugin catalog.
- `sandbox/` owns the Rust sandbox substrate: `eosd`, daemon RPC/dispatch,
  command sessions, LayerStack, OCC, overlay execution, isolated workspaces,
  plugin PPC, terminal pairs, runner helpers, and the shared wire protocol.
- Use Cargo from the owning Rust workspace first (`cd agent-core && cargo ...`
  or `cd sandbox && cargo ...`). Keep dependencies in the workspace
  `Cargo.toml`, respect the existing Rust 2021 / `rust-version = "1.85"`
  contract, and follow the workspace lint posture.
- Python package metadata still lives in `pyproject.toml` for legacy backend
  code, tests, scripts, and migration glue. Use `uv` only when touching that
  Python surface.
- `backend/src` is the legacy Python backend during the Rust migration. It will
  be deprecated once the migration is complete; prefer the Rust implementation
  when behavior exists in both places unless the task explicitly asks for Python
  parity, migration glue, or a legacy backend fix.

## Codebase Memory And Architecture

Use `docs/architecture/index.html` as the maintained codebase-memory and
architecture bundle before making architecture-shaped changes. The root page
links the module pages for `docs/architecture/workflow`,
`docs/architecture/agent_loops`, `docs/architecture/tools`, and
`docs/architecture/sandbox`; those pages are the first stop for ownership,
workflow, invariants, diagnostics, and refresh triggers. Treat the older
standalone harness/context-engine HTML reference
as historical background and stale-claim comparison material; the maintained
cross-module map now lives under `docs/architecture`.

- Treat the code checkout as source truth and `docs/architecture` as the
  curated memory layer. For Rust-owned behavior, verify the current anchor in
  `agent-core/` or `sandbox/` even when an architecture page still lists
  `backend/src` evidence paths. When refreshing architecture docs, update the
  smallest affected page and convert stale Python evidence paths to Rust
  evidence paths instead of adding disconnected notes.
- Use this Rust ownership map before falling back to legacy Python:
  `agent-core/crates/eos-runtime` owns request bootstrap and root-agent entry;
  `eos-state` and `eos-db` own persisted Request/Task/Workflow/Iteration/Attempt
  state and stores; `eos-workflow` owns delegated workflow lifecycle, context
  packets, attempt orchestration, and plan DAG handling; `eos-engine` owns the
  query loop, provider stream handling, tool dispatch, background supervisor,
  and notifications; `eos-tools` owns tool definitions, terminal tools, hooks,
  and model-facing tool surfaces; `eos-sandbox-api` and `eos-sandbox-host` own
  host-side sandbox protocol, provisioning, and lifecycle; `sandbox/crates/*`
  owns the daemon/runtime substrate.
- Task is the persisted agent interface. A top-level request mints a root
  `Task(role=root, workflow_id=None)` and runs the root agent directly through
  `agent-core/crates/eos-runtime/src/entry.rs` and
  `agent-core/crates/eos-runtime/src/root_agent.rs`; the request finishes
  through `submit_root_outcome`. Delegated decomposition is launched by agents
  with the non-terminal `delegate_workflow` tool and persists Workflow ->
  Iteration -> Attempt state through `agent-core/crates/eos-workflow` plus
  `eos-state` / `eos-db`. Coordination still flows through persisted state,
  terminal submissions, context packets, and outcomes; do not introduce
  peer-to-peer agent communication or a global agent orchestrator. Each Attempt
  owns one planner-authored DAG of generator and reducer Task rows whose edges
  are `needs`. Stages are PLAN -> RUN -> CLOSED, and the reducer is the exit
  gate.
- `ContextEngine` builds role packets from store state for workflow agents only.
  Keep lifecycle policy in workflow handlers/managers, not hidden in context
  construction. The Rust context code lives under
  `agent-core/crates/eos-workflow/src/context`.
- Workflow state DTOs live in `agent-core/crates/eos-state`; SQL-backed stores
  live in `agent-core/crates/eos-db`; workflow lifecycle and outcomes are
  coordinated by `agent-core/crates/eos-workflow`. `WorkflowStarter::start`
  creates delegated workflow state from a running Task and leaves the parent
  Task running. Agents inspect or cancel the background handle with
  `check_workflow_status` / `cancel_workflow`, then submit their own terminal
  outcome. There is no synthetic root workflow, legacy waiting status, legacy
  delegation link column, or close-time parent mutation.
- `AttemptOrchestrator` is per-Attempt lifecycle machinery, not permission to
  add a global orchestration layer. Related launch, stage-advance, and close
  behavior lives under `agent-core/crates/eos-workflow/src/attempt`.
- The engine loop owns agent execution and terminal-tool enforcement.
  Successful terminal tools are stamped as terminating by
  `agent-core/crates/eos-tools`; dispatch and loop exit run through
  `agent-core/crates/eos-engine/src/tool_call/dispatch.rs` and
  `agent-core/crates/eos-engine/src/query/loop_.rs`. Terminal tools must be
  called alone; those terminal results are persisted task/workflow state inputs,
  not just user-facing messages. Background execution is an engine dispatch
  mode, not a provider-level persistent shell session.
- Sandbox is the tool-execution environment. Agents run outside the sandbox and
  call provider-backed sandbox APIs for file, shell, plugin, and workspace
  actions. The Rust host/API layer lives in
  `agent-core/crates/eos-sandbox-api` and
  `agent-core/crates/eos-sandbox-host`; the daemon and wire protocol live in
  `sandbox/crates/eos-daemon`, `sandbox/crates/eosd`, and
  `sandbox/crates/eos-protocol`. Rust sandbox config is Docker-only today; do
  not reintroduce Daytona or non-Docker provider branches unless the task is
  explicitly a legacy Python migration task.
- Workspace routing in Rust lives in `sandbox/crates/eos-daemon/src/dispatcher.rs`
  and the daemon command/plugin/isolated modules. Shared workspace `read_file`,
  `write_file`, and `edit_file` use daemon-owned LayerStack/OCC fast paths when
  a workspace binding exists. Shell, search, and plugin-style operations use
  the overlay pipeline; write-capable overlay results publish through OCC-gated
  paths. LayerStack/OCC services live in `sandbox/crates/eos-layerstack` and
  `sandbox/crates/eos-occ`; overlay and namespace execution live in
  `sandbox/crates/eos-overlay` and `sandbox/crates/eos-runner`.
- Isolated workspace mode is an explicit `enter_isolated_workspace` /
  `exit_isolated_workspace` lifecycle. It gives an agent a persistent private
  workspace for that isolated session through the active `agent_id` handle, not
  a separate public `isolated_workspace_id` routing parameter. Writes are
  captured and audited but not OCC-published; exit tears down the namespace,
  releases the snapshot lease, and removes scratch state. Enter rejects active
  sandbox-bound background work, exit cancels or drains it, and plugin/LSP
  operations are blocked while isolated mode is active for that agent. The Rust
  model tool lives in `agent-core/crates/eos-tools/src/model_tools/isolated.rs`;
  host lifecycle code lives in `agent-core/crates/eos-sandbox-host`; daemon ops
  live in `sandbox/crates/eos-daemon/src/isolated.rs`; core isolated lifecycle
  lives in `sandbox/crates/eos-isolated`. The architecture references are
  `docs/architecture/tools/isolated-workspace.html` and
  `docs/architecture/sandbox/workspaces.html`.

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

- Make the touched file's final implementation as small as the request allows.
  Prefer net-negative changes when existing code can be simplified or deleted,
  and use aggressive, transformative rewrites when they improve extensibility or
  implementation feasibility. Do not add speculative features, configuration,
  extension points, or abstractions.
- Treat LOC guidance as a final-code standard, not a net-diff target: if a file
  implements something in 200 lines that can be expressed clearly in 50, rewrite
  it toward the smaller shape.
- Keep `lib.rs`, `main.rs`, and `mod.rs` thin, usually under 100-200 LOC. Normal
  implementation modules should aim for 300-600 LOC when practical.
- Treat 800-1000+ LOC implementation files as a review smell: split when the
  file mixes multiple concepts, lifecycle phases, backends, DTOs, tests, or
  helper layers. Very large files are acceptable only when mechanically
  cohesive, such as generated code, big enum/table definitions, or tightly
  coupled parser/state-machine code.
- Do not enforce a hard file-size cap in this repo. The better standard is final
  files as small as the request allows, with splits following real ownership
  boundaries like `eos-engine`, `eos-workflow`, `eos-tools`, or sandbox modules,
  not arbitrary LOC.
- If the solution is growing large and a smaller design would solve the same
  problem, simplify before continuing.
- In Rust, prefer typed IDs, enums, DTOs, ports, and explicit dependency edges
  over stringly or ad hoc compatibility shims. Keep workspace dependency DAGs
  intentional; do not add cross-workspace back-edges or broad dependencies just
  to reach a helper.
- Avoid defensive branches for impossible states unless the surrounding codebase
  already requires that style.
- Match the existing code's style and ownership boundaries even when you would
  design greenfield code differently.

## Rust Best Practices

- Respect crate ownership before reaching for a helper. Put engine-loop,
  workflow-lifecycle, model-tool, sandbox-host, daemon, and protocol behavior in
  the crate that owns that surface, and do not add cross-workspace back-edges or
  broad dependencies to avoid a local design decision.
- Keep dependency edges explicit. Declare shared dependencies in the owning
  workspace `Cargo.toml`, consume them with `workspace = true`, and treat
  internal path dependencies as architecture edges that need a clear reason.
- Encode contracts in Rust types. Prefer typed IDs, enums, DTO structs, ports,
  and explicit state transitions over strings, bool flag bags, ad hoc JSON, or
  compatibility shims.
- Keep public APIs narrow. Use `pub(crate)` until another crate genuinely needs
  the item, keep `lib.rs` / `mod.rs` as routing and export surfaces, and document
  public invariants where the workspace enables missing-docs linting.
- Handle errors deliberately. Use `?` and error context instead of `unwrap` or
  `expect` in production code; use `thiserror` for library/domain errors and
  `anyhow` near binary, orchestration, or test edges where concrete error types
  add little value.
- Treat async boundaries as lifecycle boundaries. Do not hold locks across
  `.await`, avoid blocking work inside async tasks, use cancellation and bounded
  concurrency where background work can outlive a call, and make lock ordering
  explicit when multiple locks are required.
- Keep unsafe exceptional and local. `agent-core` code should remain
  `unsafe`-free; sandbox crates may use unsafe only where namespace, syscall, or
  FFI boundaries require it, with a tight safety invariant and safe wrapper.
- Treat wire, persisted-state, and serde DTOs as contracts. Field renames,
  defaults, versioning, and JSON shape changes need focused tests or golden
  coverage because they affect daemon protocol, audit, and database behavior.
- Use Rust conventions consistently: `iter` / `iter_mut` / `into_iter`,
  `as_` / `to_` / `into_` conversion names, `&str` / `&Path` / slices for
  borrowed inputs, owned values for cross-task state, and `tracing` rather than
  `println` or `dbg!` for diagnostics.
- Localized lint allows are acceptable only when they explain the invariant or
  compatibility reason. Prefer fixing the code over suppressing Clippy, and keep
  test-only `unwrap` / `expect` allowances scoped to tests or test helpers.

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
- For Rust-owned changes, prefer scoped Cargo verification from the owning
  workspace (`cargo check`, `cargo test -p <crate>`, targeted tests, then
  clippy when risk warrants it). Run Python checks only for legacy Python,
  parity, or migration-glue changes.
- Use a concrete Cargo check ladder for Rust changes: `cargo check -p <crate>
  --all-targets` for syntax/type sanity, `cargo test -p <crate> <targeted_test>`
  or `cargo test -p <crate>` for behavior, and `cargo clippy -p <crate>
  --all-targets -- -D warnings` for lint-sensitive changes. Broaden to
  `--workspace --all-targets` only when the change crosses crates or dependency
  edges, and report pre-existing workspace lint noise instead of hiding it with
  broad `allow` attributes.
- For multi-step tasks, keep a short plan with a verification step for each
  meaningful phase, then iterate until the criteria are met.
