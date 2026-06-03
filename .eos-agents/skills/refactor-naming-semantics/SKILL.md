---
name: refactor-naming-semantics
description: "Review and refactor target code with an autonomous naming-semantics and Reduction-Focused Refactor loop. Use when auditing or renaming files, folders, modules, functions, classes, important variables, public import facades, implementation boundaries, dead code, duplicated logic, compatibility shims, vague helper layers, and speculative abstractions; launches parallel subagent audits/lanes by default for package-sized or multi-file targets, while preserving behavior unless an existing behavior is clearly a bug."
---

# Autonomous Naming + Reduction Refactor

Run a bounded autonomous refactor loop for target code. Keep behavior unchanged unless an existing behavior is clearly a bug. The primary focus is naming semantics, merged with the Reduction-Focused Refactor discipline of deleting redundant code before adding new structure.

Operate as the orchestrator: define the target boundary, choose the loop shape, launch subagents for parallelizable work, integrate results, run verification gates, and stop when the exit condition is met.

## Parallel Agent Mandate

Invoking this skill is a request to use the subagent orchestration described here. For any package-sized target, multi-file subsystem, broad folder, or target with more than one independent ownership area, the default loop is parallel-first:

- Launch at least two read-only subagents before choosing implementation scope: one semantic/import-contract lane and one reduction/deletion-evidence lane. Use more lanes when the target has clearly disjoint ownership areas.
- Launch write-capable worker subagents for non-overlapping cleanup lanes when deletion, rename, or test work can be bounded by explicit file ownership.
- Keep only the immediate critical-path task, public API decisions, overlapping files, integration, and final verification local.
- Do not silently downgrade a broad target to a local-only sequential pass. If subagent tooling is unavailable, blocked by active tool policy, or the user explicitly opts out, stop and report that blocker before shrinking scope.
- A sequential loop is allowed only for one focused file, one symbol family, one risky public facade, or a target where no independent read-only audit can materially advance the task.

## Resources

- `scripts/refactor_audit.py`: Run before non-trivial edits to inventory target files, symbols, suspect names, importer hints, test candidates, and large-file reduction opportunities.
- `scripts/refactor_verify.py`: Run after edits to execute the narrow verification commands and capture a concise pass/fail report.
- `scripts/refactor_loop_notes.py`: Use for longer loops to initialize and append an iteration note file that bridges context between passes and subagents.
- `scripts/render_subagent_prompt.py`: Render an agent prompt template with JSON or `--set key=value` values before spawning a subagent.
- `agents/semantic_cartographer.md`: Read-only explorer for naming semantics, ownership boundaries, and rename maps.
- `agents/import_contract_auditor.md`: Read-only explorer for importers, public facades, persisted contracts, and compatibility classifications.
- `agents/reduction_evidence_auditor.md`: Read-only explorer for deletion proof, redundancy, fallback, and simplification candidates.
- `agents/cleanup_lane_worker.md`: Write-capable worker for one disjoint cleanup work unit.
- `agents/desloppify_cleanup_worker.md`: Write-capable cleanup pass that removes slop introduced by an implementer.
- `agents/refactor_review_sentinel.md`: Read-only reviewer for correctness, semantics, compatibility, missed call sites, and verification gaps.
- `agents/verification_evidence_runner.md`: Read-only verifier for commands, first-failure classification, and evidence capture.
- `agents/integration_coordinator.md`: Orchestrator/integrator prompt for landing parallel lanes; usually keep this role local to the main agent.
- `references/naming_semantics.md`: Load before meaningful renames or when judging unclear file, module, class, function, status, lifecycle, or variable names.
- `references/reduction_focused_refactor.md`: Load before deleting compatibility paths, collapsing helpers, removing fallbacks, or reducing duplicated logic.
- `references/autonomous_refactor_orchestration.md`: Load before multi-pass, broad, or parallel refactors to choose the loop shape, quality gates, subagent lanes, and exit conditions.
- `references/self_verification_loop.md`: Load when checks fail, when a pass needs rework, or when the loop may need multiple iterations.

