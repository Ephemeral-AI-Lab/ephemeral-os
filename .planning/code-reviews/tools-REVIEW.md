---
phase: ad-hoc-tools-review
reviewed: 2026-05-13T00:00:00Z
depth: deep
files_reviewed: 56
files_reviewed_list:
  - backend/src/tools/__init__.py
  - backend/src/tools/_framework/core/base.py
  - backend/src/tools/_framework/core/context.py
  - backend/src/tools/_framework/core/decorator.py
  - backend/src/tools/_framework/core/hooks.py
  - backend/src/tools/_framework/core/registry.py
  - backend/src/tools/_framework/core/results.py
  - backend/src/tools/_framework/core/runtime.py
  - backend/src/tools/_framework/core/validation.py
  - backend/src/tools/_framework/execution/hook_runner.py
  - backend/src/tools/_framework/execution/tool_call.py
  - backend/src/tools/_framework/factory.py
  - backend/src/tools/_framework/introspection/catalog.py
  - backend/src/tools/_framework/introspection/schema_summary.py
  - backend/src/tools/ask_helper/__init__.py
  - backend/src/tools/ask_helper/_lib/_compose.py
  - backend/src/tools/ask_helper/ask_advisor.py
  - backend/src/tools/ask_helper/ask_resolver.py
  - backend/src/tools/background/__init__.py
  - backend/src/tools/background/_lib/_common.py
  - backend/src/tools/background/cancel_background_task.py
  - backend/src/tools/background/check_background_task_result.py
  - backend/src/tools/background/wait_background_tasks.py
  - backend/src/tools/sandbox/__init__.py
  - backend/src/tools/sandbox/_lib/context.py
  - backend/src/tools/sandbox/_lib/file_payloads.py
  - backend/src/tools/sandbox/_lib/mutation_result.py
  - backend/src/tools/sandbox/_lib/registry.py
  - backend/src/tools/sandbox/_lib/session.py
  - backend/src/tools/sandbox/_lib/shell_policy.py
  - backend/src/tools/sandbox/edit_file.py
  - backend/src/tools/sandbox/read_file.py
  - backend/src/tools/sandbox/shell.py
  - backend/src/tools/sandbox/write_file.py
  - backend/src/tools/skills/__init__.py
  - backend/src/tools/skills/_factory.py
  - backend/src/tools/skills/load_skill.py
  - backend/src/tools/skills/load_skill_reference.py
  - backend/src/tools/subagent/__init__.py
  - backend/src/tools/subagent/_factory.py
  - backend/src/tools/subagent/run_subagent.py
  - backend/src/tools/submission/__init__.py
  - backend/src/tools/submission/_factory.py
  - backend/src/tools/submission/advisor/__init__.py
  - backend/src/tools/submission/advisor/submit_advisor_feedback.py
  - backend/src/tools/submission/context/__init__.py
  - backend/src/tools/submission/context/attempt.py
  - backend/src/tools/submission/context/executor.py
  - backend/src/tools/submission/evaluator/__init__.py
  - backend/src/tools/submission/evaluator/submit_evaluation_failure.py
  - backend/src/tools/submission/evaluator/submit_evaluation_success.py
  - backend/src/tools/submission/executor/__init__.py
  - backend/src/tools/submission/executor/request_mission_solution.py
  - backend/src/tools/submission/executor/submit_execution_failure.py
  - backend/src/tools/submission/executor/submit_execution_success.py
  - backend/src/tools/submission/explorer/submit_exploration_result.py
  - backend/src/tools/submission/notification_triggers/__init__.py
  - backend/src/tools/submission/notification_triggers/request_mission_after_edit.py
  - backend/src/tools/submission/notification_triggers/resolver_limit.py
  - backend/src/tools/submission/planner/__init__.py
  - backend/src/tools/submission/planner/_schemas.py
  - backend/src/tools/submission/planner/submit_full_plan.py
  - backend/src/tools/submission/planner/submit_partial_plan.py
  - backend/src/tools/submission/resolver/__init__.py
  - backend/src/tools/submission/resolver/submit_resolver_result.py
  - backend/src/tools/submission/resolver_history.py
  - backend/src/tools/submission/verifier/__init__.py
  - backend/src/tools/submission/verifier/submit_verification_failure.py
  - backend/src/tools/submission/verifier/submit_verification_success.py
findings:
  blocker: 1
  warning: 9
  info: 5
  total: 15
status: issues_found
---

# Ad-hoc Tools Package: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** deep
**Files Reviewed:** 56 source files across `backend/src/tools/`
**Status:** issues_found

