# Complex project build shell-edit + LSP saturation scenario
**Date:** 2026-05-11
**Status:** DRAFT
**Owner:** sandbox / live-e2e
**Pairs with:** `.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md`

---

## 1. Goal

Create a new live SWE-EVO sandbox scenario that mirrors
`backend/src/live_e2e/scenarios/sandbox/complex_project_build.py`, but changes
the workload shape in two ways:

1. Shift approximately one third of logical file edits from the `edit_file`
   tool to shell-based edit commands. These shell edits must mutate real files
   through the overlay capture path and then publish through OCC.
2. Run at least 200 LSP checks in the full scenario, where a check means a
   successful LSP tool call with a semantic assertion on the returned content,
   not just a counted invocation.

The purpose is to compare `edit_file` and shell mutation behavior in the same
project-build workload while also proving that LSP state remains correct across
mixed OCC edit paths, overlay captures, and layer-stack auto-squash.

## 2. New scenario identity

Add sibling scenario classes rather than changing the existing
`complex_project_build` contract:

```
backend/src/live_e2e/scenarios/sandbox/complex_project_build_shell_edit_lsp.py
backend/src/live_e2e/squad/complex_project_build_shell_edit_lsp_probe.py
backend/src/live_e2e/tests/sweevo/test_complex_project_build_shell_edit_lsp.py
```

Scenario registry keys:

```
sandbox.complex_project_build_shell_edit_lsp
sandbox.complex_project_build_shell_edit_lsp_smoke
```

Executor actions:

```
complex_project_build_shell_edit_lsp
complex_project_build_shell_edit_lsp_smoke
```

The scenario should reuse the existing scheduler demo fixtures and refactor
passes from:

```
backend/src/live_e2e/scenarios/sandbox/_fixtures/
```

Do not fork the fixture content unless a new fixture is required for an exact
LSP diagnostic or reference assertion.

## 3. Baseline copied from complex_project_build

Keep these behaviors from the existing scenario:

- Build a stdlib-only Python project under `/ephemeral-os`.
- Rebind the workspace base with `api.build_workspace_base(..., reset=True)`.
- Use skeleton writes plus incremental patches to build real source and tests.
- Run the final pytest gate inside `/ephemeral-os`.
- Exercise `read_file`, `write_file`, `edit_file`, `shell`, direct
  `sandbox.api`, OCC commits, overlay capture, layer-stack squash, and LSP.
- Emit `/ephemeral-os/.metrics/perf.json` and
  `/ephemeral-os/.metrics/summary.json`.
- Keep smoke and full variants.

Keep the existing projection correctness checks:

- toolkit `read_file` equals direct `sandbox.api.read_file` after stripping line
  annotations.
- toolkit `read_file` equals shell `cat` equals direct `sandbox.api.read_file`
  for sampled final files.
- pytest JUnit XML exists, parses, and reports zero failures/errors.
- import checks prove the final projected files are importable.

## 4. Edit routing requirement

Define a logical edit operation as any operation that changes file content after
the initial skeleton write. This includes fixture patches, refactor edits,
amplification edits, and intentional repair edits. It excludes read-only shell
commands, pytest commands, LSP calls, git commands, and direct API readbacks.

The full scenario must satisfy:

```
logical_edit_count >= 600
shell_edit_count >= floor(logical_edit_count / 3)
edit_file_count >= floor(logical_edit_count / 3)
abs((shell_edit_count / logical_edit_count) - (1 / 3)) <= 0.03
```

The smoke scenario can use smaller counts but must keep the same ratio rule:

```
logical_edit_count >= 90
shell_edit_count >= floor(logical_edit_count / 3)
abs((shell_edit_count / logical_edit_count) - (1 / 3)) <= 0.05
```

Implementation rule:

```
if logical_edit_index % 3 == 2:
    apply_shell_edit(...)
else:
    apply_tool_edit_file(...)
```

Use this deterministic routing everywhere the existing probe currently calls
`_edit_file` for non-error file mutation. Intentional conflict probes should
remain explicit and counted separately as `expected_errors`.

## 5. Shell edit command shape

Shell edits must use portable Python, not `sed -i`, because the sandbox image
and shell platform details may vary. A shell edit command should:

1. Read the target file.
2. Verify the old text occurs exactly once unless the specific test case is a
   conflict probe.
3. Replace the text.
4. Write the file back.
5. Print structured JSON with path, bytes before/after, and SHA-256 before/after.

Template:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

