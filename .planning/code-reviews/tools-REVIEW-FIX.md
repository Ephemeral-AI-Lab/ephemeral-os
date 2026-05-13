---
review_path: .planning/code-reviews/tools-REVIEW.md
fix_scope: all
findings_in_scope: 15
fixed: 15
skipped: 0
status: all_fixed
iteration: 1
no_commit_mode: true
---

# Ad-hoc Tools Package: Code Review Fix Report

**Fixed at:** 2026-05-13
**Source review:** `.planning/code-reviews/tools-REVIEW.md`
**Iteration:** 1
**Mode:** no-commit (edits applied in place; user will review and commit)

## Summary

- Findings in scope: 15 (1 BLOCKER + 9 WARNING + 5 INFO)
- Fixed: 15
- Skipped: 0

All edits are surgical: only the cited lines and minimally adjacent code
were touched. No test files modified. No commits made.

**Files modified (9 fix-related):**

1. `backend/src/tools/sandbox/_lib/shell_policy.py` (BL-01, WR-01)
2. `backend/src/tools/subagent/_factory.py` (WR-02)
3. `backend/src/tools/_framework/factory.py` (WR-03, WR-04, IN-04, WR-05)
4. `backend/src/tools/__init__.py` (WR-05)
5. `backend/src/tools/background/check_background_task_result.py` (WR-06)
6. `backend/src/tools/_framework/execution/hook_runner.py` (WR-07, IN-03)
7. `backend/src/tools/_framework/core/validation.py` (WR-08, IN-02, IN-05)
8. `backend/src/tools/subagent/run_subagent.py` (WR-09)
9. `backend/src/tools/sandbox/_lib/session.py` (IN-01)

**Post-run scope-creep reverts by orchestrator (NOT fix-related, restored to HEAD):**

The fixer agent also touched the following files outside the 15 review
findings (likely "consistency cleanups" — exactly the kind of adjacent
churn the project CLAUDE.md surgical-edits rule forbids). These changes
were reverted to HEAD by the orchestrator:

- `backend/src/tools/_framework/core/runtime.py` — added unused
  `task_center_mission_id` field
- `backend/src/tools/ask_helper/_lib/_compose.py` — referenced the
  unused field
- `backend/src/tools/background/cancel_background_task.py` — refactored
  `manager.get_status()` → `manager.iter_running()` (works but
  out-of-scope)
- `backend/src/tools/__init__.py` — restored `create_tools` and
  `register_tool_instance` entries in `_LAZY_EXPORTS` / `__all__` that the
  fixer had removed
- `backend/src/tools/_framework/factory.py` — restored deleted
  `create_tools` helper function
- `backend/src/tools/subagent/_factory.py` — restored
  `RestrictedRunSubagentTool` in `__all__`

**Concurrent parallel work observed (left alone — NOT from fix):**

The user has a parallel codex session active on this branch. Two files
keep being re-applied post-revert; they are part of a separate
`start_mission_request` → `start_delegated_mission` rename refactor and
are not related to any review finding:

- `backend/src/tools/submission/context/executor.py`
- `backend/src/tools/submission/executor/request_mission_solution.py`

These show up under `git status` but should be reviewed and committed
(or reverted) by the user as part of their parallel work, not as part of
this fix.

**Verification:** All modified files pass `python3 -m ast.parse` (syntax
clean). Tests not run per prompt instruction (user has parallel work in
progress; tests may be unstable).

**Findings tagged "requires human verification"** (logic semantics, not
syntax): BL-01, WR-01, WR-04, WR-06, WR-08. These pass syntax checks but
the new allow/block boundaries should be confirmed by a human reviewer
against the project's threat model.

## Fixed Issues

### BL-01: `_clean_args_are_dry_run` matches any short option containing the letter "n"

**File:** `backend/src/tools/sandbox/_lib/shell_policy.py`
**Status:** fixed — requires human verification
**Applied fix:**
- Added module-level constant `_GIT_CLEAN_SHORT_FLAGS = frozenset("ndfxXqi")`
  enumerating the known short flags of `git clean` (`-n`, `-d`, `-f`, `-x`,
  `-X`, `-q`, `-i`). `-e` is excluded because it takes a value and cannot
  be combined.
- Rewrote `_clean_args_are_dry_run` to tokenise each combined short-flag
  bundle into a character set and require BOTH: (a) `n` is present in the
  bundle AND (b) every char in the bundle is in `_GIT_CLEAN_SHORT_FLAGS`.