## Summary

This is a deep adversarial review of the entire `backend/src/tools/` package
following the `Refactor tools package and harden sandbox runtime` (eb02f72e)
and `fix(sandbox): complete BLOCKER themes 1/3/6 + Theme 4 in-workspace edit`
(d27a2d08) commits. The framework split (core/execution/introspection) is
generally clean: registries are typed, hook contracts validate target tool
names at decoration time, and submission tools route through a unified
`resolve_executor_submission_context` / `resolve_attempt_submission_context`
seam.

The defects found are concentrated in three areas:

1. **Shell-policy gates** (`tools/sandbox/_lib/shell_policy.py`) — the
   regex-based dry-run detector misfires on common short-option strings,
   and the destructive-shell pattern only matches commands anchored at the
   start of a statement, leaking the protection through obvious shell
   indirections. The sandbox layer is the real isolation boundary, but the
   block messages overstate the policy's coverage.

2. **Registry / factory state** (`tools/_framework/factory.py` and
   `tools/subagent/_factory.py`) — `_ensure_builtins_registered` uses a
   name-sentinel that fails open when tools are renamed, factories overwrite
   silently on name collision, and `RestrictedRunSubagentTool` copies only a
   subset of `BaseTool` attributes from the delegate, so any future
   `pre_hooks` / `post_hooks` / `context_requirements` added to the
   `run_subagent` tool would be silently dropped on the registered surface.

3. **Introspection / API surface drift** — `make_skills_tools` is not
   reachable through `_register_builtins`, so the public catalog and
   schema-summary helpers silently omit `load_skill` / `load_skill_reference`,
   while `tools/__init__.py` advertises them in `_LAZY_EXPORTS`.

No injection vulnerabilities or credential leaks were identified inside
`tools/`. The two known security-adjacent items are defense-in-depth
gaps in the shell-policy prehooks, not actual escapes — sandbox commit /
write audit remains the load-bearing boundary.

## Blocker Issues

### BL-01: `_clean_args_are_dry_run` matches any short option containing the letter "n"

**File:** `backend/src/tools/sandbox/_lib/shell_policy.py:91-101`
**Issue:** The dry-run detector for `git clean` admits any short option that
contains the letter `n` after the leading dash:

```python
if arg.startswith("-") and "n" in arg[1:]:
    return True
```

This means `git clean -nx`, `git clean -ny`, or `git clean -ndnx <path>` are
treated as dry-runs and allowed through `DestructiveGitShellPreHook`, even
though only `-n`, `--dry-run`, or a `-n`-containing combined flag (e.g.
`-nfd`) is intended to imply dry-run. Worse, the loop short-circuits on the
first such match, so `git clean -an` (or any future short option containing
an `n`) bypasses the destructive guard even when `-n` was never specified.

This is BLOCKER-tier because the function is the lone gate distinguishing
dry-run `git clean` from working-tree-destroying `git clean` in this
prehook, and `clean` is in `_BLOCKED_GIT_SUBCOMMANDS` only via the dry-run
exception at line 143-146. Even though the sandbox commit pipeline is the
last line of defense, the prehook's stated purpose is to refuse the call
before it ever reaches the sandbox layer.

**Fix:** Tokenise short flags into individual letters before testing for
`n`. The intent is `-<combo>` contains the letter `n` as a flag, not
arbitrary substring match:

```python
def _clean_args_are_dry_run(args: list[str]) -> bool:
    for arg in args:
        if arg == "--":
            break
        if arg == "--dry-run":
            return True
        if arg.startswith("--"):
            continue
        if arg.startswith("-") and len(arg) > 1:
            # Combined short flags: each char is its own flag.
            if "n" in set(arg[1:]):
                # But only treat as dry-run when the bundle does NOT
                # also contain "f" (force) — git accepts -nf and still
                # requires explicit -f to actually delete. Keep symmetry
                # with git's own semantics by requiring -n alone or with
                # -d only.
                return True
    return False
```

Note: the safer behaviour is "fail closed" — any `git clean` that the parser
cannot unambiguously identify as dry-run should be denied. A unit test
should cover at minimum `-n`, `-nd`, `-nf`, `-ny` (bypass attempt), and
`--dry-run`.

## Warnings

### WR-01: Destructive-shell and git regex patterns miss common indirections