path = Path("/ephemeral-os/scheduler_demo/domain/task.py")
old = "..."
new = "..."
data = path.read_text(encoding="utf-8")
count = data.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one match, found {count}")
before_hash = hashlib.sha256(data.encode("utf-8")).hexdigest()
updated = data.replace(old, new, 1)
path.write_text(updated, encoding="utf-8")
after_hash = hashlib.sha256(updated.encode("utf-8")).hexdigest()
print(json.dumps({
    "path": str(path),
    "before_bytes": len(data.encode("utf-8")),
    "after_bytes": len(updated.encode("utf-8")),
    "before_sha256": before_hash,
    "after_sha256": after_hash,
}, sort_keys=True))
PY
```

This avoids the current shell policy's destructive git block and still routes
through the command-exec overlay capture/OCC path. Do not use `git add`,
`git commit`, `rm -r`, or broad workspace moves as shell-edit commands.

After each shell edit:

- increment `shell_edit_count`;
- record the parsed JSON payload;
- assert `before_sha256 != after_sha256` unless the operation is an intentional
  no-op check;
- read the same file through `sandbox.api.read_file`;
- assert the new text is present and old text is absent.

## 6. LSP correctness requirement

The full scenario must run at least 200 semantic LSP checks. Use a per-tool
floor so the total cannot be satisfied by one easy tool:

```
lsp.hover              >= 40 semantic checks
lsp.find_definitions   >= 40 semantic checks
lsp.find_references    >= 40 semantic checks
lsp.query_symbols      >= 40 semantic checks
lsp.diagnostics        >= 40 semantic checks
total_lsp_checks       >= 200
```

The smoke scenario should use:

```
per LSP tool >= 5 semantic checks
total_lsp_checks >= 25
```

A semantic LSP check must assert on output shape and expected content.

### 6.1 Hover checks

For known symbol positions, assert that hover output contains an expected symbol
name and at least one expected type/documentation token.

Examples:

```
Task
TaskState
MemoryStore.fetch
Scheduler.enqueue
RetryPolicy
```

### 6.2 Definition checks

For each known reference position, assert the returned definition location points
to the expected file and expected line/character range within a small tolerance.

Use fixture-owned expectations rather than hard-coded line numbers embedded in
the probe body:

```
backend/src/live_e2e/scenarios/sandbox/_fixtures/lsp_expectations.py
```

Expected schema:

```python
@dataclass(frozen=True)
class LspExpectation:
    symbol: str
    source_path: str
    source_anchor: str
    definition_path: str
    definition_anchor: str
    min_references: int
    hover_contains: tuple[str, ...]
