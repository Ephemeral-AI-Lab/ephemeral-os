# Root Cause Debugging

Use this reference when the first reproduction still leaves the bug ambiguous, the traceback lands far from the likely source, or you catch yourself cycling through test-file and source-file reads without a falsifiable hypothesis.

## Goal

Find the first failing boundary and form one testable root-cause hypothesis before editing code.

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

Do not skip from reproduction directly to edits.

## Bounded evidence you may gather

- Read the owned production file and the immediate consumer or importer.
- Read the exact failing test node when the expected behavior is unclear.
- Run one narrow import-smoke, assertion-smoke, or helper-level repro through `daytona_codeact`.
- Read one adjacent shared production file when the traceback first lands there.
- Compare one working sibling implementation in the same package when the pattern is unclear.

## What counts as the first failing boundary

- The first owned helper that receives wrong data.
- The first import path that crashes before the named test runs.
- The first warning or error producer that no longer matches the owned assertion.
- The first schema/config/public API layer where the live behavior departs from the expected contract.

The failing test file itself is usually symptom evidence, not the boundary.

## Hypothesis rules

- Keep exactly one active hypothesis at a time.
- Make it falsifiable.
- Tie it to concrete evidence from the current run.
- Prefer source-of-bad-data explanations over symptom-level rewrites.

Good:
- `valid_divisions` now rejects nullable dtypes because a helper normalizes NA values before monotonicity checks.
- Direct import no longer warns because the symbol already exists in the module dict and bypasses `__getattr__`.
- The public wrapper is fine; the first divergence is an option passed incorrectly into the downstream parser.

Bad:
- The tests seem outdated.
- Something about pandas compatibility is broken.
- I should patch the message and see if that works.

## Multi-boundary systems

When behavior crosses layers such as test -> public API -> helper -> downstream library:

1. Confirm the input at the public API boundary.
2. Confirm the value or option passed into the next helper.
3. Confirm the first downstream call where behavior changes.
4. Fix the earliest owned boundary that can legitimately correct the bug.

Do not jump to the deepest stack frame if an earlier owned boundary already explains the failure.

## Stop signs

Stop and gather evidence instead of editing when:

- You are about to say "let me read a few more files" without a new question.
- You want to patch based on test names alone.
- You are reasoning from failure counts or cluster size instead of runtime evidence.
- You are about to change multiple files to "cover possibilities".
- You have re-read the same test or source file and still cannot state a hypothesis.

## Escalation rules

- After one failed hypothesis, return to the failing boundary and gather one new datum.
- After two failed hypotheses, check whether the boundary is wrong and whether one adjacent shared surface owns the bug.
- After three failed hypotheses or fixes, stop local thrashing and surface replanning evidence.

Do not keep stacking fixes on top of an unproven theory.

## Few-shot examples

- Example: the exact pytest node fails during collection because `pkg/__init__.py` imports a deprecated symbol.
  The first failing boundary is the shared import chain, not the owned assertion body.
  Confirm that import path once, then either widen one step on the same chain or report a blocker.

- Example: `pytest.warns(FutureWarning, match="use_nullable_dtypes")` fails because no warning appears.
  Check whether the warning-producing path still runs and whether the public import or option path bypasses it.
  Do not rewrite the warning text before proving the warning is emitted at all.

- Example: many URL-related tests fail in one file, but the wrapper module delegates validation to a downstream core library.
  Treat the Python wrapper as an owner candidate, not a proven root cause.
  Find the first boundary where wrapper inputs or outputs differ from the expected contract before patching.