**File:** `backend/src/tools/sandbox/_lib/shell_policy.py:13-16, 65-75`
**Issue:** Both pattern anchors are `(?:^|[;&|]\s*)`. This blocks
`rm -rf /testbed` and `git rm <path>` only when they appear at the start of
a statement or immediately after `;`, `&&`, `||`, or `|`. The following
common shell forms are not caught:

- `$(git rm -rf .)` — command substitution
- `` `git reset --hard HEAD~5` `` — backticks
- `bash -c "rm -rf /testbed"` — wrapping shell invocation
- `eval "rm -rf /testbed"` — explicit eval
- `xargs -I{} git checkout {} <stuff>` — argv-fed mutator
- `find /testbed -delete` — alternative deletion verb that the regex does not
  enumerate at all (`mkfs`, `dd of=/...` are covered; `find -delete`,
  `truncate -s 0`, `> /testbed/...` are not).

Additionally `_BLOCKED_GIT_SUBCOMMANDS` (lines 39-57) omits subcommands that
also mutate state: `worktree`, `branch -D`, `tag -d`, `update-ref`,
`replace`, `notes`, `gc`, `prune`, `submodule add`/`update --init`,
`config --add`. The `_DESTRUCTIVE_GIT_MESSAGE` claims the prehook blocks
"destructive git commands and other git mutation commands"; the regex
delivers materially less than that.

**Fix:** Two separate fixes:

1. Soften the message ("Blocks a known set of destructive git mutation
   commands" rather than "destructive git commands and other git mutation
   commands are forbidden") so the prehook does not overpromise.
2. Either extend the subcommand list and add an `eval`/`bash -c`/backtick
   detection arm, OR explicitly document this as defense-in-depth that
   relies on the sandbox audit boundary. The current implementation is the
   worst of both worlds — the message claims a security guarantee the
   regex cannot deliver.

### WR-02: `RestrictedRunSubagentTool` drops `pre_hooks`, `post_hooks`, `context_requirements`, and `is_terminal_tool` from the delegate

**File:** `backend/src/tools/subagent/_factory.py:52-68`
**Issue:** `RestrictedRunSubagentTool.__init__` only copies `name`,
`description`, `short_description`, `input_model`, `output_model`,
`background`, and `task_type` from `run_subagent`. `BaseTool` defines
four additional contract attributes that govern execution:
`pre_hooks`, `post_hooks`, `is_terminal_tool`, and `context_requirements`.
Today `run_subagent` is decorated with none of them, so the omission is
silent — but adding any hook (e.g. a rate-limit prehook) to `run_subagent`
later will be invisible on the registered tool because `make_subagent_tools`
wraps it through this restricted shim.

Symmetrically, when the restricted shim's `input_model` is swapped to
`RestrictedRunSubagentInput`, `validate_hook_targets` is *not* re-run.
That's fine today (no hooks exist), but the implicit invariant that hook
target names match the wrapping tool's name is brittle.

**Fix:** Copy every attribute the framework reads off `BaseTool`. Cleanest
fix is to enumerate them explicitly:

```python
def __init__(self, *, allowed_agent_names: tuple[str, ...]) -> None:
    self._delegate = run_subagent
    for attr in (
        "name", "description", "short_description",
        "output_model", "background", "task_type",
        "is_terminal_tool", "pre_hooks", "post_hooks",
        "context_requirements",
    ):
        setattr(self, attr, getattr(run_subagent, attr))
    self.input_model = _build_restricted_input_model(allowed_agent_names)
    validate_hook_targets(
        tool_name=self.name,
        pre_hooks=tuple(self.pre_hooks or ()),
        post_hooks=tuple(self.post_hooks or ()),
    )
```

### WR-03: `_ensure_builtins_registered` sentinel breaks if either canary tool is renamed

**File:** `backend/src/tools/_framework/factory.py:87-90`
**Issue:**

```python
def _ensure_builtins_registered() -> None:
    if "run_subagent" in _factories and "read_file" in _factories:
        return
    _register_builtins()
```

This is an idempotency guard that hardcodes two tool names. If either tool
is renamed (and `read_file` recently was the subject of a sandbox refactor),
the guard falls through and `_register_builtins()` runs again on every
call to `has_tool` / `create_tool` / `list_available_tools`. Because
`register_tool_factory` (line 27-30) silently overwrites prior entries
(see WR-04), repeated re-registration is "safe" but wasteful, and obscures
collisions.

Worse, if a plugin's `register_plugin_tools()` has side effects (e.g.
network calls, file I/O, telemetry), those would run on every introspection
call rather than once.

**Fix:** Use an explicit module-level boolean:

