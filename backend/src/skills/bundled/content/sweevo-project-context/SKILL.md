---
name: sweevo-project-context
description: Stable SWE-EVO benchmark constraints and project context template for coordinator and worker agents operating on software evolution tasks.
---

# SWE-EVO Project Context

This skill carries the stable benchmark policy for SWE-EVO runs. Instance-specific facts such as the repo, exact FAIL_TO_PASS targets, PASS_TO_PASS guardrails, grading command, and frontier cap come from the current prompt or WorkItem payload.

When benchmark prose, release notes, or changelog bullets disagree with the live checkout and named tests, the live checkout plus the named tests win.

---

## Shared benchmark constraints

- **Source of truth is the current sandbox checkout.** The SWE-EVO test patch is already applied in the sandbox for this run. Treat the working tree, the named FAIL_TO_PASS targets, the PASS_TO_PASS guardrails, and the grading command as the benchmark contract.
- **Missing named tests are a runtime mismatch signal.** If a developer or validator cannot collect a named FAIL_TO_PASS node, or discovers the expected test file/function is absent from the live checkout, treat that as a sandbox or benchmark-surface mismatch first. Re-check the applied test surface and report the mismatch instead of guessing replacement owner files from similarly named modules.
- **Use the injected repo root as-is.** The benchmark runtime already injects the sandbox repo root as the working directory for worker shell commands. Do not prepend guessed `cd /workspace`, `cd /home/user`, or similar path hops unless the payload explicitly names a real child directory.
- **Changelog prose is background context only.** Do not treat release notes or version-transition prose as the implementation checklist.
- **Fix the repository, not the ambient environment.** Do not rely on ad hoc `pip install`, `conda install`, `uv add`, or other sandbox-only environment mutation as the benchmark fix. If dependency metadata is part of the solution, land it in the repo-managed manifest or lockfile.
- **Plan -> Execute -> Validate.** Planner decomposes, developer edits, validator verifies. Do not collapse those phases into one role.
- **Background work stays backgrounded.** After launching a scout or other background task, keep working other ready surfaces, use `check_background_progress` for spot checks, and wait only when that result is the remaining blocker.
- **Progress checks are per fresh scout.** If you launch a new scout and later want to wait on it, inspect that specific task with `check_background_progress` first. Do not spawn a batch of fresh scouts and then immediately wait on them.
- **Live tooling beats cached context.** Atlas briefs, shared briefings, and planner hints are useful snapshots. On any conflict, trust live code intelligence and live sandbox reads.
- **Runtime tool names are literal.** In team-mode sandboxes, use the actual `daytona_*` tool names exposed by the developer/validator toolkits. Do not fall back to generic `edit_file` / `read_file` tool names from other environments.

## Retry loop

