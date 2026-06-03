# Autonomous Refactor Orchestration

Use this reference for multi-pass refactors, broad renames, or any cleanup that can benefit from subagents.

## Parallel-First Rule

For broad folders, package-sized targets, and multi-file subsystem refactors, the orchestrator must launch parallel subagents before narrowing implementation scope. This is not optional best effort:

- First wave: at least two read-only subagents, normally a semantic/import-contract audit and a reduction/deletion-evidence audit.
- Additional waves: write-capable workers for non-overlapping cleanup lanes, plus reviewer/verifier agents after implementation when the refactor is non-trivial.
- Local-only sequential work is acceptable only for one focused file, one symbol family, one risky public facade, or a target with no useful independent audit lane.
- If subagent tooling is unavailable or blocked by active tool policy, stop and report that as a blocker before reducing scope.

## Pattern Spectrum

Choose the least powerful loop that can finish safely:

| Pattern | Best for | Parallelism |
| --- | --- | --- |
| Sequential cleanup loop | One focused file, one symbol family, or one risky public facade | None |
| Sidecar subagent loop | Importer discovery, test discovery, review, or verification while orchestrator edits | Read-only or verification |
| Parallel lane loop | Independent modules with disjoint write sets | Worker subagents |
| Dependency DAG loop | Larger refactors with ordered dependencies | Parallel by DAG layer |

Do not use parallel code-edit lanes when two agents need the same file, the public API decision is unsettled, or one lane depends on another lane's output.

## Orchestrator Responsibilities

The main agent owns:

- Target boundary and behavior invariants.
- Public compatibility decisions.
- Work decomposition and dependency ordering.
- Subagent prompts and write-set boundaries.
- Integration, conflict handling, final verification, and final report.

Subagents own only the bounded task assigned to them.

## Decomposition Rules

Create work units with:

- `id`: kebab-case responsibility name.
- `target_paths`: files or modules the lane is responsible for.
- `allowed_edits`: specific files, modules, or change types the lane may write.
- `forbidden_paths`: overlapping, public facade, migration, generated, or fixture files the lane must not edit.
- `deps`: work-unit IDs that must land first.
- `file_overlap_group`: shared ownership group for units that cannot land in the same wave.
- `public_contracts`: imports, APIs, persisted shapes, and compatibility paths to preserve.
- `read_context`: files, audit report, loop notes, and invariants to read first.
- `acceptance`: exact semantic cleanup expected.
- `verification_commands`: narrow commands or searches the lane should run when feasible.
- `risk_tier`: `trivial`, `small`, `medium`, or `large`.
- `handoff_file`: optional file or final summary section for lane output.

Prefer fewer cohesive units. Keep tests with the implementation they validate. Use dependency edges only for real code or import dependencies.

## Subagent Promotion Rules

Promote subagents by default for:

- Parallel importer/reference discovery across different packages.
- Independent naming audits for separate ownership areas.
- Independent reduction/deletion audits for separate ownership areas.
- Public-contract audits that are read-only.
- Disjoint implementation lanes with no file overlap.
- Reviewer passes after an implementer wrote code.
- Verification passes that can run while the orchestrator continues with non-overlapping work.

Keep work local for:

- The immediate blocking task on the critical path.
- Ambiguous public API or compatibility decisions.
- Cross-cutting renames that require one atomic edit across many files.
- Shared public facades, migrations, generated registries, root test setup, or shared fixtures.
- Conflict resolution and final integration.

For package-sized or multi-directory targets, do not proceed with only local work unless the target has first been decomposed and no independent audit or worker lane exists.

## Agent Template Catalog

Use these prompt templates from `agents/` when spawning subagents:

| Template | Mode | Use |
| --- | --- | --- |
| `semantic_cartographer.md` | read-only explorer | Naming map, ownership boundaries, rename families |
| `import_contract_auditor.md` | read-only explorer | Importers, public contracts, compatibility classifications |
| `reduction_evidence_auditor.md` | read-only explorer | Deletion proof, redundancy, simplification candidates |
| `cleanup_lane_worker.md` | write worker | One disjoint behavior-preserving cleanup work unit |
| `desloppify_cleanup_worker.md` | write worker | Separate cleanup pass after an implementer |
| `refactor_review_sentinel.md` | read-only reviewer | Independent review after implementation |
| `verification_evidence_runner.md` | read-only verifier | Command execution, first-failure classification |
| `integration_coordinator.md` | orchestrator/integrator | Main-agent integration policy or a narrowly bounded integration lane |

Render templates with:

```bash
python3 <skill>/scripts/render_subagent_prompt.py \
  <skill>/agents/cleanup_lane_worker.md \
  --values /tmp/work-unit.json \
  --strict \
  --out /tmp/cleanup-lane-prompt.md
```

Pass rendered prompts to the available subagent mechanism. If the environment only exposes generic `explorer` and `worker` roles, use read-only templates with `explorer` and write templates with `worker`.

## Subagent Prompt Template

```text
Use $refactor-naming-semantics for this bounded refactor lane.

Owned scope:
- <paths/modules>

Do not edit:
- <forbidden paths>

Invariants:
- <behavior and public compatibility constraints>

Task:
- <specific naming/reduction goal>

Checks:
- <narrow commands or searches>

Handoff:
- <summary file or final response requirements>

You are not alone in the codebase. Do not revert edits made by others. If concurrent changes affect your files, adapt to them. Edit only your owned scope, preserve behavior unless you find a clear bug, and report changed files, reductions, naming changes, checks run, and remaining risks.
```

## Loop Shape

Use this stage order:

1. Audit and baseline.
2. Decompose into work units and dependency layers.
   - For broad targets, launch the first read-only audit wave here before editing locally.
3. Run each DAG layer:
   - Launch independent lanes in parallel.
   - Keep the orchestrator doing non-overlapping work while lanes run.
   - Integrate lane outputs one coherent unit at a time.
   - Run layer checks.
4. Run a separate de-sloppify cleanup pass.
5. Run a separate reviewer or verifier pass.
6. Fix only concrete issues from checks or review.
7. Evict failed units with conflict, test, or review context if they cannot land after one focused retry.
8. Stop when exit conditions are met.

## Merge and Eviction

When independent lanes finish:

1. Inspect changed paths and compare them to each unit's allowed edit set.
2. Land non-overlapping units first.
3. For overlapping units, integrate one at a time and rerun affected checks after each integration.
4. If a unit conflicts, fails checks, or violates ownership, evict it from the current pass.
5. Capture eviction context: conflicting files, relevant diff, command output, and the concrete reason it could not land.
6. Re-run the unit in a later pass only with that context and a revised narrower scope.

## Exit Conditions

Always set at least one explicit stop condition:

- Default maximum passes: 3.
- Narrow tests and import/reference searches pass.
- No high-confidence naming or deletion improvement remains inside the target boundary.
- Maximum pass count reached.
- Remaining work requires a product/API decision.
- Failures are unrelated and documented.
- The same verification failure repeats after one focused retry.
- Context is too low and no current handoff file exists.

Avoid unbounded loops. Do not retry the same failure without adding new evidence from logs, diffs, or review output.