```python
_builtins_registered = False

def _ensure_builtins_registered() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    _register_builtins()
    _builtins_registered = True
```

### WR-04: `register_tool_factory` silently overwrites existing entries

**File:** `backend/src/tools/_framework/factory.py:27-30`
**Issue:** `_factories[name] = factory` overwrites any existing
registration with no warning. Plugin name collisions (e.g. two plugins
both shipping a `read_file`) become invisible — last write wins.

This is particularly load-bearing because `_register_builtins` imports
`plugins.core.loader.register_plugin_tools` (line 74) and registers the
plugin tools *after* the builtins. A plugin can therefore shadow any
builtin tool without complaint.

**Fix:** Raise on duplicate registration unless an explicit
`override=True` argument is passed, or at minimum log a warning at
WARNING level when overwriting:

```python
def register_tool_factory(
    name: str, factory: ToolFactory, *, override: bool = False
) -> None:
    if name in _factories and not override:
        raise ValueError(
            f"Tool factory {name!r} already registered; "
            f"pass override=True to replace."
        )
    _factories[name] = factory
```

### WR-05: `make_skills_tools` is not reachable via `_register_builtins`, but is advertised in `_LAZY_EXPORTS`

**File:** `backend/src/tools/_framework/factory.py:72-84` and
`backend/src/tools/__init__.py:46`
**Issue:** `tools/__init__.py` exposes `make_skills_tools` in
`_LAZY_EXPORTS` and `__all__`, suggesting it's part of the same public
factory surface as `make_sandbox_tools`, `make_submission_tools`,
`make_ask_helper_tools`, and `make_background_tools`. However:

- `_register_builtins` (factory.py:72-84) does not call `make_skills_tools`
  because skill tools require a `SkillRegistry` argument that cannot be
  resolved at static registration time.
- Consequently `_iter_available_tools()` in
  `tools/_framework/introspection/catalog.py:20-22` and
  `collect_schema_tools` in `schema_summary.py:17-38` will never list
  `load_skill` or `load_skill_reference` in any auto-generated catalog,
  even though they are registered into per-agent tool registries through
  `make_skills_tools(registry, allowed_slugs=...)`.

This means the catalog under-reports the actual tool surface for any agent
that has skills enabled. Downstream consumers using `collect_tool_catalog`
to build UI tool docs will silently miss skill tools.

**Fix:** Either:
1. Drop `make_skills_tools` from the public `tools/__init__.py` surface and
   document skill loading as agent-scoped (caller supplies the registry).
2. Or have the catalog accept a `SkillRegistry` and expose skill tools when
   one is provided, with a separate code path.

### WR-06: `check_background_task_result` short-circuits delivery for cancelled subagent tasks but reports them as `failed`

**File:** `backend/src/tools/background/check_background_task_result.py:42-54, 116-119`
**Issue:** For a `run_subagent` task, `_build_subagent_result` (line 43-54)
returns `"failed"` for any non-`completed`/`delivered` raw status, including
`cancelled`. The caller may have *just cancelled the task on purpose* via
`cancel_background_task` — they will then see `status=failed` and the last
five messages, with no indication that the failure was their own action.

Lines 116-119 then call `manager.collect_completed()` only when
`raw_status in ("completed", "failed", "cancelled")`. The subagent
mapping conflates cancelled with failed in the surface output, but the
delivery-mark logic correctly distinguishes them. The two are inconsistent:
the surface says "failed", the bookkeeping says "cancelled". From the
agent's perspective, this makes cancel-then-check appear to be a subagent
crash.

**Fix:** Plumb an explicit `cancelled` status through `_build_subagent_result`
when `raw_status == "cancelled"`, so the caller sees the cancellation as
distinct from a spontaneous failure. At minimum, prefix the peek output
with `"[cancelled]"` so the agent doesn't blame the subagent.

### WR-07: `validate_tool_output` returns the raw `output` as a metadata field when output validation fails

**File:** `backend/src/tools/_framework/execution/hook_runner.py:329-335`
**Issue:** `_validate_hook_output` calls `validate_tool_output` which on
failure constructs `ToolResult(output=..., is_error=True)`. Lines 333-334
then return `validated.output` (the prose error message) as the failure
reason that the hook runner stitches into its hook-failure JSON. Two
problems:

1. The "output" of a *validation failure* result is a human-readable
   error string, not the original tool output. Embedding it into the hook
   failure trace is fine, but the variable is named `validation_error` while
   actually carrying a wrapped error message — readers may believe it's the
   raw pydantic error.