- Fails closed: bundles like `-ny`, `-an`, `-no` are NOT treated as dry-run
  and the prehook will reject the call. Bundles like `-n`, `-nf`, `-nd`,
  `-nfd`, `-ndnx`, `--dry-run` ARE accepted as dry-run.

**Rationale for "requires human verification":** the canonical set of git
clean short flags may have evolved; `-e` is correctly excluded but a
future flag added to git would require updating `_GIT_CLEAN_SHORT_FLAGS`.
Tag pinning a regression test (e.g. parametrised cases for `-nx`, `-ny`,
`-an`, `-nfd`, `--dry-run`) is left as a follow-up.

**Discrepancy with review note:** The review's example fix
(`if "n" in set(arg[1:])`) does NOT actually fix the cited bug — `n in
{"n","x"}` is still `True`. The prompt's `n`/`r` allowlist hint contained
`r`, which is not a `git clean` short flag (likely a typo for `-d`/`-f`).
The implemented allowlist follows actual git semantics, not the literal
review/prompt text.

### WR-01: Destructive-shell and git regex patterns miss common indirections

**File:** `backend/src/tools/sandbox/_lib/shell_policy.py`
**Status:** fixed (partial — documented gap) — requires human verification
**Applied fix:**
- Extended `_BLOCKED_GIT_SUBCOMMANDS` to include: `branch`, `tag`,
  `worktree`, `update-ref`, `submodule`, `notes`, `prune`, `replace`. This
  closes the metadata-mutation gap for `branch -D`, `tag -d`, etc.
- Softened `_DESTRUCTIVE_GIT_MESSAGE` to say "a known set of destructive
  git mutation subcommands is forbidden here" instead of overpromising
  coverage. Explicitly notes that shell-substitution forms (`$(...)`,
  backticks, `bash -c`, `eval`) can bypass the prehook and that the
  sandbox commit/write audit is the authoritative boundary.

**Not applied (documented as residual gap in the new message):**
shell-substitution detection at the policy entry point. A safe rejection
of `$(...)`, backticks, `bash -c`, `eval`, `find -delete`, `xargs git...`
would require a non-trivial parser and risks blocking legitimate
workflows. The new message correctly states the residual gap rather than
overclaiming coverage. Per prompt guidance: "If a refactor is too large,
document the residual gap in the fix report under 'partial' status."

**Rationale for "requires human verification":** Subcommand additions
(`branch`, `tag`, etc.) also block read-only listing (e.g. `git branch`
with no args). This is consistent with how `stash` is already blocked
(which prevents `git stash list` too), but a human should confirm this
broader-block posture is acceptable.

### WR-02: `RestrictedRunSubagentTool` drops `pre_hooks`, `post_hooks`, `context_requirements`, `is_terminal_tool`

**File:** `backend/src/tools/subagent/_factory.py`
**Status:** fixed
**Applied fix:**
- Replaced manual attribute-by-attribute assignment with a loop over a
  defined attribute list: `name`, `description`, `short_description`,
  `output_model`, `background`, `task_type`, `is_terminal_tool`,
  `pre_hooks`, `post_hooks`, `context_requirements`. `input_model` is
  intentionally set separately (it's the only attribute that diverges
  from the delegate).
- Added `validate_hook_targets` call after attributes are copied so the
  invariant "hook target_tool matches wrapping tool's name" is checked
  even though `run_subagent` has no hooks today.
- Added `from tools._framework.core.hooks import validate_hook_targets`
  import.

### WR-03: `_ensure_builtins_registered` sentinel breaks if either canary tool is renamed

**File:** `backend/src/tools/_framework/factory.py`
**Status:** fixed
**Applied fix:**
- Added module-level `_builtins_registered: bool = False` flag.
- Rewrote `_ensure_builtins_registered` to short-circuit on
  `_builtins_registered and _factories`. The `_factories` truthiness check
  is critical for test-fixture isolation: when a fixture clears
  `_factories` between tests, the flag becomes stale; the falsy
  `_factories` causes a fall-through to re-register. (Without this, tests
  that rely on the `_isolate_tool_factories` fixture in
  `test_spawn_agent.py` would observe an empty registry on second-test
  invocation.)

### WR-04: `register_tool_factory` silently overwrites existing entries

**File:** `backend/src/tools/_framework/factory.py`
**Status:** fixed — requires human verification
**Applied fix:**
- Added keyword-only `override: bool = False` parameter to both
  `register_tool_factory` and `register_tool_instance`.
- Raises `ValueError` on duplicate registration unless `override=True` is
  passed.
- Verified all call sites: only internal callers (`_register_many` in the
  factory itself, four call sites within `_register_builtins`) and two
  test files (`test_spawn_agent.py`, `test_loader.py`, `test_lsp_catalog.py`).
  Test calls register tools with unique names per fixture (fixture clears
  `_factories` at start), so no test currently relies on overwrite
  semantics. No test files modified.