- **First attempt should be fresh.** Start benchmark investigation from a fresh sandbox so you can trust the first failure signal.
- **After local runtime or skill fixes, prefer sandbox reuse.** Re-run with a stable `sandbox_name` so the harness can reuse the latest healthy prepared sandbox instead of rebuilding the image each time.
- **Reuse the latest healthy evidence.** Checkpoints, scout artifacts, token totals, and validator evidence are part of the retry surface. Do not restart cold if the existing sandbox is healthy and the repo can be reset in place.
- **Retry/replan handoff must preserve the evidence packet.** Carry forward clustered failing ids, affected files, prior touched files, the latest healthy checkpoint or sandbox anchor, and what changed since that point so the next planner turn can reuse evidence instead of rediscovering it.
- **On resume, reuse before reopening.** If a resumed or replanned turn already has a stable owner cluster or subsystem key, consult atlas/shared briefings first and scout only the still-missing slice.
- **Ownership mismatch is a planning problem.** If validator evidence says the active lane owns the wrong files or missed a sibling cluster, replan around that packet. Do not push that rediscovery work down to the next developer or validator lane.
- **Exact existing paths only.** Planner and replanner payloads must stay anchored on exact existing checkout-relative paths from the live repo or inherited evidence. If a cited file path is missing in the live checkout, treat that as an owner-map mismatch and re-anchor before assigning work. The one exception is a missing module file spelled verbatim by the failing import path when the parent package/directory already exists live; that exact import-path file may be assigned as a creation target, but do not generalize that into guessed sibling aliases.
- **Exact validator evidence is enough to branch.** On a replan turn, if the validator packet already names the exact failing pytest ids and live CI confirms the exact owner file(s), stop. Do not reopen test bodies or shared router files like `core.py` just to restate expected behavior; pass the symptom, owner surface, and guardrail target to the next developer lane.
- **Fresh benchmark roots should stay live-first.** On the first root planning pass, prefer `ci_scope_status(scope_paths=[...])` plus fresh scouts over `atlas_lookup`. Use Atlas only on resume/replan or when the current turn already established a live owner map and still needs cross-run structural reuse.
- **Fresh benchmark roots anchor live ownership before scouting.** After any required reference load, the first live planning tool call should be `ci_scope_status(scope_paths=[...])` on the likely owner files/directories. Do not open a fresh benchmark root with `run_subagent`, `ci_workspace_structure`, or symbol queries before that anchor.
- **Same-run scout context beats Atlas in a moving repo.** On resume/replan and on non-root branches, reuse current-turn shared scout context, dependency artifacts, and just-finished scout packets before consulting `atlas_lookup`. Atlas is cross-run structure, not the first answer to sibling-aware repo drift.

---

## Planner rules for SWE-EVO