2. More importantly, `validate_tool_output` writes
   `output_validation_error` into the failed `ToolResult.metadata`. That
   metadata is dropped on the floor here — only the `output` string is
   propagated. Anyone post-mortem-ing a hook failure loses the structured
   validation error.

**Fix:** Either keep using `validate_tool_output` and surface its metadata
into the hook-failure trace explicitly, or skip the helper and call the
validator inline to retain both the formatted error and structured
metadata.

### WR-08: `_strip_runtime_control_fields` silently swallows unknown engine-level fields

**File:** `backend/src/tools/_framework/core/validation.py:150-158`
**Issue:** The helper removes only `background` (line 19) when the tool's
own input model does not declare it. Future engine-level schema
decorations (e.g. `timeout`, `priority`, `correlation_id`) would also need
stripping but would now leak into Pydantic validation as
`extra="forbid"` violations on the input models that declare it (e.g.
`EditFileInput.model_config = ConfigDict(extra="forbid")` in
`backend/src/tools/sandbox/edit_file.py:21`).

This is a maintainability landmine: the set of engine-level decorations is
defined exactly once in `decorate_schemas_for_background` (validation.py:107),
and the strip set is defined exactly once at line 19. They will drift the
moment a second decoration is added.

**Fix:** Either:
1. Centralise the runtime-control schema decorator and strip lists in a
   single registry (e.g. a `RUNTIME_CONTROL_FIELDS` constant containing
   `("background", ...)` shared by both functions).
2. Or document the invariant inline at both sites with a `# Keep in sync
   with ...` comment so the next change touches both.

### WR-09: `_on_spawned` lambda captures `agent.messages` without synchronisation

**File:** `backend/src/tools/subagent/run_subagent.py:203-212`
**Issue:** The `_on_spawned` callback registers a `progress_provider` that
closes over `agent.messages` (a plain list) and is invoked from the parent
task whenever `check_background_task_result` is called for the subagent.
The subagent itself is appending to that list on its own coroutine. In
asyncio with cooperative scheduling this is *probably* safe (no GIL-aware
data race because there's no `await` between read and the iteration in
`format_last_n_messages`), but:

- `format_last_n_messages` does `messages[-n:]` (snapshot) and then
  iterates `msg.content`. If `msg.content` is being mutated by the
  subagent during streaming-block construction, the iteration could see
  a partially constructed block. None of the block types have
  `__iter__` that yields mid-construction, but `TextBlock.text` is a
  mutable string-builder in some streaming code paths.
- More concretely: if the subagent finishes between the parent reading
  `tracked.result` (line 39 in check_background_task_result.py) and the
  parent calling `_peek_messages`, the result could change in flight.

This is INFO-grade in practice but worth surfacing — a snapshot helper
that copies `list(agent.messages)` before iteration would make the
contract explicit.

**Fix:** Capture an explicit snapshot at the start of
`format_last_n_messages`:

```python
def format_last_n_messages(messages: list[ConversationMessage], n: int) -> str:
    snapshot = list(messages)  # shallow copy under whichever event loop
    if not snapshot:
        return "(no messages yet)"
    n = min(n, PEEK_MESSAGE_MAX)
    tail = snapshot[-n:]
    ...
```

## Info

### IN-01: `resolve_sandbox_path` accepts any absolute path verbatim

**File:** `backend/src/tools/sandbox/_lib/session.py:29-36`
**Issue:** `resolve_sandbox_path` returns `path` unchanged if it
starts with `/`. The repo-root prefix is only joined for relative paths.
This means a tool input like `file_path="/etc/passwd"` is passed through
to the sandbox API without normalisation. The sandbox layer is the
authoritative boundary, but a `read_file` of `/etc/passwd` is an
information-disclosure path that depends entirely on the sandbox provider
correctly refusing or sandboxing it.

Note: this is INFO not WARNING because in normal operation the sandbox
runs inside an isolated rootfs and `/etc/passwd` is the sandbox image's
own copy, not the host's. Still, the tool layer should reject `/proc/`,
`/sys/`, `/dev/`, and host-only paths defensively when running in any
non-isolated sandbox provider.

**Fix:** Add a small allowlist or reject obvious system paths at the tool
layer; document the sandbox-provider trust assumption explicitly.

### IN-02: `execute_tool_body`'s `except Exception` swallows tracebacks

**File:** `backend/src/tools/_framework/core/validation.py:51-63`
**Issue:**

