# Independent Verification — Query engine main loop (terminal-forced exit, not text-end)

Area: agent-core query engine main loop. Reviewer opened every Rust and Python
anchor and re-derived each invariant. Source precedence: Python = ground truth.

## Files opened
- Python: `backend/src/engine/query/loop.py`, `request.py`, `provider_history.py`;
  `backend/src/engine/tool_call/dispatch.py`, `streaming.py`;
  `backend/src/tools/_framework/execution/tool_call.py`;
  `backend/src/notification/rules/must_submit_terminal_tool.py`,
  `terminal_tool_call_count_reminder.py`.
- Rust: `agent-core/crates/eos-engine/src/query/loop_.rs`, `request.rs`, `context.rs`;
  `agent-core/crates/eos-engine/src/tool_call/dispatch.rs`, `streaming.rs`;
  `agent-core/crates/eos-engine/src/notifications.rs`;
  `agent-core/crates/eos-engine/src/background/supervisor.rs`;
  `agent-core/crates/eos-tools/src/execution.rs`.
- Docs: `docs/architecture/agent_loops/main-loop.html`.

## Invariant verdict table

| # | Invariant | Status | Rust anchor | Python anchor |
|---|-----------|--------|-------------|----------------|
| 1 | Loop ends ONLY on a terminal tool, not text/non-tool output | confirmed_match | `loop_.rs:177-185` (`tool_uses.is_empty()` -> `continue`), `199-206` (`is_terminal` -> `ToolStop; break`) | `loop.py:280-310` |
| 2 | Text-only / non-terminal tool call does NOT end loop | confirmed_match | `loop_.rs:184` `continue`; non-terminal tool batch falls through to top of loop | `loop.py:311-312,328` |
| 3 | Exit conditioned on SUCCESSFUL terminal stamp (errored terminal does not stop) | confirmed_match | stamp `execution.rs:106-110` (`is_terminal && !is_error`); filter `dispatch.rs:82-95`; gate `loop_.rs:199-206`; test `dispatch.rs:530-562` | stamp `tool_call.py:197-198`; filter `dispatch.py:165-177`; gate `loop.py:303` |
| 4 | Budget + hard ceiling integrated; `ceil(1.5×limit)`, `>=`, operand `tool_calls_used + text_only_no_terminal_turns` | confirmed_match | `loop_.rs:24-29` `(3L+1)/2`, `>=`, `tool_calls_used.saturating_add(text_only_no_terminal_turns)` | `loop.py:44-47` |
| 5 | Tool calls counted once per unique tool-use id; no double count | confirmed_match | `loop_.rs:140-142` (BTreeSet insert-gated +1 on delta), `169-174` (+1 for non-streamed final), dispatch never mutates `tool_calls_used` | `loop.py:199`; `dispatch.py:479,522`; `tool_call.py:80-81` |
| 6 | Budget-tier reminders 75/100/125%, fire-once, body | confirmed_match | tiers `notifications.rs:131-145`; fire_once `57-59`; trigger `80-85` `used*den >= limit*num`; body `115-122` | tiers `terminal_tool_call_count_reminder.py:14-18`; trigger `:32-35`; body `:37-48` |
| 7 | Repeating terminal_call_reminder fires every turn after first assistant msg while no terminal | confirmed_match | trigger `notifications.rs:71-74` (`!terminal_tools.is_empty() && any(Assistant)`); not fire_once `57-59` | `must_submit_terminal_tool.py:17-21,41` |
| 8 | On TOOL_STOP, terminate background tasks for parent exit | confirmed_disparity | NOT called in loop. `loop_.rs:199-206` only sets `ToolStop` + `break`. `terminate_for_parent_exit` exists (`supervisor.rs:197`) but loop never invokes it | `loop.py:303-307` `terminate_for_parent_exit()` |
| 9 | On TERMINAL_NOT_SUBMITTED, cancel all background tasks | confirmed_disparity | NOT done. `loop_.rs:84-96,103-106,180-183` only emit failure event + break; no cancel | `loop.py:313-315` `cancel_all()` |
| 10 | Background supervisor created + per-turn completion drain + finally-cancel | confirmed_disparity | loop never constructs `BackgroundTaskSupervisor`; `enable_background_tasks` only read for Debug (`context.rs:69,103`); no drain, no finally-cancel; `dispatch_assistant_tools(ctx, &tool_uses)` (`loop_.rs:187`) takes no supervisor; dispatch.rs has no background branch | `loop.py:113-116` create; `162-172,238-241` drain; `305-307` terminate; `313-315`/`329-331` cancel |
| 11 | Provider request uses sanitized history copy (sanitize_tool_sequence) | confirmed_disparity | `request.rs:25-30` builds from `messages.to_vec()` raw; `loop_.rs:120-128` records raw `messages`; no `provider_history` module; no sanitize anywhere in eos-engine | `request.py:8,29` `build_provider_messages`; `provider_history.py:20-39` |
| 12 | Mid-stream foreground tool execution + progress events when no terminal tools | confirmed_disparity (partial) | `streaming.rs:8-36` is a name-only deferred-tracker (`Vec<ToolName>`, no execute, no progress); `loop_.rs:136-155` only counts deltas; ALL execution in post-message `dispatch_assistant_tools` | `streaming.py:95-130` start mid-stream; `138-152` get_progress; `200-238` `_execute_tool` |
| 13 | Provider stream without final assistant message raises error | confirmed_match | `loop_.rs:157-162` returns `EngineError::Internal`; no mid-stream cancel (moot — no mid-stream execution) | `loop.py:214-222` raise RuntimeError + `executor.cancel_all()` |
| 14 | TERMINAL_NOT_SUBMITTED synthetic event shape + body | adjusted (event shape matches; body text diverges) | `loop_.rs:84-96` `ToolExecutionCompleted{tool_name:"", is_error:true}`; body `31-37` drops structured counts | `loop.py:316-323` same event; body `50-57` has `tool_calls_used=N,...,hard_ceiling=C` |
| 15 | Notifications drained into transcript at top of turn before request build | confirmed_match | `loop_.rs:108-117` `dispatch_rules` -> `append_notifications` before `build_query_run_request` (`:119`) | `loop.py:238-251` then build at `:254` |

