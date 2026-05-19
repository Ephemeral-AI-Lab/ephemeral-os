# Drafted Tool Descriptions

One sophisticated draft per tool in `backend/src/tools/`. Structured with
the same information architecture as Claude Code's tool prompts:
one-line summary → Use when → Do NOT use for → Capabilities & constraints →
Output shape → Pitfalls → Examples (only where non-obvious).

These are **drafts**, not committed code. The matching file path is shown so
the description can be lifted into `_prompt.py` per the plan in `PLAN.md`.

Conventions:
- Second person throughout — the description addresses you, the caller.
- Other tool names appear as `` `tool_name` `` — final code should
  interpolate from `tools/_names.py` constants.
- Numeric caps appear symbolically (e.g. `MAX_READ_FILE_LINES`) — final
  code should f-string them from the actual Python constant.
- "Workspace" refers to the leased file tree you operate on; no internal
  infrastructure terms are exposed.

---

## Workspace tools

> File paths below point to the on-disk source location. The descriptive
> prose intentionally avoids exposing internal infrastructure terms.

### `shell` — `backend/src/tools/sandbox/shell.py`

```
Run a single bash command from the workspace root. You get captured stdout,
stderr, and exit code, and any file writes the command performs are tracked.

Use this when:
- You need to run tests, builds, linters, type-checkers, or other tooling
  (`pytest`, `make build`, `npm test`, `ruff check`).
- You need a capability not exposed as a dedicated tool (git operations,
  pip/uv/npm install, codegen).
- You're verifying environment state (`which python`, `git status`, `ls -la`).

Prefer dedicated tools when applicable:
- File reads → `read_file`, not `cat`/`head`/`tail`/`sed`.
- File mutations → `write_file` / `edit_file`. The dedicated tools produce
  cleaner audit trails and structured errors.
- Filename search → `glob`, not `find`/`ls`.
- Content search → `grep`, not `grep`/`rg` via shell.
- Use `shell` for genuine gaps (moves via `mv`, deletes via `rm`, git,
  codegen).

Do NOT use for:
- Long-running interactive processes (REPLs, watchers, dev servers). Each
  call is one-shot and bounded by `timeout`.
- Background daemons. There is no persistent shell session between calls;
  cwd resets to the workspace root each time.
- Streaming progress — you only get the final captured output.

Capabilities and constraints:
- Runs as bash, with the workspace root as cwd.
- `timeout` (seconds) bounds the run; default is 900.
- Writes performed by the command are tracked. A command that exits 0 but
  writes outside the audited boundary returns `is_error=True` with
  "commit aborted: ...".
- No environment leakage between calls — set env vars inline
  (`FOO=bar cmd ...`).
- No interactive input — use non-interactive flags (`--yes`,
  `--non-interactive`, `--no-input`).

Output shape:
- `status`: "ok" | "error".
- `changed_paths`: files changed by the command.
- `conflict_reason`: populated when the audit/commit step conflicts.
- `command`, `exit_code`, `stdout`, `stderr`: captured command output.
- `error`: populated when status is "error" — combines exit-code failures
  and audit conflicts.

Common pitfalls:
- Quoting: prefer single quotes around regexes and arguments containing `$`.
- Pipelines: pipe failures are masked unless you `set -o pipefail` inline.
- Background `&`: don't — the audit will not see the result, and you have
  no way to wait.
- `cd <dir> && ...`: cwd does not persist across calls; the form is fine
  within one call but useless across calls.
```

---

### `read_file` — `backend/src/tools/sandbox/read_file.py`

```
Read a UTF-8 text file from the workspace, returned with line numbers.

Use this when:
- You need the actual contents of a specific file path.
- You need to inspect a code/config region before editing it (`edit_file`
  requires you to read first).

Prefer over:
- `shell` with `cat`/`sed -n`/`head`/`tail` — `read_file` is cheaper,
  returns structured output, and integrates with the edit precondition
  check.

Do NOT use for:
- Binary files (PDF, images, archives) — output is UTF-8 only.
- Directory listings — use `glob`.
- Searching for content across many files — use `grep`.
- Re-reading a file you just edited — `edit_file`/`write_file` would have
  errored if the change failed; the harness already tracks the new
  content.

Capabilities and constraints:
- You can read up to MAX_READ_FILE_LINES (200) per call. Use `start_line`
  and `end_line` to page through larger files.
- Paths are workspace-relative or workspace-absolute. Paths outside the
  workspace return an error.
- Output is line-numbered with `cat -n` style prefixes
  (`<lineno><tab>line`), making it easy to cite specific lines
  (file_path:lineno).

Output shape:
- `file_path`: resolved path.
- `content`: numbered text block.
- `start_line`, `end_line`: window actually returned.
- `truncated`: True when more lines exist past `end_line`.

Common pitfalls:
- Indentation in `edit_file`: the line-number prefix is NOT part of file
  content. When you echo a line back into `edit_file.old_text`, drop the
  `<lineno><tab>` prefix.
- Stale reads: if another tool has changed the file since your last read,
  the next `edit_file` may return `aborted_version` — re-read and retry.
- Empty files return an empty `content`, not an error.
```

