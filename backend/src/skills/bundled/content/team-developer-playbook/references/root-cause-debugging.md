# Root Cause Debugging

Use this reference when the first reproduction still leaves the bug ambiguous, the traceback lands far from the likely source, or you catch yourself cycling through reads without a falsifiable hypothesis.

## Required checkpoint before first edit

Before the first source edit, write down all three:

1. `Observed failure`: the exact failing command, node, import, warning, or assertion.
2. `First failing boundary`: the first production function, module, helper, import chain, or config surface where behavior diverges.
3. `Hypothesis`: one concrete statement of what is wrong and why the evidence points there.

If you cannot state all three after the first reproduction, gather one more bounded piece of evidence instead of patching.

## Debug loop

1. Reproduce exactly once on the owned verify surface.
2. Read the traceback or assertion carefully.
3. Identify the first failing boundary, not just the final test assertion.
4. Gather one bounded confirming datum.
5. State one hypothesis.
6. Make one minimal edit or one minimal proving check.
7. Re-verify on the same narrow surface.

## Dead-cycle breaker

If one scoped packet, one symbol/reference query, and one proving repro all land on the same boundary, stop exploring. The next action must be one of:

1. Make the smallest production edit at that boundary.
2. Repair or revert your own last experiment first if it broadened the red surface into a shared startup, import, or warning-filter crash.
3. Surface one concrete blocker tied to that boundary.
4. Replan because the boundary is shared or unowned.

## Bounded evidence you may gather

- Read the owned production file and the immediate consumer or importer.
- Use `ci_query_symbols(...)` or `ci_query_references(...)` once to identify the next caller/callee boundary before writing custom runtime probes.
- Use `inspect_inherited_context(...)` once when a shared brief might answer the question, but only if you also keep the current scoped coherence token in view.
- Run one narrow import-smoke, assertion-smoke, or helper-level repro through `daytona_codeact`.
- Read one adjacent shared production file when the traceback first lands there.

## What counts as the first failing boundary

- The first owned helper that receives wrong data.
- The first import or warning-filter path that crashes before the named test runs.
- The first warning or error producer that no longer matches the owned assertion, even if a wrapper re-raises it later with extra context.
- The first schema/config/public API layer where the live behavior departs from the expected contract.

The failing test file itself is usually symptom evidence, not the boundary.

## Hypothesis rules

- Keep exactly one active hypothesis at a time.
- Make it falsifiable.
- Tie it to concrete evidence from the current run.
- Prefer source-of-bad-data explanations over symptom-level rewrites.

## Multi-boundary systems

When behavior crosses layers such as test -> public API -> helper -> downstream library:

1. Confirm the input at the public API boundary.
2. Confirm the value or option passed into the next helper.
3. Confirm the first downstream call where behavior changes.
4. Fix the earliest owned boundary that can legitimately correct the bug.

Do not jump to the deepest stack frame if an earlier owned boundary already explains the failure, do not keep tracing sibling paths once that boundary survives a proving repro, and when several owned failures share one signature, prove the earliest shared boundary before patching leaf helpers.

## Stop signs

Stop and gather evidence instead of editing when:

- You are about to say "let me read a few more files" without a new question.
- You are about to open `git status`, `git log`, `git show`, `git diff`, `git stash`, `git checkout`, or `git restore`, reason from failure counts or cluster size, or treat payload prose, themed work-item text, or repo history as stronger evidence than the current red node.
- You are about to blame pytest config, warning aliases, caller-stack tricks, a supposedly missing alias/module, or a "wrong" test after your last edit moved startup imports through a warning-producing hook, lazy export, or new module.
- You are about to treat `owned_failures` as edit ownership because a verify file imports a missing private compat alias or module.
- You are about to add a root-only skip, xfail, or verify-file rewrite before you can name the owned loader or access gate.
- You have re-read the same test or source file and still cannot state a hypothesis.
- The same boundary already survived one proving repro and you are still reading siblings instead of patching or replanning.
- You are about to call a still-red owned verify failure "pre-existing" or plan to ignore, deselect, or xfail it instead of tracing the current boundary.
- You have already restated the same boundary in different words and still have not edited, shared a blocker, or replanned.
## Escalation rules
- After one failed hypothesis, return to the failing boundary and gather one new datum; if the red surface only broadened after your edit, revert that local experiment first.
- After two failed hypotheses, check whether the boundary is wrong and whether one adjacent shared surface owns the bug.
- After three failed hypotheses or fixes, stop local thrashing and surface replanning evidence.
## Few-shot examples
- Example: pytest dies while parsing a warning filter because resolving `pkg.tests.warning_aliases.RemovedInXWarning` imports `pkg/__init__.py`, then `pkg/base.py`, and your deprecation edit moved `_FLAG` behind `pkg.compat.__getattr__`.
  The first failing boundary is that shared import chain and its internal caller, not `setup.cfg`, the warning alias, or the owned assertion body.
  Confirm the production import path once with one import-smoke command and one live source read, then switch startup callers like `pkg/base.py` to a quiet supported path such as `pkg._compat` or widen one step on that chain.
  Keep internal callers on a quiet internal export; deprecation hooks belong on explicit public access paths only. Do not patch warning filters, tests, caller-stack heuristics, or import-order tricks, and if your last edit caused that startup crash, repair or revert it before any more diagnosis.
- Example: the verify file fails collection on `from pkg._compatibility import FLAG`, the payload lists that test under `owned_failures`, and `pkg/base.py` still imports private names through `pkg.compatibility`.
  The first failing boundary is the shared compat/export surface, not the verify file or warning filter.
  Confirm the importer chain and live compat module once, then restore the quiet private owner or move startup callers there first. After that edit, stop for one import-smoke or exact verify; do not rewrite the test import or add a module-level deprecation hook on the public wrapper while startup still uses it.
  If satisfying both quiet startup and deprecated public access would require caller-stack or import-order behavior, widen one step or replan.
- Example: `pytest.warns(FutureWarning, match="deprecated_option")` should warn only on an explicit opt-in path, but the default path now warns or errors too.
  Check the live deprecation guard or option normalization branch first. If one backend still expects an explicit `ValueError` on that opt-in flag, preserve that engine-specific ordering before any new warning.
- Example: a helper accepts `None`, a mapping, or a caller-provided container, and truthiness tweaks do not move behavior.
  Treat function entry values as the first boundary. Print the live type, identity, and one downstream handoff before changing selection logic.
- Example: a backend dispatcher or compatibility wrapper re-raises `FutureWarning` or `ValueError` with "Original Message: ...", and the exact test still expects `pytest.warns(...)` or `pytest.raises(...)`.
  Treat the wrapper as transport, not the first failing boundary. Read the original producer line named in the traceback and fix the ordering or guard there before blaming test config, regex matching, or wrapper behavior.
- Example: a chmod-based permission test runs as UID 0 and repeated probes still succeed.
  Treat the owned loader or access gate as the first boundary. Read that gate once; do not jump straight to root-only skips or harness-shaped simulation. If the repo already owns a generic readability or access gate, patch that gate without keying on UID 0; otherwise replan.
- Example: the exact pytest target returns `ERROR: not found`, exit code 4, or `no tests ran`, the current scoped packet names a different failing node than the inherited one, or that control failure appears right after `git stash/pop` or a revert-style experiment.
  Treat that as a wrong-target, stale-target, control failure, or poisoned sandbox, not proof the owned surface is green.
  Re-collect the current owned verify target from live scope or replan from the latest healthy checkpoint; do not keep debugging the earlier test name or summarize a broader suite pass around it.
- Example: several aggregate failures share the same wrong MultiIndex shape or dtype across sibling methods.
  Treat the earliest shared result-builder or shuffle/handoff as the first boundary, and print one live pandas side plus one live dask intermediate before patching a leaf helper.
  In assertion diffs, do not guess expected/actual from `left`/`right` labels or from counts alone; anchor on the test callsite and the exact objects you printed before choosing the bad side.
  If a local helper edit makes unrelated custom-agg paths fail, revert it before continuing. If the work item names a different helper, or the failure says a module/symbol is missing, follow the reproduced binding path and read the binder/importer before inventing aliases or new modules.