## Disparity adjudication (investigator findings)

- **D1 (high, missing) — Background-supervisor lifecycle absent from Rust loop: CONFIRMED.**
  The investigator's claim "whole file never constructs/drains/cancels" is correct.
  Nuance worth recording (investigator slightly overstated absence of the *type*):
  the `BackgroundTaskSupervisor` struct, `terminate_for_parent_exit` (`supervisor.rs:197`),
  `cancel`, `inflight_count`, and `background/dispatch.rs` ALL exist in the crate. The
  gap is purely that the *query loop never wires them*: no construction, no per-turn
  `collect_*_completion_notifications` drain, no TOOL_STOP terminate, no
  TERMINAL_NOT_SUBMITTED cancel, no `finally` cancel, and `dispatch_assistant_tools`
  takes no `background_tasks` argument so background-policy tools cannot be launched.
  Doc `main-loop.html:61,95,107,185` explicitly requires this. Real, high severity.
  (This single root gap underlies invariants 8, 9, 10, and part of 12.)

- **D2 (high, missing) — Provider-message sanitization missing: CONFIRMED.**
  `request.rs:26` `.messages(messages.to_vec())` sends the raw durable transcript;
  no `sanitize_tool_sequence` / `_drop_unmatched_tool_blocks_in_place` /
  `reduce_background_task_history` equivalent exists. Python `provider_history.py`
  drops unmatched tool-use/result pairs and compacts background snapshots before every
  provider request (`request.py:29`). Doc `main-loop.html:57,96` requires it. Real, high.
  Note: the background-history compaction half of `build_provider_messages`
  (`reduce_background_task_history`) is moot in Rust today because no background tasks
  reach the loop (D1), but the unmatched-tool-pair dropping is independently load-bearing
  for provider correctness regardless of background work.

- **D3 (medium, partial) — Mid-stream foreground execution + progress not implemented: CONFIRMED.**
  Rust `streaming.rs` `StreamingToolExecutor` only stores `Vec<ToolName>` and never
  executes or emits progress. All Rust tool execution happens after the assistant
  message completes (`dispatch_assistant_tools`, `loop_.rs:187`). Python starts
  non-deferred tools as deltas arrive (`streaming.py:128` `_start_tool`) and streams
  `ToolExecutionProgressEvent`. Behavioral parity for *results* is preserved (Rust
  defers-all then runs the full batch foreground in parallel, `dispatch.rs:131-179`),
  so terminal/exit semantics are unaffected; the loss is latency/progress streaming,
  not loop correctness. Medium is appropriate.