- When the request already names one dominant FAIL_TO_PASS cluster plus several smaller named failures, keep the first scout wave proportional to the live owner surface. On large roots with several named clusters and permissive `ci_scope_status(...)` admission, the first wave should usually cover 3-4 disjoint production-owner scouts; otherwise use the smallest useful disjoint wave. Do not bundle unrelated owner surfaces into one scout lane just to mimic an old two-lane default, and do not spend a first-wave lane on the already-named giant test file unless no plausible production owner exists yet.
- If a guessed production owner file turns out to be missing, re-anchor on the nearest exact existing production directory/package path or park that cluster behind a child planner. Do not compensate by opening more benchmark test-file scouts in the same root turn.
- When a dominant FAIL_TO_PASS cluster contains dozens or hundreds of parametrized nodes, summarize it as one owner slice with a representative deduped subset of failing ids. Do not paste the full repeated test-id list into one developer payload.
- Preserve exact pytest node ids verbatim in planner payloads. Do not shorten `test_info_versions` to `test_info`, drop parametrization, or invent normalized aliases when you split FAIL_TO_PASS targets across child planners or workers.
- Never synthesize pytest node ids. If you need only a representative subset, copy exact node ids verbatim from the prompt. If you cannot quote an exact node id confidently, use the exact checkout-relative test file path from the prompt instead of inventing parametrization suffixes or hash-like ids.
- At any submitted benchmark plan level, keep validators paired with the concrete developer lanes they actually verify and stay within the active runtime validator cap for that plan. Child benchmark plans inherit that same cap. Residual child-planner branches emit their own validators after decomposition.
- In child benchmark plans, keep validators branch-local and risk-weighted instead of emitting one validator per developer or recreating an umbrella validation layer.
- Planner briefings must be execution-ready. Each developer or validator lane should receive the exact retry target, owned files or region, nearest same-surface guardrail, and any artifact refs or residual-cluster notes needed to act without fresh ownership discovery.
- Every planner `briefings` entry needs a stable `name`, a valid `source`, and the matching payload field for that source. Use `ref` for `source="artifact"` and `inline` for `source="inline"`. A content-only briefing object is malformed and will be rejected at plan submission time.
- Reproduction and verification commands must use exact checkout-relative test paths from the prompt or scout evidence. Do not shorten `dask/dataframe/io/tests/test_json.py` to `tests/test_json.py`, and do not strip package prefixes from benchmark focus files.
- On large benchmark roots, spend the first exploration pass on a small set of disjoint source-owner scouts rather than one long serial hypothesis lane.
- Treat scout lanes as scarce. Open a new scout only for a genuinely distinct unresolved owner slice, and keep fresh scout fanout modest enough that each lane still answers a real ownership question.
- If the first scout wave comes back partial or still leaves several disjoint owner hypotheses alive, launch another disjoint scout wave or a narrowed child planner. Do not freeze the root plan just because the first wave already ran.
- Once substantial planner budget has already gone into the same root benchmark surface, the default next step is the plan. Another wave needs a genuinely new disjoint owner cluster, not a deeper read of the same mapped clusters.
- Once the launched scout wave has returned enough owner coverage to name the dominant lane and at least one residual boundary, stop narrating and emit the plan. Do not spend extra planner turns debating benchmark-patch intent, missing fixtures, or whether the failures represent "new code" versus "regressions".
- Do not use root-planner CI queries like `import tables`, `ujson`, or similar dependency-name probes as evidence of the fix owner. Those probes do not identify source ownership and they frequently push the planner toward environment theories instead of code lanes.
- The submitted root benchmark plan should stay materially smaller than the runtime `max_plan_size`. Use dependency edges to keep the ready frontier small; do not confuse a small ready frontier with a tiny total plan.
- If the natural root task set exceeds 10 concrete slices, regroup adjacent sibling work into expandable child-planner items until the submitted level is back within 10.
- On large benchmark roots, child planners are also workload-sharding tools. If two developer lanes cannot plausibly cover every known residual cluster, keep the remaining owned surface behind one or more downstream expandable planner items instead of leaving it implicit.
- For broad SWE-EVO roots, the default graph shape should usually be a small set of critical developer lanes, one downstream expandable planner item for still-broad residual work, and one verifier whenever residual owned work still remains after the initial concrete lanes are chosen.
- At the benchmark root, that downstream child planner is a submitted expandable plan item only. Never call `run_subagent` / `team_planner` to pre-run or preview that branch once scout coverage is sufficient.
- Use the FAIL_TO_PASS list as reproduction signals, not as a reason to scout giant test files just to restate known failures.
- When the failure surface is broad, cluster by likely production owner and guardrail surface first. A hundred failing test IDs in one module still count as one source-owner lane, not a hundred planner tasks.
- A dominant cluster does not erase the residual cluster. If named FAIL_TO_PASS targets remain outside the dominant owner surface, the root plan must still give those residual targets their own developer lane or expandable child planner. Do not hide unresolved non-dominant failures inside validator-only coverage.
- Once one likely owner file or subsystem is known, stop changelog/version archaeology. Hand off the symptom, likely owner, exact reproduction target, and verification target.
- If the next planner thought is "I need to understand the actual test failures" inside a cluster that is already source-owner complete, stop and hand that cluster to a developer or validator. Exact runtime mismatch confirmation belongs downstream.
- The same rule applies to replanners: once validator evidence plus one live owner confirmation already identifies the corrective file(s), do not re-read the test body or shared parameter-plumbing files to author a patch recipe. Emit the corrective JSON and let the developer confirm runtime details.
- Do not treat a dependency pin or `pyproject.toml` entry as the root cause from the root planner just because the changelog mentions a version bump. A manifest bump is only a planner-owned lane when the task is explicitly packaging-related or a developer later confirms the repo manifest is the real fix from live evidence.
- If runtime evidence says an external dependency is missing a symbol or attribute, but a concrete repo file already imports or calls that symbol, keep the root lane anchored on the local consumer or compatibility surface. Do not rewrite the root plan into "upgrade the dependency" unless live manifest or lockfile evidence proves the repo-managed dependency metadata is the actual owner.
- Root planners must not spend CI turns on dependency-name or import archaeology (`pydantic_core`, package pins, installed versions, lockfiles) once concrete source owners are known. If version drift is still plausible, pass it to the developer lane as a hypothesis tied to an exact reproduction target.
- Once source-owner scouts exist, do not open new manifest or giant-test scouts. Remaining uncertainty belongs to a developer or validator lane unless source ownership is still ambiguous.
- Split disjoint owner clusters into separate source-owned execution lanes. Do not collapse unrelated modules into one omnibus developer task just because they appear in the same release-note block.
- When one dominant owner cluster is already mapped and the remaining named failures span several smaller modules, keep the root graph hierarchical: emit the dominant developer lane, one concrete residual lane that is already source-owned, and park the still-unowned residual surface behind a downstream expandable child planner. Do not keep scouting just to flatten every residual into the root plan.
- For residual child-planner branches, keep `owned_files`, `owned_failures`, and `expansion_hint` grounded on exact existing checkout-relative paths. If the source owner is still unresolved, say so explicitly and anchor the branch on the exact failing test file plus the confirmed candidate directory. Do not guess nonexistent production files such as `utils_dataframe.py` or truncated paths such as `dask/dataframe/s`.
- `owned_files` is not a hypothesis bucket. Planner and child-planner payloads must keep missing guessed owners out of `owned_files`; unresolved production guesses belong in `expansion_hint` or `notes` until live structure confirms an exact existing path.
- Replanners follow the same rule: if a validator or inherited note cites a missing alias path such as `pyarrow.py` while live CI resolves the surface to `arrow.py`, treat the alias as stale evidence and assign the corrective lane on the exact existing file only.
- When the failure packet itself names a missing module import path such as `from dask._compatibility import PY_VERSION`, do not collapse that into an existing neighbor file if Python still needs the missing module path. Carry the exact import-path target (for example `dask/_compatibility.py`) into the corrective lane, optionally alongside one existing companion export surface already confirmed by live evidence.
- Once the dominant owner slice and one residual slice are both mapped, stop waiting on additional scouts from the same root surface and emit the plan. Downstream developers and validators own the runtime confirmation.
- Validators verify; they do not own first-fix work. Every named FAIL_TO_PASS cluster in the request must map to a developer or child-planner owner before a validator is allowed to cover it.
- A root or sibling validator must not depend on an expandable child planner as a placeholder barrier. If residual work is parked behind child planning, the verification for that slice belongs inside that child branch or behind the concrete leaf developers it emits.
- If one file is large but still the likely owner, a bounded single-file scout is valid. If that still leaves several independent regions, emit a narrowed child planner instead of forcing a flat root plan.
- Parent and sibling exploration lanes must stay disjoint. Do not reopen a slice already owned by a scout or child planner unless new evidence invalidates the boundary.
- While scouts are running, keep the planner moving on other uncovered branches, shared-context reuse, and plan-shape reasoning. Wait only when a scout result becomes the remaining blocker.
- Benchmark planner turns should not spend tool budget on explicit `share_briefing` promotion unless that tool is visibly available. Same-run scout auto-promotion plus attached artifact refs already cover the normal reuse path here.
- Once the returned scout evidence is sufficient to name the likely implementation surfaces and direct validation surfaces, the root planner should stop scouting and emit the plan. This may happen after one wave or several; additional confirmation belongs to developer or validator lanes, not to the root planner.
- Treat pytest assertion renderings as runtime symptoms only. A line such as `where None = MultiHostUrl(...).path` is not proof that the bug lives in a particular accessor or external dependency API; it is only evidence the downstream worker should reproduce against the owned source slice.
- A planner-side `ci_query_symbols(kind="class")` miss does not prove a public type is absent from the repo. Imported dependency classes, `Annotated[...]` aliases, and lazy/export-only names can live behind the local module surface without appearing as classes. Keep the lane on the local export/compatibility file until a worker confirms the exact missing public import.
- If the planner receives a budget warning, the next assistant message must be the final plan JSON. Do not spend the remaining budget checking background progress or reopening hypotheses.
- Treat duplicate-scout rejections and background wait protocol errors as stop-and-plan signals. Reuse the gathered evidence instead of retrying the same exploration pattern, and do not pivot into `ci_recent_changes`, `ci_edit_hotspots`, or dependency/version archaeology once those signals fire.
- A repeated `WAIT_REQUIRES_PROGRESS_CHECK` or repeated whole-batch wait on the same benchmark wave is evidence that the planner should finish the plan, not evidence that another planner-side deep-dive is needed.
- After emitting the final plan JSON, stop immediately. Do not append prose summaries after the payload.

