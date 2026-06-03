# Independent verification — Terminal tools enforcement (agent-core)

Area: Terminal tools enforcement (called-alone, stamped terminating, dispatch/loop exit).
Verifier opened every Python and Rust anchor below directly. Trust nothing; re-derived.

## Critical false-match check (production `is_terminal` wiring) — CLEARED

The four matches (1/2/3/5) all hinge on `RegisteredTool.is_terminal` being true for the
production-registered `submit_*` tools. The tests hand-build tools with `terminal=true`, so
they would stay green even if production wiring were broken. Verified the production path:

- `model_tools/submission.rs::register()` registers each `submit_*` via `super::register_tool`
  passing **no** terminal bool (submission.rs:573-639).
- `model_tools/mod.rs::register_tool` (mod.rs:36-52) calls
  `RegisteredTool::new(name, meta::tool_intent(name), meta::is_terminal(name), …)`.
- `meta.rs:49-51`: `is_terminal(name) = TerminalTool::from_tool_name(name).is_some()`.
- `executor.rs:44,70-81`: `RegisteredTool` stores `is_terminal: bool`.
- `model_tools/mod.rs:115-116` test iterates production-registered terminals.

Conclusion: `stamp_terminal`, `reject_terminal_batch`, `first_terminal_result` all read the
correct production flag. No false match across 1/2/3/5.

## Invariant verdict table

| # | Invariant | Status | Rust anchor | Python anchor |
|---|-----------|--------|-------------|---------------|
| 1 | Terminal tool batched with siblings is rejected; no tool executes | confirmed_match | `eos-tools/src/dispatch.rs:63-103` `reject_terminal_batch` (`len()<=1→None`; any terminal→reject all, byte-exact msg); engine consumes at `eos-engine/src/tool_call/dispatch.rs:217-235` returns early, never builds `runnable` (test `terminal_batched_with_sibling_rejects_all` asserts 0 executions) | `engine/tool_call/dispatch.py:180-206` `_validate_tool_batch`; `dispatch.py:346-358` early return + `executor.cancel_all()` |
| 2 | Successful terminal stamped `is_terminal=True`; errored terminal not stamped | confirmed_match | `eos-tools/src/execution.rs:106-115` `stamp_terminal` (`if tool.is_terminal && !result.is_error`); test `stamps_terminal_on_success` | `_framework/execution/tool_call.py:197-198` `if tool.is_terminal_tool and not final.is_error: final = replace(final, is_terminal=True)` |
| 3 | Dispatch projects first terminal result; loop exits TOOL_STOP | confirmed_match | `eos-engine/src/tool_call/dispatch.rs:82-95` `first_terminal_result` + `:278-296` sets `ctx.terminal_result`; `loop_.rs:199-206` → `QueryExitReason::ToolStop` + break | `dispatch.py:165-177` `_first_terminal_tool_result`; `loop.py:298-310` `terminal_result` → `TOOL_STOP` + break |
| 4 | Terminal results are persisted task/workflow state inputs | confirmed_match | `submission.rs:162-167` SubmitRootOutcome → `task_store.set_task_status(…, Some(&terminal))` + `request_store.finish_request`; generator/reducer/planner → `PlanSubmissionPort` (`submit_generator`/`apply_reducer`/`apply_plan`); result block carries `is_terminal`+metadata `dispatch.rs:40-48` | `tool_call.py:121-127` ToolResultBlock carries `is_terminal`+metadata; submission tools persist via stores / orchestrator |
| 5 | Terminal set enumerated, matches Python (6 submit_* tools) | confirmed_match | `terminal.rs:17-70` closed enum of 6 + `from_tool_name`/`tool_name` round-trip; `meta.rs:49-51`; test `descriptors_total` asserts exactly 6 | 6 files under `tools/submission/{root,generator,reducer,planner,advisor,explorer}` each `is_terminal_tool=True`; `loop.py:123-128` derives terminal set from registry |
| extra | Hard ceiling `ceil(1.5*limit)` with `>=` over `(tool_calls_used + text_only_no_terminal_turns)` | confirmed_match | `loop_.rs:24-29` `ceiling = limit*3+1)/2 (sat); used + text_only >= ceiling` | `loop.py:41-47` `used + text_only >= math.ceil(1.5*limit)` |

### Ceiling-formula equivalence proof
`(L*3+1)//2 == ceil(1.5*L)` verified for L∈[0,11] (exhaustive script run). Integer-division
floor of `(3L+1)/2` equals the rational ceiling of `3L/2` for all non-negative L. Match exact.

## Investigator disparity adjudication