**Rationale for "requires human verification":** Plugin loaders may rely
on the previous overwrite-on-collision behaviour. With the new contract,
two plugins both defining `read_file` (or a plugin shadowing a builtin)
will now raise at load time rather than silently last-write-wins.
Behavioural change — review intentional shadowing patterns in the plugin
ecosystem.

### WR-05: `make_skills_tools` is not reachable via `_register_builtins`

**Files:** `backend/src/tools/__init__.py`, `backend/src/tools/_framework/factory.py`
**Status:** fixed
**Applied fix:**
Documentation-only fix (option 1 from the review, surgical version):
- Added a comment in `tools/__init__.py` near the `make_skills_tools`
  entry in `_LAZY_EXPORTS` explaining that it is agent-scoped (requires
  a `SkillRegistry` at call time) and NOT registered into the global
  factory map, so it will not appear in `collect_tool_catalog` /
  `collect_schema_tools` output. This is expected.
- Added a corresponding note to `_register_builtins`'s docstring so the
  omission is documented at the registration site too.

Per advisor guidance, a registry-resolution refactor was rejected as out
of surgical scope. The skill-loading helper remains in the lazy-export
surface for direct agent-build-time use (the documented pattern).

### WR-06: `check_background_task_result` reports cancelled subagent tasks as `failed`

**File:** `backend/src/tools/background/check_background_task_result.py`
**Status:** fixed — requires human verification
**Applied fix:**
- Added an explicit `raw_status == "cancelled"` branch in
  `_build_subagent_result` that returns `("cancelled", "[cancelled] ...")`
  with the peek-output prefix so the parent agent can distinguish "I
  cancelled this on purpose" from "the subagent crashed."
- Updated the tool's `description` to document the new `cancelled` status
  value and the `[cancelled]` prefix convention.
- `normalize_status` (for non-subagent tools, e.g. shell) was NOT
  modified — it still collapses cancelled to "failed". The finding scope
  was specifically subagent tasks; modifying the non-subagent path is
  outside the surgical bound. If desired, that's a separate change.

**Rationale for "requires human verification":** Downstream consumers may
read the `status` field as a closed enum of {running, finished, failed}.
A new `cancelled` value could need plumbing into consumer code paths
(e.g. UI rendering, metrics dashboards). Audit consumers before merging.

**Enum-consumer audit performed:**
- `normalize_status` (non-subagent path) still collapses `cancelled` to
  `failed`; its parametrised test at
  `test_engine/test_background_unit.py:89-97` is unaffected (the
  `("cancelled", "failed")` case still passes since `normalize_status`
  was not modified).
- No test in the repo asserts on the SUBAGENT cancelled-path output
  string (grep for `_build_subagent_result` and `cancelled` in tests
  returned a single hit, the `normalize_status` case above).
- UI/dashboard consumers (if any) were not exhaustively audited beyond
  the backend codebase — manual review still recommended before merging.

### WR-07: `validate_tool_output` metadata dropped on hook output validation failure

**File:** `backend/src/tools/_framework/execution/hook_runner.py`
**Status:** fixed
**Applied fix:**
- Changed `_validate_hook_output` return type from `str | None` to
  `tuple[str | None, dict[str, object]]`. On failure it now returns both
  the prose error AND the structured metadata lifted from the failed
  `ToolResult` (which carries `output_validation_error`).
- Updated the single caller in `run_post_hooks` to merge the validator's
  structured metadata into the hook's own metadata before passing to
  `_build_hook_failure_result`. The merge prefers validator keys (so
  `output_validation_error` is always present when validation fails),
  but preserves the hook's other metadata fields.
- No public-API change: `_validate_hook_output` is a private method;
  no external consumers.

### WR-08: `_strip_runtime_control_fields` silently swallows unknown engine-level fields

**File:** `backend/src/tools/_framework/core/validation.py`
**Status:** fixed — requires human verification
**Applied fix:**
- Added a detailed docstring comment on `_RUNTIME_CONTROL_FIELDS`
  describing the keep-in-sync invariant: any property name written into
  `props` by `decorate_schemas_for_background` MUST also appear in this
  set, or pydantic `extra="forbid"` models will reject the inflated
  input.
- Added a corresponding "Keep in sync with `_RUNTIME_CONTROL_FIELDS`"
  comment inside `decorate_schemas_for_background` at the `props["background"]
  = ...` site so the next developer to add a runtime-control field
  encounters both sides of the invariant.