## Loop Selection

Choose the simplest loop that fits:

- **Sequential loop**: One focused file, one symbol family, or one risky public facade. Work locally through audit -> reduce -> rename -> verify -> report.
- **Subagent sidecar loop**: Independent analysis, importer discovery, verification, or review can run while the orchestrator edits locally.
- **Parallel subagent lane loop**: Multiple disjoint write scopes can be refactored in parallel. Each lane gets explicit ownership, invariants, and checks.
- **DAG loop**: Larger refactors with dependencies. Decompose into layers; run each layer sequentially, with independent units inside a layer in parallel.

Use subagents aggressively when the work is parallelizable, but keep orchestration, dependency ordering, final integration, and final verification local. For broad targets, launching parallel agents is required, not optional, unless blocked as described in the Parallel Agent Mandate.

## Autonomous Workflow

1. Establish the refactor contract.
   - Identify the exact files, folders, packages, symbols, or subsystem named by the user.
   - If the target boundary is ambiguous, stop and ask for the missing boundary before editing.
   - Define behavior invariants, public import paths, persisted formats, and compatibility paths that must remain stable.
   - Set an exit condition before editing. Default to `max_passes: 3` unless the user asks for a different limit.
   - Require a completion signal: no stale names/imports, no unjustified compatibility paths, and narrow checks passed or residual risk documented.

2. Build the baseline.
   - Inspect nearby architecture docs, package conventions, public facades, and current naming patterns before judging names.
   - Find current importers, references, tests, fixtures, mocks, docs, and public entry points with `rg`, LSP references, and local test naming conventions.
   - Run `python3 <skill>/scripts/refactor_audit.py <target...> --repo <repo> --out /tmp/refactor-audit.md` for non-trivial targets.
   - Initialize loop notes for longer work: `python3 <skill>/scripts/refactor_loop_notes.py --notes /tmp/refactor-loop-notes.md init --target "<target>" --checks "<checks>" --invariants "<invariants>"`.
   - Identify the narrowest relevant checks before changing code.

3. Decompose and delegate.
   - Load `references/autonomous_refactor_orchestration.md` before multi-file, multi-pass, or parallel work.
   - Build a dependency DAG: units with no file overlap and no code dependency can run in parallel; dependent or overlapping units must run sequentially or through an integration queue.
   - Define each work unit with `id`, `target_paths`, `allowed_edits`, `forbidden_paths`, `deps`, `file_overlap_group`, `public_contracts`, `acceptance`, `verification_commands`, `risk_tier`, and `handoff_file`.
   - For broad or multi-file targets, spawn read-only audit subagents before implementation scope is narrowed. Minimum first wave: semantic/import-contract audit plus reduction/deletion-evidence audit.
   - Spawn subagents for disjoint write scopes, independent importer/test discovery, naming/reduction audits, or reviewer/verifier passes.
   - Prefer the bundled templates in `agents/` over ad hoc prompts. Render them with `scripts/render_subagent_prompt.py` when the work-unit contract is non-trivial.
   - Keep tests with the implementation unit they validate. Do not split implementation and tests into independent parallel writers.
   - Give each subagent a bounded assignment: owned files/modules, forbidden files, behavior invariants, public paths to preserve, expected check command, and final report shape.
   - Tell every code-edit subagent it is not alone in the codebase, must not revert others' work, and must adapt to concurrent changes.
   - Keep overlapping files, public facade decisions, shared fixtures, generated registries, merge conflict resolution, and final verification with the orchestrator.

4. Reduce before reorganizing.
   - Load `references/reduction_focused_refactor.md` for deletion, simplification, compatibility removal, fallback removal, or helper consolidation.
   - Remove dead code, unused helpers, unnecessary parameters, duplicated branches, stale feature flags, speculative abstractions, and compatibility paths not required by real callers.
   - Treat backward-compatible aliases, migration bridges, legacy import paths, deprecated parameters, dual old/new code paths, fallback dispatchers, and compatibility shims as deletion candidates.
   - Keep compatibility only when a current public contract, external API, persisted data format, documented migration window, or active caller requires it.
   - If compatibility must stay, keep the public facade thin and move internal callers to the canonical path.

