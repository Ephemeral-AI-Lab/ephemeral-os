# Review — phase-03-eos-tool_SPEC.md

Reviewer pass: 2026-06-09. Grounded against the real `eos-tools` / `eos-tool-ports`
source and against phase-00 (lock), phase-02 (DAG), phase-04 (engine).

## Verdict

Yes — it produces a **materially healthier** `eos-tool`, but not yet the *much*
healthier one it is one step away from. The two structural wins are real and
correct: collapsing **10 `*Service` structs + the 11-argument
`build_default_registry_with_services`** into one `ToolRuntime` (a direction
phase-00 already locks), and collapsing **51 one-file-per-tool modules → ~22
family modules**. What keeps it at "healthier" rather than "much healthier":
(1) it **silently diverges from the phase-00–locked file tree** without an
amendment, and (2) it **stops short of the deeper simplifications it sets up** —
leaving a defensive `Option`/`MissingPort` layer intact and two genuine
twin-pair executors uncollapsed, while over-justifying the `submission/`
subfolder on an inflated LOC figure.

## 1. Healthier shape? — the structural wins (real)

| Dimension | Today | phase-03 target | Verdict |
| --- | --- | --- | --- |
| Tool dependency injection | **10 `*Service` structs** across two `services.rs` (eos-tools 203 LOC + eos-tool-ports 323 LOC) | one `ToolRuntime` in `registry.rs` | **Strong win**, locked by phase-00 (lines 168, 282) |
| Registry builder | 2-arg static + **11-arg** `…_with_services` | `build_default_registry(config, caller, runtime)` | **Strong win** — the collapse is the point of `ToolRuntime` |
| Concrete tools | 51 modules, one file per command, deep `tools/<family>/<cmd>.rs` tree | family handlers `tools/<family>.rs` | **Right direction** |
| `eos-tool-ports` crate | separate "ports" crate (misleading vocabulary) | folded; contracts routed to real owners | **Win**, matches the lock's retired-crate list |
| Hook contracts vs execution | mixed | declarations in `hooks.rs`, execution + `HookOutcome` stay in engine | **Clean split**, agrees with phase-04 in substance |

The `eos-tool-ports` ownership-split table (spec lines 166–190) is the part most
worth praising: it refuses to dump every old port into `eos-tool` and instead
routes `HookOutcome` → engine-private, `CancelPort` → lifecycle phase,
`AttemptSubmissionPort` → `eos-workflow`, launch contracts → `eos-types`. That is
exactly the "no compatibility-shim dumping" discipline the repo asks for.

## 2. SRP / boundaries / Rust best practice

| Area | Assessment |
| --- | --- |
| `ToolRuntime` SRP | Coherent **as a composition-root bundle** (phase-00 sanctions "Runtime = local object graph"). Caveat: keep each `tools/<family>.rs` `register()` taking the **narrow slice** it needs — today `sandbox::register` already takes only sandbox+command services. Don't pass the whole bundle into every family. |
| Acyclic DAG argument (lines 228–252) | **Confirmed correct** by the phase-00 locked DAG: `eos-engine → {types, tool, llm-client, sandbox-port}` (no workflow, no agent-run); `eos-agent-run → eos-engine`. So engine genuinely cannot build the workflow/agent-launch resources. The spec's reasoning here is sound and worth keeping verbatim. |
| Hook split | `hooks.rs` = declarations, engine = execution. phase-04 line 450 confirms "engine does not own hook contracts"; line 339 confirms "hooks in `eos-tool`". Substantively consistent. |
| `dyn` vs enum discipline | `ToolExecutor` stays `dyn` (open set of tools — correct). The closed sets (intent, hooks) stay enums — correct. |

**Remaining smells the spec does not address** (these are the Q3 openings):

| Smell | Where | Why it matters |
| --- | --- | --- |
| Defensive `Option` + `MissingPort` on every handle | `eos-tool-ports/services.rs` — every field `Option<…>`, every method `Err(MissingPort)` if absent | Violates the repo's "avoid defensive branches for impossible states." It exists only to serve the inert schema-snapshot path. |
| Closure callbacks instead of typed ports | `services.rs` uses `Arc<dyn Fn(..) -> BoxServiceFuture<..>>` + 7 type aliases | CLAUDE.md prefers typed ports/traits over closures. The spec says "trait defined in `eos-tool`" but is **hand-wavy** about converting these run-local callbacks. |
| Two over-stated equivalence claims | spec lines 213–216, 341–344 ("subagent/advisor differ only in record kind"); lines 128–136 (submission "keyed by `AgentRunRecordKind`") | Both are contradicted by the code (see §3). They make the design look more uniform than it is, which mis-sizes the collapse. |

## 3. Refactor more aggressively (the answer to Q3)

Grounded in the actual executor bodies. The spec keeps 6 submission files purely
on a "flattened ≈960 LOC" argument and never asks whether the *logic* is
distinct. It is not uniform — it is **two twin-pairs + two genuinely distinct
executors**:

| Aggressive move | Evidence | Impact | Confidence |
| --- | --- | --- | --- |
| **Collapse `generator` + `reducer`** into one enum-dispatched executor | `submit_generator_outcome.rs:34-83` vs `submit_reducer_outcome.rs:34-83` are line-for-line parallel; DTOs `GeneratorSubmission`/`ReducerSubmission` are field-for-field identical; deltas are the port method (`submit_generator` vs `apply_reducer`) + two label strings + one `terminal_tool_result`. | ~208 → ~90 LOC; closed-set enum `{Generator,Reducer}` — exactly the repo's "enum for a closed set" rule | **High** |
| **Optionally collapse `subagent` + `advisor`** (service-less, metadata-only terminals; no port call) | both = `parse → is_blank → ToolResult::ok(summary).with_metadata(…)`; differ only in DTO + metadata keys | 6 → 4 files; smaller win (each keeps a bespoke DTO + advisor's `Verdict` enum) | Medium |
| **Keep `planner` + `root` separate** — the split is *earned* here, not by LOC | planner: DAG validation + `PlanTask`/`PlanNodeId` construction (`submit_planner_outcome.rs:109-232`); root: 4 ownership guards + two-store CAS commit (`submit_root_outcome.rs:56-126`) | n/a — folding these into a god-executor would force conditional branches the repo norms reject | **High** |
| **Re-justify the `submission/` subfolder honestly** | the "≈960 LOC flattened" figure includes ~118 LOC of root's inline tests + ~400 LOC of planner/root distinct logic that must stay; only ~360 LOC across the 4 small files is collapsible boilerplate | the subfolder is still defensible **after** the 6→4 collapse, but the spec's stated reason overstates the duplication | — |
| **Split the inert path from the live path** → delete `Option`/`MissingPort` | the schema-snapshot/validation builder needs only `config` + schema, **not** executable handles; the live builder needs non-optional handles | removes the `Option<…>` on every field, the `MissingPort` variant, and the `InertSandboxTransport` shim | Medium-High |
| **Convert run-local callbacks → narrow object-safe traits** in `eos-tool` | replaces `BoxServiceFuture` + 7 `Arc<dyn Fn>` aliases with e.g. `SubagentSessionRegistry`, `CommandSessionRegistry` ports, impl'd at the composition root | aligns with CLAUDE.md typed-ports rule; tension: the impls must carry per-run state, so this is the one move to prototype before committing | Medium |
| **Fix the two over-stated claims** in the spec text | `run_subagent` is detached background launch (`run_subagent.rs:166-171`); `ask_advisor` is blocking `wait_for_agent_outcome` (`ask_advisor.rs:101-104`) — they share the `AgentRunApi` **port**, not a record-kind switch | doc-only, but it corrects the mental model that sizes the collapse | High |

**Net aggressive target:** submission 6 → 4 executor files (~120–150 LOC of
duplicated `execute` bodies removed), `Option`/`MissingPort` defensive layer
deleted, run-local closures promoted to typed ports. The sandbox file family
(`read`/`write`/`edit`) is the **counter-example that is already done right** —
it hoisted every shared concern into `sandbox/lib.rs` (`mutation_result`,
`edit_output`, `resolve_path`), leaving `write`/`edit` near-minimal and
`read_file` genuinely distinct. The submission family extracted only leaf
helpers and left the full `execute` bodies duplicated; closing that asymmetry is
the concrete win.

## 4. Doc-consistency fixes (supporting, not the headline)

| Issue | Detail | Fix |
| --- | --- | --- |
| **Lock divergence (not a stale-index nit)** | phase-00 (status **Accepted**) locks the `eos-tool` source shape at lines 146–164: flat 13 modules, **flat `submission.rs`**, **no `config.rs` / `isolated_workspace.rs` / `ask_advisor.rs`**. index.md matches phase-00 faithfully. **phase-03 diverges in 4 ways** and phase-00's amendment record only ratifies phase-02's DAG, never phase-03's tree. | phase-03's shape is **better on the merits** (a flat `registry.rs` would hit ~950 LOC; flat `submission.rs` ~960 — both repo review-smells). So **ratify phase-03's splits back into phase-00 + index.md** (exactly as phase-02's DAG was ratified back), rather than leaving three docs disagreeing. This needs a Phase-0 reopen per the lock's own rule. |
| Module budget 22 vs 23 | "Resulting File Structure" ASCII lists `submission/` with no shared `lib`, but prose (line 128) and acceptance criteria (line 320) require a shared `lib` → 23. | Add the shared `lib` to the ASCII (or drop the requirement) and state it once. |
| "≤ 22 **net of** the two splits" is undefined | unclear whether `config.rs` + `submission/` are excluded from the 22 count. | Pin the counting rule explicitly. |
| Stale cross-reference | phase-03 lines 326–330 say "Phase 04 confirms this split (its `tool_call.rs` owns 'execution glue')." phase-04 has **no `tool_call.rs`** — it uses `tool_dispatch/{mod,batch,execution}.rs`. Substance still agrees. | Update the filename to `tool_dispatch/execution.rs`. |
| Nested `mod.rs` tension | `tools/submission/mod.rs` is a nested `mod.rs` router; the index.md guardrail says "final target crates avoid nested `mod.rs` routing," and Module Collapse Plan line 159 says "tools/submission/<family>.rs handler (subfolder kept)" (sibling-file form). | Use `tools/submission.rs` + `tools/submission/<family>.rs` (the allowed `foo.rs` + `foo/` shape), not `submission/mod.rs`. |
| Naming drift | phase-03 line 184 uses `WorkflowTaskRole`; phase-00 uses `TaskRole`. | Use one name. |

## Process note

This review's deep submission/sandbox executor analysis came from a fan-out
workflow whose other investigator agents misbehaved (they emitted placeholder
`StructuredOutput` and never converged — the "stuck" symptom). The
executor-body agent and the cross-phase DAG/hook findings were salvaged; the
phase-00 lock check, runtime-shape analysis, and consistency audit were
completed directly against the primary sources cited above.
