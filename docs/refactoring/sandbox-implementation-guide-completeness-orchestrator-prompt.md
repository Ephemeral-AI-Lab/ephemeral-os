# Sandbox Implementation Guide Completeness Orchestrator Prompt

Use this prompt to launch a review-only, multi-agent completeness audit of:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/refactoring/sandbox-implementation-guide.md
```

```text
You are the review orchestrator for the EphemeralOS sandbox refactor guide.

Working directory:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Primary artifact under review:

docs/refactoring/sandbox-implementation-guide.md

Task:

Launch subagents to determine whether `sandbox-implementation-guide.md` is a
complete, internally consistent, implementation-ready guide for the sandbox
protocol / manager / gateway CLI / daemon / runtime refactor.

This is a review-only completeness audit. Do not edit files. Do not apply
patches. Do not refactor code. Running read-only inspections, `rg`, `find`,
`cargo metadata`, `cargo tree`, `cargo check`, and targeted `cargo test`
commands is allowed when useful, but the final output must be findings and
recommendations only.

The audit must distinguish three categories:

1. Guide incompleteness or contradiction.
2. Live implementation drift relative to the guide.
3. Historical or transitional references that are acceptable because they
   describe earlier phases.

Do not report stale names in phase 0-8 historical prompt files as bugs unless
they are used as active final-state instructions.

Common context every subagent must read first:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-manager-daemon-split.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-runtime.md
- docs/refactoring/sandbox-daemon.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-gateway-cli.md
- docs/refactoring/sandbox-phase-8-runtime-support-rename-prompt.md
- docs/refactoring/sandbox-phase-9-compatibility-cleanup-prompt.md
- Cargo.toml
- README.md
- config/README.md

Common baseline commands:

```sh
git status --short --untracked-files=all
find crates -maxdepth 3 -name Cargo.toml -print | sort
cargo metadata --no-deps --format-version 1 > /tmp/eos-guide-completeness-metadata.json
```

Subagent guardrails:

- Use exact file:line evidence for every finding.
- Findings must be about guide completeness, correctness, or implementability.
- Do not invent missing requirements. If evidence is weak, say so and skip.
- Do not require cleanup of intentionally historical text in earlier phase
  prompts.
- Do not treat ordinary English words such as command, workspace, daemon,
  config, client, exec, poll, or cancel as stale unless they are active package
  names, operation names, CLI names, file/module names, or final-state docs.
- Preserve the agreed naming model:
  - `sandbox-protocol`
  - `sandbox-manager`
  - `sandbox-gateway-cli`
  - `sandbox-daemon`
  - `sandbox-runtime`
  - `sandbox-runtime-command`
  - `sandbox-runtime-workspace`
  - `sandbox-runtime-namespace-process`
  - `sandbox-runtime-layerstack`
  - `sandbox-runtime-overlay`
  - `sandbox-runtime-config`
- Preserve the agreed request/response model:
  - `Request`
  - `Response`
  - `OperationExecutionSpace`
  - `operation_execution_space`
  - `command_session_id`
- Preserve the agreed runtime operation names:
  - `exec_command`
  - `write_command_stdin`
  - `poll_command`
  - `read_command_lines`
  - `cancel_command`

Launch these subagents in parallel.

## Subagent 1: Phase Coverage And Prompt Linkage

Review whether the implementation guide has complete phase coverage and whether
each phase has enough information to be executed or reviewed.

Focus:

- Phase 0 through phase 9 are all present.
- Each phase has a goal, package scope, implementation steps, resulting or final
  folder structure when relevant, verification commands, and exit criteria.
- Phase prompt links exist and point to existing files for phases 1-9.
- The guide clearly says when support packages move and avoids moving them too
  early.
- Phase 8 and phase 9 instructions do not contradict their standalone prompt
  files.
- The package-order section matches the later phase sections.

Suggested commands:

```sh
rg -n "^## Phase|^Prompt:|Goal:|Implementation steps:|Verification:|Exit criteria:|Final verification:|Final folder structure|Resulting folder structure" docs/refactoring/sandbox-implementation-guide.md
for f in docs/refactoring/sandbox-phase-{1,2,3,4,5,6,7,8,9}*.md; do test -f "$f" && echo "exists $f"; done
rg -n "docs/refactoring/sandbox-phase" docs/refactoring/sandbox-implementation-guide.md
```

Return:

- Missing guide sections.
- Missing prompt files or broken references.
- Phase-to-phase contradictions.
- Any guide steps too vague to implement safely.

## Subagent 2: Cargo Package Graph And Folder Shape

Review whether the guide's package/folder claims match the live workspace and
the intended final shape.

Focus:

- Root workspace members and `[workspace.dependencies]`.
- Actual package names from `cargo metadata`.
- Actual folder shape under `crates/`.
- Whether `crates/daemon` is absent, empty, or still active.
- Whether the guide still presents old internal support package names as final
  package names.