---

### `edit_file` — `backend/src/tools/sandbox/edit_file.py`

```
Apply one exact search-and-replace edit to an existing file.

Use this when:
- You want a targeted, minimal change to an existing file (rename a symbol
  in one spot, fix a line, add an import).
- The block you want to replace can be unambiguously identified by 2–6
  adjacent lines.

Prefer over:
- `write_file` — for ANY modification of an existing file. Use
  `write_file` only when you are creating a new file or intentionally
  rewriting the whole thing.
- `shell` with `sed`/`awk` — `edit_file` is atomic, audited, and refuses
  ambiguous matches instead of silently mangling.

Do NOT use for:
- Creating new files — use `write_file`.
- Renaming a symbol across the whole repo in one call — call `edit_file`
  once per file, or use `write_file` if the file needs a wholesale
  rewrite.

Required precondition:
- You MUST have read the target file with `read_file` in this conversation
  before calling `edit_file`. The tool will error otherwise — this
  protects you from blind edits with stale assumptions about file
  contents.

Capabilities and constraints:
- `old_text` must match byte-for-byte: whitespace, indentation, newlines,
  all included.
- `old_text` must be unique in the file. If it isn't, widen the match with
  surrounding context until it is — don't trim it to be terser.
- You cannot create new files. If the path doesn't exist, the call fails.
- Optimistic concurrency: if the file changed under you (e.g., another
  tool or test run wrote to it), the result is `aborted_version` —
  re-read, recompute `old_text`, and retry.

Output shape:
- `status`: "edited" | "aborted_version" | "failed".
- `changed_paths`: the edited file (and any side-effects from the audit
  layer).
- `applied_edits`: 1 on success.
- `conflict_reason`: populated when `status != "edited"`.

Common pitfalls:
- Including the `<lineno><tab>` prefix from `read_file` output in
  `old_text` — drop it; that prefix isn't in the file.
- Deleting a section with empty `new_text`: works, but make sure
  `old_text` includes the trailing newline so you don't leave a blank
  line.
- Using `edit_file` for find-and-replace across many occurrences in one
  file — split into multiple calls, or rewrite the file with
  `write_file`.

Example:
  # Good: 3 lines of context, unique match
  edit_file(
    file_path="src/foo.py",
    old_text="def bar(x: int) -> int:\n    return x * 2\n\n",
    new_text="def bar(x: int) -> int:\n    return x * 3\n\n",
  )
```

---

### `write_file` — `backend/src/tools/sandbox/write_file.py`

```
Create a new file, or completely overwrite an existing one, with UTF-8 text.

Use this when:
- You are creating a file from scratch.
- You are intentionally rewriting the entire contents of an existing file
  (e.g., a generated artifact, a config rewritten from a template).

Prefer over:
- `shell` with `echo >` or here-docs — `write_file` is atomic and
  audited; shell redirection is not.

Do NOT use for:
- Partial changes to an existing file — use `edit_file`. `write_file`
  will silently destroy any content you don't supply.
- Appending — there is no append mode. To add to a file, read it, then
  write the combined content.
- Creating directories — the parent directory must already exist. Use
  `shell` to `mkdir -p` first if needed.

Capabilities and constraints:
- The call always overwrites if the path exists. There is no "create if
  not exists" mode.
- UTF-8 text only. Binary content is not supported.
- Path must be workspace-relative or workspace-absolute.

Output shape:
- `status`: "written" or a failure status.
- `file_path`: resolved path.
- `bytes_written`: UTF-8 byte count of `content`.
- `changed_paths`: typically `[file_path]`.

Common pitfalls:
- Using `write_file` to "fix one line" — that's almost always wrong; use
  `edit_file`. Wholesale rewrites are reviewer-hostile and easy to get
  wrong.
- Forgetting the trailing newline — most repos expect files to end with
  `\n`.
```

---

### `grep` — `backend/src/tools/sandbox/grep.py`