---

## Developer rules for SWE-EVO

- Start from the exact named failing test or a faithful reproduction lifted directly from it.
- Planner diagnoses are hypotheses until the current failing output confirms them.
- When the first failing pytest surface is a missing public name or collection/import error, inspect the exact import site and the owning module's export surface before inventing new public types or neighboring symbols.
- After a bounded export fix, rerun the named pytest entry point before widening the same lane to additional public names. Do not infer the next missing symbol from memory, truncated output, or nearby imports.
- Once that missing public name is anchored to a local export file, do not spend developer budget on dependency version checks or dependency capability archaeology. Fix the local surface first.
- After one targeted reproduction plus one or two focused code reads identify the deciding function or branch, edit immediately. Do not spend the attempt on repeated ad hoc probes.
- If the exact retry target is already green in the sandbox, stop debugging and report that result; let the validator spend the one broader regression check.
- If the named pytest node does not exist in the live checkout, stop and report the mismatch with the exact missing node id. Do not invent substitute tests, new compatibility shims, or neighboring files until the benchmark surface is confirmed.
- If the first failing surface is an unowned benchmark test import/collection error, keep the lane on the adjacent production/export owner. Do not "repair" the benchmark by editing the unowned test file just because the payload or node name is stale.
- A test path mentioned only in `owned_failures`, `verify`, or a failing command is not test ownership. Worker lanes should still prefer the production/export surface first, but they may edit a nearby test or support file when that is part of the minimal correct fix.
- `owned_files` defines the default edit surface, not an absolute write-permission wall. If the minimal coherent fix needs one or two adjacent supporting files outside `owned_files`, the developer may widen to them and should explain that widened touch clearly.
- If that adjacent production/export owner sits outside the lane's assigned `owned_files`, the developer may widen to that likely owner when it remains the same cohesive bug. Report `scope_mismatch` only when the work expands into a different subsystem, a separate bug family, or an unbounded refactor.
- A developer claim that a named benchmark test now encodes "old behavior" after a contradicted patch is not enough to open a test-edit lane. Unless an independent validator packet or already-owned test surface proves the expectation is stale, keep the corrective branch on the confirmed production or coordination owner surface.
- Fix production code first. Do not edit tests, snapshots, or benchmark harness files unless the WorkItem explicitly assigns them.
- When touching core metadata propagation or schema-wrapper logic, run a same-surface regression slice that checks ordering, wrapper passthrough, and error-shape stability, not just the exact failing test.
- When touching RootModel JSON schema description propagation, validate both the field-description-only case and the docstring-vs-field precedence case.
- If the full benchmark later exposes new pass-to-pass regressions, treat those exact failures as the next concrete retry targets; do not keep reporting only the original FAIL_TO_PASS list.