- Did NOT centralise into a separate registry module — option 2 from the
  review (inline keep-in-sync comments) is more surgical and works
  without changing imports.

**Rationale for "requires human verification":** This is a doc-only
defensive change today, but the strip set is still maintained by hand. A
linter/test that asserts the equality at startup would be more robust;
left as a follow-up.

### WR-09: `_on_spawned` lambda captures `agent.messages` without synchronisation

**File:** `backend/src/tools/subagent/run_subagent.py`
**Status:** fixed
**Applied fix:**
- Changed the lambda inside `_on_spawned` from
  `lambda last_n: format_last_n_messages(agent.messages, last_n)` to
  `lambda last_n: format_last_n_messages(list(agent.messages), last_n)`.
- `list(agent.messages)` performs a shallow copy at the moment the
  progress provider is invoked. This snapshots the message tail under
  the same event loop so iteration in `format_last_n_messages` cannot
  observe a partially appended message.
- Did NOT modify `format_last_n_messages` itself (the review suggested
  doing the copy inside that helper). Per advisor guidance, pick one
  location; the lambda is closer to the source of the concern and
  leaves `format_last_n_messages` reusable from non-async paths.

### IN-01: `resolve_sandbox_path` accepts any absolute path verbatim

**File:** `backend/src/tools/sandbox/_lib/session.py`
**Status:** fixed
**Applied fix:**
- Added a single-line trust-boundary comment inside `resolve_sandbox_path`
  documenting that absolute paths are passed through verbatim by design
  and the sandbox provider (isolated rootfs) is the authoritative
  refusal layer.
- Per prompt guidance: kept comment minimal (no multi-paragraph
  docstring). Did NOT add tool-layer path rejection logic — the project
  memory note says "the provider isolates; prefer minimal comment."

### IN-02: `execute_tool_body`'s `except Exception` swallows tracebacks

**File:** `backend/src/tools/_framework/core/validation.py`
**Status:** fixed
**Applied fix:**
- Added `logging` import and module-level `logger`.
- Modified `execute_tool_body`'s exception path to:
  1. Include `type(exc).__name__` in the user-facing output (so the agent
     and triage can distinguish error types without prose parsing).
  2. Call `logger.exception("Tool execution failed: %s", tool.name)` to
     persist the full traceback in logs.
- Per advisor guidance: did NOT inline the traceback into
  `ToolResult.output` (would balloon LLM token consumption on every tool
  crash). Traceback stays in logs only.
- Public error structure (`ToolResult(output=..., is_error=True)`)
  unchanged; only the output string content changed.

### IN-03: Hook target validation runs twice for every tool

**File:** `backend/src/tools/_framework/execution/hook_runner.py`
**Status:** fixed
**Applied fix:**
- Removed the `validate_hook_targets` call from
  `ToolHookExecutionHelper.__init__` (the per-tool-call site).
- Removed the now-unused `validate_hook_targets` import.
- Added a comment in `__init__` documenting that hook-target validation
  is the `@tool` decorator's responsibility (decoration-time check is
  the contract enforcement point).
- The decorator-side validation in `core/decorator.py` is unchanged and
  remains the single source of truth.

### IN-04: `_register_builtins` runs plugin import unconditionally

**File:** `backend/src/tools/_framework/factory.py`
**Status:** fixed
**Applied fix:**
- Added `import os` and gated the plugin-loader import + invocation behind
  `if not os.environ.get("EOS_SKIP_PLUGIN_IMPORTS_FOR_TESTS")`.
- Documented the env var in `_register_builtins`'s docstring with the
  intended use case (unit tests exercising the framework in isolation).
- Moved `from plugins.core.loader import register_plugin_tools` from the
  top of the function to inside the `if` block so the import itself
  doesn't run when the env var is set. This preserves the lazy-import
  property the original code relied on (avoiding circular imports).

### IN-05: `parse_tool_input`'s secondary `except Exception` has misleading message

**File:** `backend/src/tools/_framework/core/validation.py`
**Status:** fixed
**Applied fix:**
- Changed the message from `f"Invalid input for {tool.name}: {exc}"` to
  `f"Internal validation error for {tool.name}: {type(exc).__name__}: {exc}"`.
- Removed the implicit "please retry" framing — this path indicates an
  internal failure (non-mapping input, custom validator bug), not bad
  agent arguments.
- Added `logger.exception(...)` to capture the traceback in logs.
- Kept the second handler in place (per prompt's "either" option), with
  the message corrected to reflect the actual failure mode.

---

_Fixed: 2026-05-13_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
_Mode: no-commit (user reviews combined diff)_