```
Regex-scan workspace file contents.

Use this when:
- You need to find which files contain a pattern (`files_with_matches`
  mode).
- You need to count matches per file (`count` mode).
- You need to extract matching lines for inspection (`content` mode).

Prefer over:
- `shell` with `grep`/`rg` — `grep` is cheaper, routed read-only, and
  returns structured output.

Do NOT use for:
- Reading whole files — once you know the path, use `read_file`.
- Enumerating files by name (no content match) — use `glob`.
- Structural code search (AST-aware) — there is no `--type`-aware mode;
  combine `glob` to narrow scope, then call `grep`.

Capabilities and constraints:
- Pattern is Python `re` regex (NOT PCRE2). Possessive quantifiers and
  recursive groups are unsupported. Literal braces work without escaping.
- VCS directories (`.git`/`.svn`/`.hg`/`.bzr`/`.jj`/`.sl`) are excluded.
- Files larger than 10 MB and non-UTF-8 files are skipped silently.
- Output is capped at 20 KB total content AND `head_limit` entries
  (default 250; 0 = unlimited subject to the byte cap).
- `multiline=True` enables `re.MULTILINE | re.DOTALL` — `.` matches
  newlines, `^`/`$` match line boundaries.
- `glob_filter` is fnmatch (e.g. `'*.py'`), not bash glob.

Output shape:
- `mode`: "files_with_matches" | "count" | "content".
- `filenames`: matched files in scan order.
- `content`: rendered match content (`content` mode) or `path:count`
  lines (`count` mode); empty in `files_with_matches` mode.
- `num_files`, `num_lines`, `num_matches`: cardinalities.
- `applied_limit`, `applied_offset`, `truncated`: paging signals.

Common pitfalls:
- Forgetting `multiline=True` for cross-line patterns — the regex won't
  match across newlines by default.
- Over-broad scope: scanning the whole workspace for "TODO" returns
  truncated output. Pass `path=...` or `glob_filter=...` to narrow first.
- Confusing `head_limit=0` with "no results": 0 means "unlimited".

Example:
  # Find every place a symbol is defined
  grep(pattern=r"^class (Foo|Bar)\b", output_mode="content",
       line_numbers=True)
```

---

### `glob` — `backend/src/tools/sandbox/glob.py`