| ID | Investigator claim | Verdict | Reasoning |
|----|--------------------|---------|-----------|
| D1 | `terminal_not_submitted` message diverges (free-text vs structured) | confirmed (low) | Rust `loop_.rs:31-37` emits free text "The agent used {N} tool calls/text-only turns without submitting a terminal tool. Submit one of the terminal tools to finish the run." Python `loop.py:50-57` emits structured `Agent stopped: terminal tool not submitted. tool_calls_used=.., text_only_no_terminal_turns=.., tool_call_limit=.., hard_ceiling=..`. Different content; both errored run-failure results. User/log-facing only — no state impact. Severity low. |
| D2 | No background-task drain/cancel on terminal exit or loop teardown | confirmed (medium) | `BackgroundTaskSupervisor` EXISTS in `eos-engine/src/background/supervisor.rs` with `cancel`, `terminate_for_parent_exit`, `inflight_count`, but `loop_.rs` references `background` only at test line 282 — the production loop never instantiates/drains/cancels a supervisor, and `tool_call/dispatch.rs` has no `dispatch_background_tool_call` path. Python `loop.py:303-309` (`terminate_for_parent_exit` on TOOL_STOP), `:313-315` (`cancel_all` on TERMINAL_NOT_SUBMITTED), `:329-331` (finally `cancel_all`). This is a wholesale unported background subsystem, adjacent to terminal enforcement rather than a terminal-specific defect. The terminal exit signal/break itself is correct. |
| D3 | Hard-ceiling check fires one iteration later than Python after a non-terminal batch | adjusted → investigator_overstated (none/low) | Python checks at loop-bottom (`loop.py:313`) after counting; Rust checks at loop-top (`loop_.rs:103`) before the next provider request, plus after a text-only turn (`loop_.rs:180`). For `tool_call_limit>=1` with counters from 0, both suppress the post-crossing provider request: request count, failure event, and exit reason are identical. "One iteration later" implies an extra LLM round-trip that does not occur. Only genuine difference is the degenerate `tool_call_limit=0` boundary (Rust breaks with 0 requests at top; Python issues 1 request first). Not a real divergence for any realistic limit. |
| D4 | `StreamingToolExecutor`/`should_defer_tool` is dead scaffolding; deferral implicit | confirmed (low) | `streaming.rs:9-36` defines the types + `should_defer_tool` (returns `!terminal_tools.is_empty()`), but `loop_.rs` never references them; `loop_.rs:136-155` streams feed only budget counting, and `:187` dispatches ALL tools post-message. Deferral is implicit (no mid-stream execution exists), so exclusivity is still enforced before any sibling body runs. Dead code, not a behavioral defect. Python `loop.py:60-78,150-159` wires the deferrer into a live `StreamingToolExecutor`. |
| D5 | Post-dispatch tool-emitted notification flush not mirrored | adjusted → overstated/moot (low) | `NotificationService` (`notifications.rs:178-205`) has `notify_system`/`drain`, and `ExecutionMetadata.notifications` carries the sink (`metadata.rs:84`), but NO `eos-tools` executor calls `notify_system` mid-dispatch — nothing feeds the queue. With no producer there is nothing to flush. The Rust loop DOES drain rule-based notifications via `dispatch_rules` (`loop_.rs:108-117`), which is the live notification path. The unflushed-tool-queue gap is real only once a tool producer is ported; today it is inert. |

## New findings

- **NF1 (low):** `loop_.rs:103` checks `terminal_submission_failed` at the TOP of the loop, before
  `dispatch_rules` and before the provider request. Python checks at the BOTTOM (`loop.py:313`).
  Behaviorally equivalent for `tool_call_limit>=1` (see D3), but the placement also means the Rust
  `terminal_call_reminder` notification rule (`dispatch_rules`) does NOT fire on the iteration where
  the ceiling is hit (Rust breaks first). Python likewise breaks before re-entering, so this is also
  parity-equivalent — noted for completeness, not a defect.
- **NF2 (informational):** `terminal.rs` carries no `// PORT backend/src/...` line anchor; it uses a
  module-doc `Ports _terminals/registry.py` reference instead. Not load-bearing.
- **NF3 (low, confirms D2 scope):** `eos-engine/src/background/dispatch.rs::dispatch_background_tool_call`
  EXISTS as a function but is never called from `tool_call/dispatch.rs::dispatch_assistant_tools`
  (which dispatches everything foreground). Reinforces that the background subsystem is built but unwired,
  not absent.

## Overall verdict

The terminal-tool enforcement core is a faithful port. All five mandated invariants are
confirmed_match with verified production wiring (not just green tests): called-alone rejection
(`reject_terminal_batch`, byte-exact message), success-only stamping (`stamp_terminal`), first-terminal
projection + `ToolStop` exit, in-executor persistence of terminal state (`set_task_status`+`finish_request`
/ `PlanSubmissionPort`), and the exact 6-tool closed enum derived from `TerminalTool::from_tool_name`.
The hard-ceiling constant and `>=` operator match exactly (`(L*3+1)/2 == ceil(1.5L)`). The investigator's
real disparities are all peripheral and low/medium: a divergent failure-message string (D1), an unported
background-task supervisor subsystem (D2 — confirmed but adjacent to terminal enforcement), and dead
streaming/notification scaffolding (D4/D5). D3 ("ceiling fires one iteration later") is overstated — the
observable behavior is identical for any realistic `tool_call_limit`. No false matches were found.