- **D4 (low, divergent) — TERMINAL_NOT_SUBMITTED body drops structured counts: CONFIRMED.**
  Rust `loop_.rs:31-37` emits only a prose sentence with the summed count; Python
  `loop.py:50-57` includes `tool_calls_used=`, `text_only_no_terminal_turns=`,
  `tool_call_limit=`, `hard_ceiling=`. The event *shape* (`ToolExecutionCompleted`,
  empty `tool_name`, `is_error=true`) matches. Cosmetic; low severity, correctly rated.

## New findings

- **(N1, none) Hard-ceiling check is positioned differently but is behaviorally
  equivalent.** Python evaluates `terminal_submission_failed` at the BOTTOM of the
  turn (`loop.py:313`), so the TERMINAL_NOT_SUBMITTED event is emitted on the same
  turn the ceiling is crossed. Rust evaluates it at the TOP of the loop
  (`loop_.rs:103`) and re-checks after a text-only turn (`loop_.rs:180`). For
  tool-bearing turns Rust therefore emits the failure at the start of the *next*
  iteration. No additional provider request is issued past the ceiling in either
  version (the Rust top-of-loop gate fires before `build_query_run_request`), the
  threshold/operands/constant are identical, and the same exit reason is reached.
  This is an ordering equivalence, not a disparity. Note: the investigator's
  invariant-1/2 row text described the Python flow but did not flag this ordering;
  recording it so a future reviewer does not mistake it for a divergence.

- **(N2, none) Ceiling arithmetic is exactly equal across all three sites.**
  `ceil(1.5*L) == (3L+1)/2` (integer) == `(3L).div_ceil(2)` for every non-negative
  integer L (verified for even/odd L). Rust uses `(3L+1)/2` in the gate
  (`loop_.rs:25`) and `(3L).div_ceil(2)` in the reminder body (`notifications.rs:98`);
  both equal Python's `math.ceil(1.5*limit)`. No off-by-one.

- **(N3, none) Budget-tier trigger forms are algebraically identical.** Python
  `used >= ceil(L*num/den)` (`terminal_tool_call_count_reminder.py:21-22,34`) vs Rust
  `used*den >= L*num` (`notifications.rs:83-84`). For integers these are equivalent,
  and the `den==0`/`limit==0` guards (`notifications.rs:80`) prevent divide-by-zero
  the Python `_ceil_ratio` form sidesteps structurally. Match.

## Checks for FALSE MATCHES (primary worry)

I specifically re-derived invariants the investigator marked "match" to ensure none
hide a broken Rust path:
- Inv 3 (terminal stamp gating): Rust independently verified via `execution.rs:107`
  AND the dedicated test `terminal_tool_error_does_not_project_terminal_result`
  (`dispatch.rs:530-562`) which asserts `ctx.terminal_result.is_none()` after an
  errored terminal. Genuine match, not overstated.
- Inv 4/5 (counting + ceiling): traced the exact mutation sites; dispatch.rs never
  touches `tool_calls_used`, so no double-count. Genuine match.
- Inv 7 (repeating reminder): Rust `trigger` requires an Assistant message and is not
  `fire_once`; matches Python's `any(role=='assistant')` + `fire_once=False`. Genuine.
No false matches found.

## Overall verdict

The Rust query loop faithfully reproduces the CORE area dynamic the checklist targets:
terminal-forced exit. The loop drives provider turns and tool execution and exits
ONLY on a successful terminal-tool stamp (`is_terminal && !is_error`), never on
text-only or non-terminal-tool output; text-only turns increment a counter and
`continue`; the hard ceiling is `ceil(1.5×tool_call_limit)` over
`tool_calls_used + text_only_no_terminal_turns` with `>=`, exact to the constant.
Notification tiers, fire-once semantics, the repeating terminal reminder, top-of-turn
notification drain, and per-unique-id tool counting all match. All four investigator
disparities are CONFIRMED and correctly attributed, the most consequential being two
high-severity gaps that sit OUTSIDE the narrow terminal-exit dynamic: (1) the
background-task supervisor lifecycle is entirely unwired in the Rust loop (D1; the
supervisor type exists but is never constructed, drained, or cancelled, so neither the
TOOL_STOP parent-exit termination nor the TERMINAL_NOT_SUBMITTED cancel-all runs), and
(2) provider history is sent raw with no `sanitize_tool_sequence` equivalent (D2),
which can leak malformed/unmatched tool-use-result pairs to the provider. Mid-stream
execution/progress (D3) is a latency/UX regression that does not affect loop
correctness, and the TERMINAL_NOT_SUBMITTED body text (D4) is cosmetic. No false
matches detected.