```
Enumerate workspace files matching a glob pattern.

Use this when:
- You need a list of files by name or extension (e.g. "every Python file
  in `pkg/`").
- You're narrowing scope before a more expensive operation (`grep`,
  `read_file` per file).

Prefer over:
- `shell` with `find`/`ls` — `glob` returns structured output.

Do NOT use for:
- Searching file CONTENTS — use `grep`.
- Recursive directory walks across hidden VCS data — `.git/`, `.svn/`,
  etc. are excluded by design.
- Following symlinks — symlinks are listed but not traversed.

Capabilities and constraints:
- Pattern is Python `fnmatch` style. `*` matches within a path segment;
  `**` does NOT recurse — set `path=...` plus a `*.py`-style narrowing
  instead.
- Brace expansion (`{a,b}`) is NOT supported.
- Leading-dot VCS directories are excluded.
- Result set is capped at 100 paths. Narrow with `path=...` when
  truncated.

Output shape:
- `filenames`: workspace-relative matched paths.
- `num_files`: count returned (post-cap).
- `truncated`: True when the cap was hit.

Common pitfalls:
- Expecting `**/*.py` to recurse — it does not. Use `path="src"` and
  `pattern="*.py"`, or call `glob` from a deeper `path` to scope down.
- Treating truncation as "all results" — check `truncated` before
  assuming completeness.
```

---

## subagent/

### `run_subagent` — `backend/src/tools/subagent/run_subagent.py`

```
Spawn a registered subagent as a background task. You hand it `prompt` as
its only input. It must finish by calling its terminal tool; whatever that
terminal tool emits becomes your result.

Use this when:
- You need to delegate a focused, context-isolated investigation (e.g.,
  "where is X used across the repo?") so your context isn't polluted by
  intermediate tool output.
- You can launch multiple independent investigations and want to run them
  in parallel — fire them all in a single message with multiple
  `run_subagent` calls.

Do NOT use for:
- Work you'd handle in 1–3 of your own tool calls — direct execution
  beats subagent overhead.
- Spawning further subagents from inside a subagent — that path is
  rejected at validation time. Handle the work directly, or submit your
  findings via your own terminal tool.
- Tasks that need shared context with you — the subagent does NOT
  inherit your conversation. The `prompt` is the only channel.

Writing the prompt:
Brief the subagent like a smart colleague who just walked into the room.
It hasn't seen your conversation, doesn't know what you've tried, doesn't
understand why the task matters.
- Explain what you're trying to accomplish and why.
- Include the exact paths, symbol names, or commands you'd run yourself.
- Specify what's in scope and what's out of scope.
- Tell it what shape of answer you want ("report in under 200 words",
  "list the file paths").
- Terse command-style prompts produce shallow, generic work.

Don't peek. The launch returns a `task_id`; the subagent runs in the
background. Don't read its transcript or poll progress unless the user
explicitly asks for a status check — that defeats the point of forking
off its tool noise. You'll be notified when it completes.

Don't race. After launching, you know nothing about what the subagent
will find. Never predict its result in any format. If the user asks a
follow-up before completion, give status, not a guess.

Capabilities and constraints:
- The launch returns immediately with a `task_id`.
- Peek progress with `check_background_task_result(task_id)` — you get
  the last few messages while it's running, the terminal output once it
  finishes.
- Block on completion with `wait_background_tasks`.
- A subagent that exits without calling a terminal tool is marked
  failed.

Output shape:
- On success: the terminal tool's output text (e.g., the
  `submit_exploration_result.summary`).
- On crash: `is_error=True`; the output explains the crash.
- On exit without terminal: `is_error=True`, "subagent exited without
  calling a terminal tool".
- Metadata includes `subagent_terminal_called` so `check`/`wait` can
  distinguish "finished cleanly" from "finished without delivering".

Example:
  run_subagent(
    agent_name="explorer",
    prompt=(
      "Find every call site of "
      "`AttemptOrchestrator.apply_evaluator_submission` in backend/src "
      "and report (file, line, calling function). The signature is "
      "changing in PR #842; I need the punch list of files to update. "
      "Report under 200 words."
    ),
  )
```

---

## background/

### `check_background_task_result` — `backend/src/tools/background/check_background_task_result.py`

```
Fetch the current result of one background task by id.

Use this when:
- You need to peek at a running subagent's progress (you get the last few
  messages).
- You need to retrieve the terminal output of a task that has finished.
- A `[BACKGROUND COMPLETED]` notification arrived and you want the full
  result.

Do NOT use for:
- Polling — once you call this on a finished task, the engine treats it
  as delivered and won't re-send the completion notification. Call once
  per task, when you actually need the result.
- "Are there any tasks?" — use `wait_background_tasks` with a short
  timeout, or rely on the listing exposed by `cancel_background_task`
  errors.

Capabilities and constraints:
- For `run_subagent`: you get the terminal-tool output if finished, or
  the last 5 messages otherwise (prefixed with `[cancelled]` when you
  cancelled the task).
- For other backgroundable tools (e.g., `shell`): you get the full
  output verbatim once finished, a progress snapshot while running.
- This call marks completed tasks as delivered as a side effect — see
  above.

Output shape (JSON):
- `id`: the task id.
- `status`: "running" | "finished" | "failed" | "cancelled".
- `tool_command`: the rendered original tool invocation, for context.
- `result`: terminal output or progress peek.

Common pitfalls:
- Calling this preemptively on a still-running subagent — you get a
  peek, not a result. Either wait for the notification or call
  `wait_background_tasks` if you need to block.
```

---

### `wait_background_tasks` — `backend/src/tools/background/wait_background_tasks.py`

```
Block until every running background task settles, or `timeout` expires.

Use this when:
- You've launched parallel work (multiple `run_subagent` /
  backgrounded `shell`) and your next planning step depends on all of it
  finishing.
- You want a synchronization barrier before a verification/submission
  step.

Do NOT use for:
- "Just give me one task's result" — call `check_background_task_result`
  on the specific id; that's cheaper than blocking on everything.
- Indefinite waits — `timeout` is bounded to [1, 300] seconds; schema
  validation rejects anything outside.

Capabilities and constraints:
- You get one compact entry per task: `task_id`, `status`
  (`running`|`finished`|`failed`), and `tool_command`.
- This call does NOT return result bodies — call
  `check_background_task_result` per task to fetch each.
- Newly completed tasks are marked delivered so the engine does not
  double-emit `[BACKGROUND COMPLETED]` messages.
- The call returns immediately with a "no tasks" snapshot when nothing
  is running.

Output shape:
- Rendered snapshot text (`wait_completed` | `wait_timed_out` |
  `wait_no_tasks`) listing each task's status.
- Metadata mirrors the snapshot for tool-call consumers.

Common pitfalls:
- Treating "timed out" as failure: it isn't. Tasks are still running;
  either call again with more timeout, or cancel them explicitly.
- Calling `wait` when you only have one task —
  `check_background_task_result` on its id is more direct.
```

---

### `cancel_background_task` — `backend/src/tools/background/cancel_background_task.py`

```
Cancel a running background task by id, or the sole running task with
`task_id="auto"`.

Use this when:
- You launched a subagent or backgrounded shell command that's no longer
  useful (wrong scope, superseded, the user changed direction).
- The task is wasting tokens/time and you want it to stop early.
  Subagents will be interrupted and salvage any partial result before
  reaching a terminal state.

Do NOT use for:
- Cancelling all tasks at once — `task_id="all"` is rejected. Cancel
  each task explicitly so your choice is auditable.
- Stopping finished tasks — those return an error.

Capabilities and constraints:
- Mutating: the task is marked cancelled and removed from the running
  pool.
- `task_id="auto"` resolves to the sole running task; if 0 or >1 are
  running, you get an error with a listing.
- For subagents: cancellation requests a graceful early-stop; the
  subagent is interrupted and may emit a partial terminal result before
  settling.
- `reason` is optional but recommended — it's surfaced in the
  cancellation message and helps post-hoc analysis.

Output shape:
- Plaintext acknowledgement, including the reason when you supplied
  one.

Common pitfalls:
- Cancelling a task whose result you actually need — the cancelled
  status returns `[cancelled]` and a peek, NOT the would-be terminal
  output.
```

---

## ask_helper/

### `ask_advisor` — `backend/src/tools/ask_helper/ask_advisor.py`

```
Ask the advisor for a blocking, read-only audit of the terminal submission
you're about to make.

Use this when:
- You're about to call a terminal tool (e.g., `submit_execution_success`,
  `submit_verification_success`, `submit_plan_closes_goal`) and you want a
  second pair of eyes on (1) tool selection and (2) whether the work you've
  done actually supports the payload.
- The submission is high-stakes (closes a goal, marks an attempt
  complete).

Do NOT use for:
- Trivial submissions where the right terminal is unambiguous and the
  work is obvious (e.g., a short summary acknowledging an already-passed
  eval).
- Fixing problems — the advisor only audits and cannot edit. Use
  `ask_resolver` when issues need to be addressed.

Capabilities and constraints:
- Read-only. The advisor cannot mutate files.
- The advisor sees your original task and contract, a filtered version
  of your transcript, the terminal-tool catalog (with each terminal's
  review focus), and the submission you're about to make.
- Lenient approve bar: the advisor approves when your tool choice is
  right and your payload is plausibly supported, even if the work isn't
  pristine. It rejects only on real quality problems (wrong terminal,
  stubs, TODOs, unsupported claims).
- You get back `approve` / `reject` plus a summary covering: tool
  selection, payload-vs-work support, residual risks.

Input shape:
- `tool_name`: the terminal you intend to call.
- `tool_payload`: the exact arguments you'd pass.

Output shape:
- The advisor's summary text, with verdict in metadata.

Common pitfalls:
- Calling `ask_advisor` AFTER submitting the terminal — too late. Call
  BEFORE.
- Ignoring a prior `reject` and re-asking with the same payload — a
  caller that ignores prior feedback warrants a sharper second reject.
```

---

### `ask_resolver` — `backend/src/tools/ask_helper/ask_resolver.py`

```
Ask the resolver to address unresolved verifier or evaluator issues. The
resolver may edit files and submits via `submit_resolver_result`.

Use this when:
- A verifier or evaluator has surfaced concrete issues (failing checks,
  missing artifacts, wrong outputs) and you want a focused agent to fix
  them before you re-submit.
- The issues are well-described enough to act on without a fresh planning
  pass.

Do NOT use for:
- Read-only review — use `ask_advisor`. The resolver edits files; the
  advisor does not.
- Open-ended replanning — the resolver works from your contract,
  transcript, and issue list; it doesn't re-derive the plan.

Capabilities and constraints:
- The resolver has edit access (`edit_file`, `write_file`, etc.) inside
  your workspace.
- It sees your original task and contract, a filtered version of your
  transcript, your `issues_to_resolve`, and the optional
  `issue_context`.
- It terminates via
  `submit_resolver_result(verdict, summary, changed_files,
  remaining_issues)`.

Input shape:
- `issues_to_resolve`: bullet list of concrete issues (≥ 1 required).
- `issue_context`: optional free-form additional context.

Output shape:
- The resolver's summary text, with resolution status in metadata.

Common pitfalls:
- Issues phrased vaguely ("make it better") — the resolver needs
  falsifiable problems ("`test_foo` fails with `ZeroDivisionError` on
  line 42").
- Treating an unresolved result as failure — it's a signal that some
  issues remain. Check `remaining_issues` and decide whether to re-ask
  or escalate.
```

---

## submission/ (terminal tools)

These are terminal tools — calling one ends your current agent run and
stamps your outcome. Each draft below is written for the agent that calls
the tool.

### `submit_execution_success` — `backend/src/tools/submission/executor/submit_execution_success.py`

```
Terminate your executor run with SUCCESS for the current generator task.

Call this when:
- You've completed the assigned executor task and the deliverable is in
  place (file created, edits applied, command run with the expected
  effect).
- You can list the concrete artifacts you produced.

Do NOT call this when:
- Any acceptance criterion is unmet — use `submit_execution_failure`.
- The task is beyond your scope or too complex to solve in one shot —
  use `submit_execution_handoff` to delegate to the planner.
- You haven't actually performed the work yet — terminate only after
  your changes are durable.

Inputs:
- `summary`: 1–3 sentence factual recap of what you did. No filler.
- `artifacts`: list of concrete artifacts (file paths, command IDs) the
  caller can verify.

Behavior:
- Records evaluator-visible success on the attempt's task. The
  orchestrator advances the DAG.
```

---

### `submit_execution_failure` — `backend/src/tools/submission/executor/submit_execution_failure.py`

```
Terminate your executor run with FAILURE for the current generator task.

Call this when:
- You attempted the task but cannot complete it (environmental block,
  contradictory constraints, missing dependency you can't supply).
- You've made enough attempts that further retries inside this run are
  unlikely to succeed.

Do NOT call this when:
- You haven't actually attempted the task — try first.
- The task is solvable but needs delegation or replanning — use
  `submit_execution_handoff` instead.
- You succeeded — use `submit_execution_success`.

Inputs:
- `summary`: 1–3 sentence factual recap of what blocked you.
- `reason`: short category-like label ("env", "missing_dependency",
  "contradictory_spec", "out_of_scope").
- `details`: bullet list of concrete evidence (command outputs, file
  paths, symptoms) that justifies the failure.

Behavior:
- Records evaluator-visible failure. The orchestrator may replan or
  escalate.
```

---

### `submit_execution_handoff` — `backend/src/tools/submission/executor/submit_execution_handoff.py`

```
Request a delegated complex-task solution for the current generator task.
This terminates your executor run and bounces the task back to the
planner.

Call this when:
- The task is genuinely too complex for a single executor pass (requires
  multi-step planning, fan-out to subagents, or cross-file
  coordination).
- You've assessed the scope before making edits — and have not yet
  edited.
- A cleaner break-up into smaller sub-tasks would produce a more
  reliable outcome.

You MUST call this BEFORE making edits. If you've already started
editing, finish what you can and use `submit_execution_success` or
`submit_execution_failure` instead.

Do NOT call this when:
- The task is bounded and doable — just do it.
- You're stuck on an environment issue — that's
  `submit_execution_failure`, not a handoff.

Inputs:
- `goal`: the higher-level goal the planner should re-plan against. Be
  specific about what's hard and what shape of decomposition you'd
  suggest.

Behavior:
- Hands the task back to the planner with the proposed goal; spawns a
  fresh planning iteration.
```

---

### `submit_verification_success` — `backend/src/tools/submission/verifier/submit_verification_success.py`

```
Terminate your verifier run with SUCCESS for the current generator task.

Call this when:
- Every check you ran passed.
- The artifacts produced by the executor actually exist and behave as
  specified.

Do NOT call this when:
- Any check failed, was skipped, or is unverifiable — use
  `submit_verification_failure` with the unresolved issues.

Inputs:
- `summary`: 1–3 sentence recap of what you verified.
- `checks`: list of the concrete verifications you performed (commands
  run, invariants asserted, files inspected). One entry per check.

Behavior:
- Records your verifier pass on the attempt. The orchestrator advances.
```

---

### `submit_verification_failure` — `backend/src/tools/submission/verifier/submit_verification_failure.py`

```
Terminate your verifier run with FAILURE for the current generator task.

Call this when:
- One or more checks failed, were skipped, or could not be performed.
- Artifacts the executor claimed to produce are missing or incorrect.

Do NOT call this when:
- Everything passed — use `submit_verification_success`.
- The issue is fixable inline by a resolver — call `ask_resolver` first
  to attempt a fix, then verify again.

Inputs:
- `summary`: 1–3 sentence recap of what failed.
- `unresolved_issues`: concrete, falsifiable issues. Each entry should
  name what was checked, what was expected, what was observed.

Behavior:
- Records your verifier failure on the attempt. The orchestrator may
  replan or escalate.
```

---

### `submit_evaluation_success` — `backend/src/tools/submission/evaluator/submit_evaluation_success.py`

```
Terminate your evaluator run with SUCCESS for the current attempt.

Call this when:
- Every criterion in the plan's `evaluation_criteria` is satisfied by
  the artifacts produced.
- The attempt as a whole meets its acceptance bar.

Do NOT call this when:
- Any criterion failed — use `submit_evaluation_failure`.
- You haven't actually checked the criteria against the artifacts — do
  that first.

Inputs:
- `summary`: 1–3 sentence recap of the evaluation outcome.
- `passed_criteria`: list of criteria the attempt passed (echo from the
  plan; do not invent new ones).

Behavior:
- Records your evaluator pass on the attempt and closes the iteration's
  goal.
```

---

### `submit_evaluation_failure` — `backend/src/tools/submission/evaluator/submit_evaluation_failure.py`

```
Terminate your evaluator run with FAILURE for the current attempt.

Call this when:
- One or more evaluation criteria are not met.
- The attempt's artifacts do not satisfy the plan's acceptance bar.

Inputs:
- `summary`: 1–3 sentence recap, citing specific gaps.
- `failed_criteria`: list of criteria that did not pass (echo from the
  plan; do not invent new ones).

Behavior:
- Records your evaluator failure on the attempt. The orchestrator may
  replan or spawn a follow-up iteration.
```

---

### `submit_plan_closes_goal` — `backend/src/tools/submission/planner/submit_plan_closes_goal.py`

```
Submit a plan that closes the goal on evaluator PASS (one bounded
iteration, no continuation).

Call this when:
- The goal can be fully delivered within this iteration — no follow-on
  slice is needed.
- Your `evaluation_criteria` cover every requirement; once they pass,
  the goal is done.

Do NOT call this when:
- The goal is too large or risky for one iteration — use
  `submit_plan_defers_goal` and articulate the next-iteration slice.
- You haven't decomposed into tasks yet — planning isn't done.

Inputs:
- `plan_spec`: high-level plan rationale (what, why, scope of this
  iteration). Nonblank.
- `evaluation_criteria`: list of falsifiable acceptance criteria. ≥ 1
  entry, each nonblank.
- `tasks`: ordered list of task descriptors. ≥ 1 entry. Each entry is
  an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor/verifier agent
      dispatchable by the planner.
    - `deps`: list of other task `id`s that must complete first
      (default `[]`). Cycles and unknown deps are rejected.
- `task_specs`: map of `task.id` → detailed spec text. Every task `id`
  must appear; no extras allowed. Each spec is nonblank.

Behavior:
- Records the plan with `closes_goal=True`. The orchestrator
  instantiates the task DAG and runs it.
```

---

### `submit_plan_defers_goal` — `backend/src/tools/submission/planner/submit_plan_defers_goal.py`

```
Submit a plan that delivers a bounded slice of the goal in this iteration
and defers the remainder to a follow-up iteration.

Call this when:
- The full goal is too large or risky to complete safely in one
  iteration.
- You can articulate a bounded slice that is independently valuable AND
  a clear `deferred_goal_for_next_iteration` describing what's left.

Do NOT call this when:
- The full goal fits in one iteration — use `submit_plan_closes_goal`.
- You haven't decided what to defer — that's a planning signal, not a
  slicing one.

Inputs (this iteration's plan):
- `plan_spec`: high-level rationale for THIS iteration's slice (what,
  why, scope). Nonblank.
- `evaluation_criteria`: list of falsifiable acceptance criteria for
  THIS iteration's slice. ≥ 1 entry, each nonblank.
- `tasks`: ordered list of task descriptors for THIS iteration. ≥ 1
  entry. Each entry is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor/verifier agent
      dispatchable by the planner.
    - `deps`: list of other task `id`s that must complete first
      (default `[]`). Cycles and unknown deps are rejected.
- `task_specs`: map of `task.id` → detailed spec text. Every task `id`
  must appear; no extras allowed. Each spec is nonblank.

Input (next iteration's seed):
- `deferred_goal_for_next_iteration`: prose describing the bounded
  remainder. Nonblank. After THIS iteration's evaluator passes, the
  orchestrator spawns a new iteration seeded with this string as its
  goal.

Behavior:
- Records the plan with `closes_goal=False`. On evaluator PASS, the
  next iteration is spawned automatically from
  `deferred_goal_for_next_iteration`.
```

---

### `submit_resolver_result` — `backend/src/tools/submission/resolver/submit_resolver_result.py`

> Note: the current schema uses `resolved: bool`. The draft below also
> covers a proposed `verdict: Literal["resolved", "partially_resolved",
> "unresolved"]` alternative that would replace the boolean with a
> finer-grained signal — useful because partial fixes are common in
> practice and the parent reacts differently to "fixed most of it" vs.
> "fixed nothing".

```
Terminate the resolver with your outcome.

Call this when:
- You've attempted to address the `issues_to_resolve` passed to you via
  `ask_resolver` and you're ready to report back.

Inputs (current schema):
- `resolved`: True if every issue is addressed; False otherwise.
- `summary`: 1–3 sentence recap of what you changed and why.
- `changed_files`: list of files you modified.
- `remaining_issues`: issues you could NOT resolve (empty when
  `resolved=True`).

Proposed schema (consider replacing `resolved: bool` with `verdict`):
- `verdict`: Literal["resolved", "partially_resolved", "unresolved"]:
    - `resolved` — every issue addressed. `remaining_issues` must be
      empty.
    - `partially_resolved` — some issues addressed, others remain.
      `changed_files` should be non-empty AND `remaining_issues` should
      be non-empty.
    - `unresolved` — none of the issues were addressed. `changed_files`
      may be empty.
- Other fields unchanged.

Case: every issue addressed
- Set `resolved=True` (or `verdict="resolved"`).
- `summary` lists the fixes by issue.
- `changed_files` lists the edited paths.
- `remaining_issues` is empty.
- Use when each issue from `issues_to_resolve` has been demonstrably
  fixed AND you have evidence (commands run, files inspected).

Case: some issues addressed, others remain
- Set `resolved=False` (or `verdict="partially_resolved"`).
- `summary` explains what you fixed AND what you couldn't, in order.
- `changed_files` lists the edited paths.
- `remaining_issues` lists each unfixed issue verbatim with a one-line
  reason ("requires a planning pass", "blocked by missing dep",
  "ambiguous: needs caller decision").
- Use when partial progress is real and the caller can decide whether
  to re-ask or escalate.

Case: nothing could be addressed
- Set `resolved=False` (or `verdict="unresolved"`).
- `summary` states why no fixes were applied (e.g., issues were
  contradictory, scope unclear, blocked by environment).
- `changed_files` may be empty.
- `remaining_issues` echoes the inbound issues.
- Use when the right answer is "the caller needs to clarify or
  escalate", not "try harder inside the resolver".

Do NOT:
- Set `resolved=True` while leaving `remaining_issues` non-empty —
  that's a contradiction.
- Submit with no summary, no changed files, and no remaining issues —
  the caller has no signal to act on.

Behavior:
- The summary is returned to the caller via `ask_resolver`'s result.
  The caller decides whether to re-verify, re-ask, or escalate based
  on the verdict (or `resolved` flag) plus `remaining_issues`.
```

---

### `submit_advisor_feedback` — `backend/src/tools/submission/advisor/submit_advisor_feedback.py`

```
Terminate the advisor with your verdict + summary.

Call this exactly once when:
- You've reviewed the caller's pending terminal submission against
  their contract and transcript.

Inputs:
- `verdict`: "approve" or "reject".
- `summary`: focused prose covering, in order:
  1. Tool selection — "correct" or "should be <other_tool>" with a
     one-sentence rationale.
  2. Quality of the work backing the payload — what's solid, what's
     unsupported. Quote transcript lines or contract fragments.
  3. Residual risks (or "None") — what the caller should weigh on
     approve, or the single most important fix on reject.

Behavior:
- The verdict + summary is returned to the caller via `ask_advisor`.
  The caller decides whether to proceed with their submission or
  revise.
```

---

### `submit_exploration_result` — `backend/src/tools/submission/explorer/submit_exploration_result.py`

```
Terminate as an explorer subagent with your read-only findings.

Call this when:
- You've completed the investigation you were spawned to do.
- You can present your findings with verifiable references (paths,
  line numbers, command outputs).

Inputs:
- `summary`: 1–3 sentence recap answering your original question
  directly.
- `findings`: bullet list of concrete observations.
- `references`: list of citable evidence (e.g.,
  `path/to/file.py:42`, `git log` excerpts) that backs each finding.

Behavior:
- Your summary is returned to the caller via `run_subagent`'s result.

Style:
- You are read-only. Do not propose changes; describe what is.
- Cite evidence. A finding without a reference is just an assertion.
```

---

## Notes for implementation

1. **Naming constants.** Every backticked tool name in these drafts
   (`` `read_file` ``, `` `edit_file` ``, etc.) should map to a single
   source of truth in `backend/src/tools/_names.py`.
2. **Numeric constants.** Where prose says `MAX_READ_FILE_LINES (200)`
   or `head_limit ... default 250`, the final implementation should
   f-string from the actual Python constant so the docstring cannot
   drift.
3. **Shared fragments.** Repeated wording in this draft (e.g., "Paths
   are workspace-relative or workspace-absolute", audit-boundary
   language) should be extracted to `tools/_prompt_fragments.py` and
   composed in.
4. **Per-tool `_prompt.py`.** Each tool's draft becomes a
   `get_<tool>_description() -> str` function in a sibling
   `_prompt.py`, wired into the `@tool(description=...)` decorator
   call. See `PLAN.md`.
5. **`submit_resolver_result` schema decision.** Before lifting the
   draft into code, decide whether to keep `resolved: bool` or move to
   the proposed `verdict` Literal. The drafted description covers both
   shapes; the implementation should match exactly one.