```

Resolve anchors at runtime using direct `sandbox.api.read_file` so the checks
survive fixture edits without stale line numbers.

### 6.3 Reference checks

For each known symbol, assert:

- at least `min_references` references are returned;
- every returned path is under `/ephemeral-os`;
- at least one reference lands in source code and at least one in tests for
  symbols intentionally used by tests;
- after a rename refactor, references to the old symbol name drop to zero and
  references to the new symbol meet the expected floor.

### 6.4 Symbol query checks

For `query_symbols`, assert that the expected symbol appears with the expected
kind/name and file path.

Minimum symbol set:

```
Task
TaskState
Schedule
Priority
Scheduler
MemoryStore
JsonSerializer
RetryPolicy
```

### 6.5 Diagnostics checks

Diagnostics must prove both clean and broken states:

1. Clean-state checks: run diagnostics on final source/test files and assert
   zero diagnostics.
2. Broken-state checks: use shell edit to inject one known Python error in a
   temporary fixture file, run diagnostics, and assert the diagnostic range or
   message contains the expected error.
3. Repair checks: fix the file with the opposite shell edit or `edit_file`, run
   diagnostics again, and assert diagnostics clear.

Broken-state probes should write under a dedicated file such as:

```
/ephemeral-os/scheduler_demo/_lsp_error_probe.py
```

Do not leave the final project broken before pytest.

## 7. Phase plan

### Phase 0: Bootstrap

Same as `complex_project_build`, plus initialize counters:

```
logical_edit_count = 0
edit_file_edit_count = 0
shell_edit_count = 0
lsp_semantic_checks = {}
```

### Phase A: Skeleton

Same as existing scenario. Skeleton creation remains `write_file`, not shell
edit. The shift rule starts after skeleton writes.

### Phase B: Patch progression with mixed edit paths

Replace direct calls to `_edit_file(...)` with:

```
await _apply_logical_edit(...)
```

`_apply_logical_edit` chooses `edit_file` or shell edit based on
`logical_edit_index % 3`.

Run a semantic LSP mini-suite every 10 logical edits:

```
hover + definition + references + query_symbols + diagnostics
```

Each tool call must record a passed/failed `SandboxCheck`.

### Phase C: Refactor passes

Keep the existing refactor passes, but route the forward and revert edits
through `_apply_logical_edit`.

After each refactor:

- assert old symbol references are gone;
- assert new symbol references meet the expected floor;
- run diagnostics on all touched files and assert zero diagnostics.

### Phase D: Amplification

Keep amplification because it drives depth/squash, but route one third of the
forward/revert pairs through shell edit.

Avoid batching shell edits. One shell command equals one logical shell edit so
overlay capture count and shell edit latency remain measurable.

### Phase E: Diagnostic injection and repair

Add a dedicated LSP diagnostic phase before final pytest:

1. `write_file` a clean `_lsp_error_probe.py`.
2. shell edit it into a known syntax/type error.
3. assert diagnostics detects the error.
4. repair with `edit_file`.
5. assert diagnostics clears.
6. import the repaired module.

This phase must contribute at least 10 diagnostics checks in the full scenario.

### Phase F: Final validation and metrics

Run final pytest as in the existing scenario. Then run a final semantic LSP
sweep over fixture expectations until all per-tool floors are met.

Emit metrics to:

```
/ephemeral-os/.metrics/perf.json
/ephemeral-os/.metrics/summary.json
```

## 8. Metrics schema additions

Extend the emitted summary with:

```json
{
  "edit_routing": {
    "logical_edit_count": 0,
    "edit_file_edit_count": 0,
    "shell_edit_count": 0,
    "shell_edit_ratio": 0.0,
    "routing_rule": "logical_edit_index % 3 == 2"
  },
  "lsp_correctness": {
    "total_checks": 0,
    "passed_checks": 0,
    "failed_checks": 0,
    "by_tool": {
      "lsp.hover": 0,
      "lsp.find_definitions": 0,
      "lsp.find_references": 0,
      "lsp.query_symbols": 0,
      "lsp.diagnostics": 0
    }
  },
  "shell_edit": {
    "count": 0,
    "errors": 0,
    "overlay_capture_count": 0,
    "changed_paths_total": 0,
    "wall_seconds_p50": 0.0,
    "wall_seconds_p95": 0.0
  }
}
```

The performance artifact should preserve existing top-level keys:

```
tool_use
layer_stack
overlay
occ
phases
```

Add a new `shell_edit` section rather than overloading `overlay`.

## 9. Test assertions

The new test file should assert:

- `report.task_center_status == "done"`.
- `report.passed_sandbox_checks` is true.
- final pytest exit code is zero.
- JUnit XML reports zero failures and zero errors.
- `logical_edit_count >= 600` in full, `>=90` in smoke.
- shell edit ratio is one third within the tolerances in §4.
- full scenario has `total_lsp_checks >= 200`.
- full scenario has each LSP tool `>=40` semantic checks.
- smoke scenario has `total_lsp_checks >=25`.
- every semantic LSP check passed.
- diagnostics detected the intentionally broken probe file and then cleared
  after repair.
- tri-source read projection still agrees byte-for-byte.
- overlay capture count includes shell edits, not only pytest/import shell
  probes.
- `perf.json` and `summary.json` include the new sections from §8.

## 10. Implementation checklist

1. Add scenario classes and registry entries.
2. Add executor action routing in `backend/src/live_e2e/squad/runner.py`.
3. Add `complex_project_build_shell_edit_lsp_probe.py`, reusing as much of
   `complex_project_build_probe.py` as possible.
4. Add `_apply_logical_edit`, `_apply_shell_edit`, and LSP assertion helpers.
5. Add fixture-level LSP expectations.
6. Extend metrics aggregation or add a scenario-local wrapper for the new
   sections.
7. Add focused host fixture tests for expectation anchors.
8. Add smoke/full live test file.
9. Run host fixture tests.
10. Run smoke live test.
11. Run full live test with `EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1`.

## 11. Verification commands

Host-level checks:

```bash
uv run pytest backend/src/live_e2e/tests/sweevo/test_complex_project_build_fixtures.py -q
uv run pytest backend/src/live_e2e/tests/test_scenario_suite_imports.py -q
```

Smoke live check:

```bash
uv run pytest backend/src/live_e2e/tests/sweevo/test_complex_project_build_shell_edit_lsp.py::test_complex_project_build_shell_edit_lsp_smoke -q -s --tb=short
```

Full live check:

```bash
EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1 uv run pytest backend/src/live_e2e/tests/sweevo/test_complex_project_build_shell_edit_lsp.py::test_complex_project_build_shell_edit_lsp_full -q -s --tb=short
```

## 12. Risks and guardrails

- Shell edits will be slower than `edit_file` because they pay snapshot
  materialization, command execution, upperdir capture, and generic OCC apply.
  That cost is the point of the scenario, so report it separately instead of
  treating it as a regression by itself.
- Shell commands must not use git mutation commands. Current shell policy blocks
  destructive git mutations before execution.
- Avoid `sed -i`; use Python for deterministic text replacement and structured
  output.
- Do not weaken existing `complex_project_build` assertions. This is a sibling
  scenario with a different workload shape.
- LSP checks must assert expected results. Counting calls alone is not enough
  for this scenario.
