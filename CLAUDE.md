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
- The legacy Python backend has been removed; `agent-core/` and `sandbox/` are
  the only implementation. There is no `backend/`, `pyproject.toml`, or `uv`
  surface to maintain.


## Parallel Agent Work

- Do not revert, overwrite, or discard another agent's work unless the user
  explicitly asks for that.
- If existing changes are outside the current plan, infer the likely intent from
  file names, diffs, tests, and surrounding code, then adjust your own plan
  around that work instead of blocking. Ask only when ambiguity makes safe
  progress impossible.
- Keep your edits scoped to your task, but integrate with concurrent changes
  when needed for correctness.
- Feel free to launch dynamic workflows and subagents for parallel execution,
  exploration, and redundancy checks when the scopes can stay disjoint; reconcile
  their findings before acting on or reporting results.
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
- Keep test-only modules and helpers under the owning test tree. Rust
  `tests.rs` files should live under the crate's `tests/` folder; when private
  module access is still required, reference them from the source module with a
  `#[path]` attribute pointing at `../tests/<module>/mod.rs`. Test setup,
  config, seam/fake/mock, fixture, and harness files belong under `tests/`
  unless they are shared production APIs.
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
- Treat Rust "OOP" as encapsulation plus composition plus trait-defined
  behavior, not class inheritance. Do not add abstract-base-style modules, deep
  trait hierarchies, `I*` interface names, or empty marker traits unless they
  encode a real invariant.
- Every Rust implementation or proposal that introduces a behavior boundary must
  name the dispatch strategy: concrete type or enum for a closed set, generics
  or `impl Trait` for compile-time polymorphism, and `dyn Trait` only for
  runtime-selected providers, plugins, test doubles, or other heterogeneous
  open sets. Traits intended for `dyn` use must stay object-safe.
- Keep generic bounds local and minimal. Prefer `where` clauses for readable
  multi-bound APIs, associated types when the trait owns output/resource types,
  and method-level bounds instead of constraining a whole struct or impl block
  for one operation.
- Design public APIs as narrow crate-owned contracts: typed DTOs, newtypes,
  state enums, ports, and explicit error types. Use `pub(crate)` internally,
  `pub use` for a clean public surface, sealed traits when callers should use
  but not implement a trait, and `#[non_exhaustive]` when versioning requires
  future fields or variants.
- Put dependency injection at real resource, provider, plugin, or cross-crate
  boundaries. Prefer concrete types inside a crate; introduce trait ports,
  generics, or trait objects only when substitution is load-bearing for tests,
  alternate backends, or runtime selection.
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

## Demonstration Guidance

- When explaining or demonstrating architecture, workflows, contracts, or
  migration plans, prefer a workflow diagram plus a comparison or grouping table
  over pure prose. Use prose to call out key caveats and evidence, not to replace
  the structured view.

## Verification

- Convert the request into concrete success criteria before or while
  implementing.
- For bugs, prefer a failing test or focused reproduction before the fix when
  practical.
- For refactors, preserve behavior and run the narrowest convincing checks before
  and after risky changes when practical.
- For Rust-owned changes, prefer scoped Cargo verification from the owning
  workspace (`cargo check`, `cargo test -p <crate>`, targeted tests, then
  clippy when risk warrants it).
- For default live E2E sandbox testing, use Docker with platform `linux/amd64`
  and image `sweevo-dask__dask-10042:latest` unless the task explicitly names a
  different image or architecture. Set the harness image variable, such as
  `EOS_LIVE_E2E_IMAGE`, to that image when a test entry point requires it.
- Use a concrete Cargo check ladder for Rust changes: `cargo check -p <crate>
  --all-targets` for syntax/type sanity, `cargo test -p <crate> <targeted_test>`
  or `cargo test -p <crate>` for behavior, and `cargo clippy -p <crate>
  --all-targets -- -D warnings` for lint-sensitive changes. Broaden to
  `--workspace --all-targets` only when the change crosses crates or dependency
  edges, and report pre-existing workspace lint noise instead of hiding it with
  broad `allow` attributes.
- For multi-step tasks, keep a short plan with a verification step for each
  meaningful phase, then iterate until the criteria are met.