- Whether phase 9 exit criteria are strong enough to catch stale package graph
  issues.

Suggested commands:

```sh
find crates -maxdepth 3 -name Cargo.toml -print | sort
rg -n 'members = \\[|workspace.dependencies|sandbox-runtime|daemon_rpc_protocol|daemon_operation|crates/daemon|name = "(command|workspace|namespace-process|layerstack|overlay|config)"' Cargo.toml crates --glob 'Cargo.toml'
cargo metadata --no-deps --format-version 1 > /tmp/eos-guide-completeness-metadata.json
cargo tree -p sandbox-runtime --prefix depth
cargo tree -p sandbox-daemon --prefix depth
```

Return:

- Final-shape mismatches.
- Missing package graph checks in the guide.
- Old package names that the guide should require removing from active
  workspace metadata.
- Any direct dependency direction that contradicts the crate specs.

## Subagent 3: Protocol, Catalog, And Operation Contract

Review whether the guide fully captures the public protocol, catalog/manual,
and operation naming contracts for agents, CLI, manager, and runtime.

Focus:

- `Request` and `Response` are the unified DTOs.
- `OperationExecutionSpace` and `operation_execution_space` are the only
  manager-vs-runtime selector.
- `OperationFamily` remains documentation grouping only.
- Manager and runtime catalogs are separate.
- Gateway/manual rendering is generated from `OperationSpec` / catalog data.
- Runtime operation names use the explicit command names.
- `command_session_id` is used instead of `command_id`.
- The guide does not reintroduce `OperationRequest`, `OperationResponse`,
  `SandboxRequest`, routed wrappers, or owner/target fields.

Suggested commands:

```sh
rg -n "Request|Response|OperationExecutionSpace|operation_execution_space|OperationFamily|OperationSpec|command_session_id|command_id|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget" docs/refactoring/sandbox-implementation-guide.md docs/refactoring/sandbox-protocol.md docs/refactoring/sandbox-manager.md docs/refactoring/sandbox-gateway-cli.md crates/sandbox-protocol crates/sandbox-manager crates/sandbox-gateway-cli crates/sandbox-runtime/operation --glob '!target/**'
rg -n 'name: "(exec|poll|cancel)"|"exec"|"poll"|"cancel"' crates/sandbox-runtime/operation crates/sandbox-manager crates/sandbox-gateway-cli docs/refactoring/sandbox-implementation-guide.md --glob '!target/**'
```

Return:

- Missing protocol/catalog rules.
- Guide text that conflicts with active protocol code.
- Any old DTO or selector names still treated as active.
- Any operation-name ambiguity that would confuse agents or CLI users.

## Subagent 4: Runtime Facade And Support-Crate Boundaries

Review whether the guide completely captures the intended `sandbox-runtime`
facade and runtime support package split.

Focus:

- `crates/sandbox-runtime/operation` package is named `sandbox-runtime`.
- Support packages remain separate and are not collapsed into the facade.
- `sandbox-runtime-command` owns process, PTY, transcript, process group, and
  command runner request construction.
- `sandbox-runtime-workspace` owns workspace lifecycle and workspace-level
  overlay planning.
- `sandbox-runtime-namespace-process` owns `ns-holder`, `ns-runner`, setns, and
  namespace-local mount/remount behavior.
- `sandbox-runtime-overlay` remains a shared low-level mount primitive used by
  workspace and namespace-process where needed.
- `sandbox-runtime-layerstack` and `sandbox-runtime-config` remain lower-level
  support crates.
- `command-request.json` remains until an explicit replacement transport
  exists.

Suggested commands:

```sh
rg -n "sandbox-runtime-command|sandbox-runtime-workspace|sandbox-runtime-namespace-process|sandbox-runtime-layerstack|sandbox-runtime-overlay|sandbox-runtime-config|command-request\\.json|mount_overlay|mount_overlay_legacy|move_mountpoint|unmount_overlay" docs/refactoring/sandbox-implementation-guide.md docs/refactoring/sandbox-runtime.md crates/sandbox-runtime --glob '!target/**'
cargo tree -p sandbox-runtime-command --prefix depth
cargo tree -p sandbox-runtime-workspace --prefix depth
cargo tree -p sandbox-runtime-namespace-process --prefix depth
cargo tree -p sandbox-runtime-layerstack --prefix depth
cargo tree -p sandbox-runtime-overlay --prefix depth
cargo tree -p sandbox-runtime-config --prefix depth
```

Return:

- Missing or contradictory support-crate ownership rules.
- Any guide wording that would move overlay wholly under workspace.
- Any dependency direction the guide should state more explicitly.
- Any live boundary drift that should be reflected in the guide.

## Subagent 5: Manager, Daemon, Gateway CLI, And Routing Boundary

Review whether the guide completely explains and constrains the manager,
daemon, gateway CLI, and forwarding split.

Focus:

- `sandbox-manager` is the host-side control plane.
- `sandbox-daemon` is the in-sandbox runtime endpoint.
- `sandbox-gateway-cli` talks to the manager over `sandbox-protocol`; it is not
  a hidden manager and does not directly link runtime implementation crates.
- Manager may route sandbox-scoped runtime requests to the daemon but does not
  implement runtime operations.
- `create_sandbox --sandbox-id` returns the container id / sandbox id as
  specified by current manager DTOs.
- Runtime operations are selected through the runtime execution space from the
  caller perspective, not through `OperationFamily`.
- The guide makes the manager-vs-runtime split easy for future agents to use.

Suggested commands:

```sh
rg -n "sandbox-manager|sandbox-daemon|sandbox-gateway-cli|forward|SandboxDaemonClient|describe_daemon_operations|describe_manager_operations|create_sandbox|OperationExecutionSpace|operation_execution_space|OperationFamily" docs/refactoring/sandbox-implementation-guide.md docs/refactoring/sandbox-manager.md docs/refactoring/sandbox-daemon.md docs/refactoring/sandbox-gateway-cli.md crates/sandbox-manager crates/sandbox-gateway-cli crates/sandbox-daemon --glob '!target/**'
cargo tree -p sandbox-manager --prefix depth
cargo tree -p sandbox-gateway-cli --prefix depth
cargo tree -p sandbox-daemon --prefix depth
```

Return:

- Missing caller-facing flow explanation.
- Boundary violations or guide gaps around forwarding.
- Any mismatch between the guide and live manager/gateway/daemon APIs.
- Any terms that are too internal or ambiguous for future agents.

## Subagent 6: Compatibility Cleanup, Packaging, And Active Docs

Review whether phase 9 is complete enough to finish stale-name cleanup and
packaging migration without damaging historical docs or live compatibility
paths.

Focus:

- README and config docs should describe the final package structure.
- Active docs should not present `daemon_operation`, `daemon_rpc_protocol`, or
  `crates/daemon/*` as live final-state paths.
- Packaging should migrate primary artifacts from legacy `eosd-linux-*` to
  `sandbox-daemon-linux-*`, or explicitly document any temporary alias.
- `docs/README/daemon/daemon_operation.md` should not remain an active README
  for the live runtime boundary after phase 9.
- Stale-name scans exclude historical phase prompt files but include active
  docs.
- Cleanup instructions avoid deleting live compatibility paths without
  call-site/test evidence.

Suggested commands:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon/(rpc_protocol|operation|server|command|workspace|namespace-process|layerstack|overlay|config)|eosd|eosd-linux|sandbox-daemon-linux|sandbox-runtime[-_]operation|sandbox_runtime[_]operation" README.md config docs xtask crates --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "mount_overlay_legacy|compat|legacy|alias" crates docs README.md config xtask --glob '!target/**'
cargo run -p xtask -- help
cargo check -p xtask
```

Return:

- Missing cleanup requirements in the guide.
- Active stale docs or packaging names the guide should explicitly catch.
- Any over-broad cleanup instruction that could delete live compatibility code.
- Any final verification missing from phase 9.

## Orchestrator Synthesis

After subagents report, produce one consolidated review.

Report format:

```text
Guide Completeness Review

Blocking Findings

1. [P0/P1] Title
   Evidence:
   - file:line ...
   - file:line ...
   Why this means the guide is incomplete:
   Recommendation:

Non-Blocking Findings

1. [P2/P3] Title
   Evidence:
   - file:line ...
   Why this matters:
   Recommendation:

Implementation Drift Not Guide Bugs

- file:line evidence and why this is implementation/docs drift rather than a
  guide defect.

Acceptable Historical References

- file:line evidence and why no guide change is needed.

Subagent Coverage

- Phase coverage/linkage: pass/fail, key evidence.
- Cargo/package graph: pass/fail, key evidence.
- Protocol/catalog/operation contract: pass/fail, key evidence.
- Runtime boundaries: pass/fail, key evidence.
- Manager/daemon/gateway routing: pass/fail, key evidence.
- Compatibility/packaging/docs: pass/fail, key evidence.

Final Recommendation

- Either: "The guide is complete enough to drive implementation."
- Or: "The guide needs these exact updates before it is safe to use."
```

Severity rules:

- P0: The guide would direct implementers to the wrong architecture or a broken
  package graph.
- P1: The guide omits a required phase, package boundary, verification step, or
  public contract needed to complete the refactor safely.
- P2: The guide is implementable but likely to cause confusion, duplicated work,
  or incomplete cleanup.
- P3: Minor wording, formatting, or optional clarity improvement.

Final constraints:

- Findings first. Keep summary short.
- Do not include more than 10 total findings unless there are multiple P0/P1
  issues.
- Every finding must cite exact file:line evidence.
- Clearly separate guide defects from implementation drift.
- If all subagents find no guide defects, say that clearly and list remaining
  implementation verification gaps, if any.
```