5. Rename after the shape is clear.
   - Load `references/naming_semantics.md` before meaningful renames.
   - Audit file, folder, module, function, class, method, and important variable names.
   - Rename names whose semantics are vague, misleading, overly generic, overloaded, or inconsistent with the surrounding architecture.
   - Prefer responsibility-based names that make ownership, workflow order, data direction, and domain meaning obvious from the path and symbol name.
   - Treat names like `utils`, `helpers`, `manager`, `handler`, `common`, `service`, `processor`, `data`, `state`, `status`, and generic lifecycle terms as smells unless they are already the clearest local convention.
   - Preserve stable public facades when required, but route internal imports to clearer internal names.

6. Run separate cleanup and review passes.
   - Review correctness, readability, boundaries, error handling, typing, and local consistency.
   - Prefer existing repo abstractions over new local wrappers.
   - Share logic only when the same policy, validation, transformation, or control flow appears in multiple places.
   - Create helper classes only when they own cohesive state or a real protocol that would be awkward as free functions.
   - Replace repeated string literals, loose status values, and lifecycle flags with typed contracts when the value set is closed and meaningful.
   - Do not introduce compatibility shims, aliases, generic adapters, or helper layers unless strictly necessary.
   - Use a reviewer subagent for non-trivial refactors after the implementer pass, especially when a subagent or prior pass authored the code.

7. Verify after every meaningful pass.
   - Run the narrow checks identified before editing.
   - Use `python3 <skill>/scripts/refactor_verify.py --cwd <repo> --command "<check>" --summary /tmp/refactor-verify.md` when a durable verification report is useful.
   - If a check fails, load `references/self_verification_loop.md`, inspect the first concrete failure, fix only refactor-caused breakage, and rerun the same check before broadening.
   - If the same failure repeats after one focused retry, evict that unit from the current pass with failure context instead of retrying blindly.
   - Append loop progress for longer work: `python3 <skill>/scripts/refactor_loop_notes.py --notes /tmp/refactor-loop-notes.md append --pass-name "<pass>" --summary "<what changed>" --checks "<check result>" --next "<next action>"`.

8. Integrate subagent output.
   - Wait for subagents only when their result is needed for the next local step.
   - Review subagent changes before relying on them.
   - If subagent edits overlap or conflict, land one coherent unit at a time and rerun the affected narrow checks.
   - Evicted units may re-enter a later pass only with concrete conflict, test, or review context.
   - Do not redo delegated work locally; integrate, refine, or ask a follow-up only for concrete gaps.

9. Keep edits bounded.
   - Update all call sites, imports, tests, docs, and fixtures affected by each rename or deletion.
   - Keep changes inside the target ownership boundary unless direct callers must change.
   - Avoid unrelated formatting churn and broad mechanical rewrites.
   - Preserve user or unrelated worktree changes.

10. Final self-review.
   - Re-run importer/reference searches for renamed symbols and deleted modules.
   - Confirm no stale aliases, unused compatibility paths, or old names remain unless deliberately preserved.
   - Run final narrow tests or checks.
   - Confirm the exit condition is met.
   - Stop on ambiguous ownership, public API decisions, unrelated verification failures, context loss without a handoff file, or only low-confidence improvements remaining.
   - If tests cannot be run, state exactly why and what risk remains.

## Deliverables

Report:

- Naming changes and why the new names improve semantics.
- Code deleted, collapsed, reduced, or deduplicated.
- Backward-compatible or public compatibility paths preserved, with the concrete reason each remains.
- Subagents used, their ownership scopes, and what they returned or changed.
- If no subagents were launched, the explicit reason this was allowed under the Parallel Agent Mandate.
- New shared functions, helper classes, enums, or typed objects, and why they reduce ambiguity rather than add indirection.
- Any behavior changed because it was a bug, with the bug named explicitly.
- Exact tests or checks run, including any failures fixed during the verification loop.
- Exit condition reached, or the concrete blocker that stopped the loop.