```python
async def execute_tool_body(...):
    try:
        return await tool.execute(parsed_input, context)
    except Exception as exc:
        return ToolResult(output=f"Tool execution failed: {exc}", is_error=True)
```

The `str(exc)` rendering loses the exception type and stack trace. Even
adding `{type(exc).__name__}: {exc}` would make production triage easier,
and `logger.exception(...)` would preserve the traceback for log
aggregation.

Mirror of the same issue in `tools/sandbox/shell.py:184-203` —
`_format_transport_exception` already includes the exception type. The
framework-level wrapper should follow that pattern.

**Fix:** Render the exception type and log the traceback through the
standard logger before returning.

### IN-03: Hook target validation runs twice for every tool

**File:** `backend/src/tools/_framework/core/decorator.py:66-70` and
`backend/src/tools/_framework/execution/hook_runner.py:38-42`
**Issue:** `validate_hook_targets` runs once at decoration time
(decorator.py:66) and a second time on every execution
(hook_runner.py:38). The second invocation is per-tool-call work for
zero benefit — hook target lists are immutable on `BaseTool` instances
in practice. Either the decoration-time check is the source of truth
(remove the runtime check) or the runtime check is (remove the
decoration check).

**Fix:** Remove the runtime check in `ToolHookExecutionHelper.__init__`.
Keep the decoration-time check as the contract enforcement point.

### IN-04: `_register_builtins` imports `register_plugin_tools` lazily but the import itself runs unconditionally on first builtins-registration

**File:** `backend/src/tools/_framework/factory.py:74`
**Issue:** Plugins are imported inside `_register_builtins` to avoid
circular imports, but that means every test that touches `has_tool` or
`create_tool` triggers `plugins.core.loader.register_plugin_tools()` and
all transitive imports. There is no opt-out for unit-tests that want to
exercise the framework in isolation.

This is informational because the import is small today; calling it out
because the pattern (lazy-import within builtin registration) tends to
grow. Consider an explicit `register_builtins_for_test()` that excludes
plugin loading.

### IN-05: `parse_tool_input`'s second `except Exception` is unreachable for non-pydantic errors

**File:** `backend/src/tools/_framework/core/validation.py:22-48`
**Issue:** `tool.input_model.model_validate(clean_input)` only raises
`ValidationError` from Pydantic, plus optionally `TypeError` if
`clean_input` is not a mapping. The `except Exception` arm at line 41
would only fire on programmer error (e.g. a custom validator raising
`RuntimeError`). The fallback message
`f"Invalid input for {tool.name}: {exc}"` would mislead the agent into
"please retry with valid arguments" when the failure is internal.

**Fix:** Either drop the second handler (let it propagate to
`execute_tool_body`'s wrapper) or change the message to indicate an
internal validation error.

---

## Cross-file observations (not finding-classed)

- The `_LAZY_EXPORTS` map in `tools/__init__.py:25-54` advertises
  `make_skills_tools` but, as noted in WR-05, the catalog/schema-summary
  helpers cannot enumerate skill tools. Consider whether to expose
  `make_skills_tools` from the top-level facade at all.
- `tools/submission/notification_triggers/__init__.py` is the only
  `__init__.py` in the submission tree with real (non-re-export) logic.
  It's correctly placed, but worth flagging in any future refactor — the
  rest of the submission package uses `_factory.py` files for "real logic"
  modules.
- `submission/explorer/__init__.py` is empty (size 0) — not a defect, just
  an opportunity to add the `__all__` declaration the other terminal-tool
  packages use for consistency.

---

## Test-coverage gaps observed

These are not findings but areas where the runtime behaviour is
non-trivial and unit coverage is thin:

- **`_clean_args_are_dry_run`** in `shell_policy.py` has no parametrised
  test for `-nx`, `-ny`, `--dry-run`, `-nfd`. See BL-01.
- **`_validate_hook_output`** in `hook_runner.py` is reached only when a
  post-hook returns a `ToolResult` whose output fails the tool's output
  schema. Tests should cover at least the JSON / RootModel split.
- **`_subagent_terminal_called`** in
  `check_background_task_result.py:31-40` reads `tracked.result.metadata`,
  which is set by `run_subagent` only on the happy path (line 246) and
  the crash paths (lines 231, 240). Verify the `cancelled` path stamps
  `subagent_terminal_called=False` somewhere (WR-06 may indicate it
  doesn't).
- **`register_tool_factory` collision behaviour** (WR-04) — no test
  asserts plugin/builtin shadowing semantics. Whichever direction the
  fix takes (raise vs warn), a regression test should pin it.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