---

## Validator rules for SWE-EVO

- Start with the exact retry target(s) named by the payload or benchmark context.
- After the exact retry target passes, spend at most one broader same-surface regression command unless the payload explicitly requires more.
- If the exact retry target fails, report that failure immediately with exact test ids, exit code, and a short verbatim error snippet.
- If verification shows the lane owns the wrong files, misses a sibling corrective cluster, or resumed from a stale retry boundary, report `plan_gap` with exact evidence. Do not broaden into fresh exploration from validator mode.
- If the validator cannot collect a named FAIL_TO_PASS node because the test or file is missing, report `FAILURE_TYPE: benchmark_surface_mismatch` with the exact missing node ids.
- The benchmark harness will run the full grading command after the team phase. Do not spend validator budget duplicating broad redundant suites by default.

---

## Observability and state

- Usage totals, model breakdowns, checkpoints, and retry metadata are part of the benchmark evidence. Prefer the latest healthy checkpoint when deciding what to resume.
- When reporting a blocker, include the exact command, exit code, failing test ids, and likely owner surface so replanning can stay surgical.

## Benchmark decomposition stop conditions

- Once the root planner can name a dominant owner slice and a residual cluster boundary, planning is complete for that layer. Submit the hierarchical plan immediately.
- After that sufficiency point, do not launch any new subagents except progress checks for already-running scouts. In particular, do not background a child `team_planner` to "help" finalize the root DAG.
- Duplicate-scout rejection, background wait protocol errors, and already-completed waits are evidence that exploration has crossed the sufficiency boundary. Do not open another scout after those signals.
- Scout launches must satisfy the literal runtime schema: pass exactly one of `prompt` or `input`, and for `agent_name="scout"` that means `input={"target_paths": [...]}` only. Never send `prompt=null` or an empty scout input.
- If `share_briefing` is absent from the visible tool list, treat that as a no-promotion profile, not as a blocker. Reuse the scout artifact locally or via auto-promoted shared context and keep planning.
- If the dominant cluster is already mapped to one owner file or one tightly-coupled owner pair, keep that as one developer lane. Everything else becomes either a residual child planner or separately bounded residual developer lanes.
- The default large-root benchmark shape for this repo is: two critical developer lanes, one residual child planner lane, and one validator lane. Smaller roots may still use one dominant developer lane plus one residual child planner lane and matching validation. Flatten the residual lane into direct developers only when each residual owner is already bounded without more planning.
- If residual work stays behind a child planner, a root validator may verify only the concrete root developer lanes or be omitted. Do not add a root validator whose primary scope is the child-planner-owned residual branch; that branch emits its own validators after decomposition.
- On benchmark-root turns that require scouting or DAG shaping, load the corresponding planner references before acting: `team-planner-playbook/exploration-script` before the first non-reference planning tool call, and `team-planner-playbook/task-planning-decomposition` before finalizing the root DAG when the runtime exposes `load_skill_reference`.
- Benchmark-root planners should treat those reference loads as mandatory opening and closing steps. If they are still narrating about cluster shape without having loaded `exploration-script`, or still debating the root DAG after scout sufficiency without having loaded `task-planning-decomposition`, they are already off-policy.
- Root benchmark scouts should target likely production owner files or directories first. Do not spend the first scout wave on already-named benchmark test files unless production ownership is still unresolved after the owner-surface pass.
- Root planner symbol hits that only land in benchmark test files are not ownership evidence. Use them only as symptoms; re-anchor the plan on the nearest exact existing production path instead of routing work through the tests.
- Planner outputs that collapse unrelated residual bugs from `construction`, `json_schema`, `root_model`, and `types` into one developer lane are low-quality plans and should be avoided.
- Planner outputs that repeat `local_id`, `agent_name`, `kind`, or `payload` keys inside one JSON object are malformed and unusable. Close the current item object and start a new sibling item instead of continuing the same object.
- If a planned multi-lane root payload extracts as only one validator-looking item, treat that as broken JSON boundaries, not as permission to submit a validator-only fallback. Repair the sibling item separators first.
- Planner outputs that invent pytest node ids, shorten benchmark test paths, or name production files that the checkout does not contain are malformed benchmark payloads even if the JSON parses. Copy exact ids and paths from the prompt or confirmed live structure only.
- Planner-side narration must stay at the ownership and validation-target level. Do not speculate about concrete code fixes from failure strings such as `MultiHostUrl.path` or other pytest assertion details.

## Pydantic cross-surface guardrails

- Changes to `pydantic/root_model.py` or any `model_json_schema` override can affect `tests/test_json_schema.py` even when the original failure came from `tests/test_root_model.py`. Include `tests/test_json_schema.py::test_root_model` or an equivalent nearby schema guardrail before declaring success.
- Changes to `pydantic/types.py` that alter `Secret` JSON output can affect docs/example expectations in `tests/test_docs.py`, not just `tests/test_types.py`. Include the nearby doc example guardrail for the touched symbol region before declaring success.
