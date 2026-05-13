---
phase: "message-prompt (ad-hoc directory review)"
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - backend/src/message/__init__.py
  - backend/src/message/agent_message_recorder.py
  - backend/src/message/event_printer.py
  - backend/src/message/messages.py
  - backend/src/message/stream_events.py
  - backend/src/prompt/__init__.py
  - backend/src/prompt/environment.py
  - backend/src/prompt/message_recorder.py
  - backend/src/prompt/prompt_report_recorder.py
  - backend/src/prompt/runtime_prompt.py
  - backend/src/prompt/system_prompt.py
findings:
  critical: 0
  warning: 6
  info: 5
  total: 11
status: issues_found
---

# Phase: Code Review Report — `backend/src/message/` and `backend/src/prompt/`

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Review of the `message/` and `prompt/` packages after the engine refactor wave
that removed the background-reminder pathway and tightened the stream-event /
prompt surface. The packages compile, the per-file logic is mostly sane, and
all currently-imported symbols resolve. There are no security findings.

The dominant defect is a **documented-but-unimplemented set of features** in
`event_printer.py`: per-agent depth indentation, run-id-to-agent lineage, and a
`subagents_spawned` summary counter — all described in the module docstring,
none actually wired by `emit()`. Combined with several module-internal-only
exports (`render_section`, `render_template`, `MultiAgentEventPrinter.raw_line`,
`MultiAgentEventPrinter.summary`) and a counter-intuitive cross-package
dependency (`message/agent_message_recorder.py` reaching into
`prompt/message_recorder.py` for a generic JSONL writer), the packages carry
real cleanup debt that the upcoming follow-up pass can take in one bite. See
the **Legacy / Dead Candidates** section at the bottom.

No BLOCKER findings: nothing flagged here is actively wrong at runtime in the
ways tested today, but two WARNINGs (WR-01 and WR-02) describe latent
behavioral mismatches that could surface if any caller starts relying on the
documented behavior or on the `run_id`-disambiguation contract.

## Warnings

### WR-01: Documented `event_printer` features (depth indent, run_to_agent, subagents_spawned) are unimplemented

**File:** `backend/src/message/event_printer.py:194`, `:195`, `:160`, `:202-260`
**Issue:** The module docstring (lines 12-20) advertises three behaviors:

1. *"Lineage via bg task_id"* — a `BackgroundTaskStarted` with
   `tool_name == "run_subagent"` is supposed to register the spawned `task_id`
   as the child run's `run_id` so that child events indent one level deeper
   than the dispatching parent.
2. *"Color per agent"* + indented per-depth columns via `self._depth`.
3. *"Summary."* — `summary()` returns `subagents_spawned` per agent.

In practice:

- `self._depth` (line 194) and `self._run_to_agent` (line 195) are
  **initialized and then never written to**. `_depth.get(run_id, 0)` at
  line 345 always returns 0, so indentation is constant.
- `_AgentTotals.subagents_spawned` (line 160) defaults to 0 and is **never
  incremented**. `summary()` (lines 277-291) sums zeros.
- The `BackgroundTaskStarted` branch in `emit()` (lines 249-255) prints the
  start line but does not populate `_depth`, `_run_to_agent`, or
  `subagents_spawned`.

This is a documented-but-unimplemented feature, not just dead state. Because
`summary()` and `raw_line()` are themselves unused outside the module (see
IN-01), the runtime impact today is limited to the constant zero indent. But
the docstring is materially misleading for any future maintainer.

**Fix:** Either implement the wiring inside the `BackgroundTaskStarted` arm of
`emit()`:

```python
elif isinstance(event, BackgroundTaskStarted):
    detail = format_background_start_detail(event.tool_name, event.tool_input)
    self._line(
        agent, run_id,
        f"{self._c('blue', '>> bg_start:')}   {event.tool_name} "
        f"task_id={event.task_id}{detail}",
    )
    if event.tool_name == "run_subagent":
        totals.subagents_spawned += 1
        parent_depth = self._depth.get(run_id, 0) if run_id else 0
        self._depth[event.task_id] = parent_depth + 1
        self._run_to_agent[event.task_id] = (
            (event.tool_input or {}).get("agent_name") or ""
        )
```

…or delete the unused state and align the docstring with what the code
actually does. The advisor recommends the latter (less code) given that
`summary()` and `raw_line()` are themselves orphaned.

---

### WR-02: `AssistantMessageComplete` is emitted with empty `run_id` by the live engine, weakening lane-isolated flushing in `MultiAgentEventPrinter`

**File:** `backend/src/engine/query/loop.py:327` (constructs the event),
`backend/src/message/event_printer.py:256-258`, `:269`, `:334-340`,
`backend/src/message/stream_events.py:46-47` (defaults)
**Issue:** `AssistantMessageComplete` and `ToolExecutionCompleted` define
`agent_name: str = ""` and `run_id: str = ""` as defaults
(`stream_events.py:46-47`, `:71-72`). The non-test live engine construction at
`engine/query/loop.py:327` passes neither:

```python
yield AssistantMessageComplete(message=final_message, usage=state.usage), state.usage
```

When this event reaches `MultiAgentEventPrinter.emit()`:

- `agent = getattr(event, "agent_name", "") or "?"` resolves to `"?"`
  (line 203).
- The `AssistantMessageComplete` branch (line 258) calls
  `self._flush_buffers(agent, run_id)` with `run_id=""`.
- `_flush_buffers` (line 334) checks `if run_id:` — falsy — so it falls
  through to the *"flush every lane belonging to this agent"* branch
  (lines 338-340).

For the single-agent live path this happens to work because the only lane
keyed on agent `"?"` is the one whose deltas we want to flush. But the design
intent (and the docstring at lines 7-10) is that lanes are keyed on
`(agent_name, run_id)` precisely so concurrent agents don't clobber each
other. The moment the live engine starts multiplexing parents and children
into the same printer (which is exactly what the surrounding work appears to
be heading toward — see the run_subagent docstring at
`event_printer.py:12-15`), this "agent=`?`, run_id=``" cluster will silently
flush *every* `"?"` lane on every assistant boundary, including buffers
belonging to interleaved subagents.

**Fix:** Choose one of:

1. Make the live engine populate `agent_name` and `run_id` on every
   `AssistantMessageComplete` it yields (the squad runner at
   `live_e2e/squad/runner.py:1075-1090` already does this — be consistent in
   `engine/query/loop.py`).
2. If `run_id`/`agent_name` are genuinely optional, drop the
   `if run_id:` fallback in `_flush_buffers` and require explicit IDs at the
   call site rather than silently widening the flush scope.

The first option preserves the docstring contract with one extra argument at
the emit site.

---

### WR-03: `event_printer.MultiAgentEventPrinter.emit` matches `SystemNotification` after the `BackgroundTaskStarted` branch — order is fine, but the `elif` chain hides a sneaky correctness coupling

**File:** `backend/src/message/event_printer.py:202-261`
**Issue:** The `emit()` method is a single long `if/elif` chain dispatching on
`isinstance`. `SystemNotification` (from `notification.runtime`) is imported
into `stream_events.StreamEvent` (line 122 of `stream_events.py`) so it is a
member of the union, but `SystemNotification` is **not** a frozen dataclass
constructed by code under review — it is a Pydantic / dataclass type defined
elsewhere. If anyone ever derives a stream event from `SystemNotification`
(or substitutes a richer notification type that subclasses it), the
isinstance guard will keep catching the subclass and the parent type's
handler will silently run. Not a present-day bug — flagging as a maintenance
hazard because:

- `_format_run_id` (lines 373-374) and `_agent_tag` accept `run_id: str = ""`
  but the `SystemNotification` branch (line 260) calls
  `self._line(agent, run_id, …)` where `run_id` is taken from
  `getattr(event, "run_id", "")` at the top of `emit()`. If the notification
  type ever drops that attribute, the `getattr` default silently quiets the
  bug.

**Fix:** Either replace the `isinstance(event, X) elif isinstance(event, Y)`
chain with a single `match event:` (Python 3.10+ pattern matching), which
makes the dispatcher exhaustive and lets the type checker yell about new
variants — or explicitly assert at the end of `emit()` that no other
`StreamEvent` variant was missed.

---

### WR-04: `message` package imports from `prompt` package — dependency inversion smell

**File:** `backend/src/message/agent_message_recorder.py:23`
**Issue:**
```python
from prompt.message_recorder import append_prompt_report_event
```

`prompt/message_recorder.py` is a generic append-only JSONL writer with no
prompt-specific logic. Having the `message` package depend on the `prompt`
package for a shared utility is an inverted layering smell: domain-wise,
prompts are built *from* messages (system prompt → user message), not the
other way round. The misleading name (`append_prompt_report_event` in a
module called `message_recorder.py` that lives under `prompt/`) is itself
evidence of misplaced ownership.

**Fix:** Move `append_prompt_report_event` (and the `_json_default` helper)
to a neutral location — e.g. `backend/src/common/jsonl.py` or
`backend/src/persist/append_jsonl.py` — and re-import from there in both
`message/agent_message_recorder.py:23` and
`prompt/prompt_report_recorder.py:10`. Rename the function to something that
reflects what it actually does (`append_jsonl_event`).

---

### WR-05: `assistant_message_from_api` silently drops unrecognized block types and `tool_result` blocks

**File:** `backend/src/message/messages.py:174-193`
**Issue:** The function loops over `raw_message.content` and only appends
`thinking`, `text`, and `tool_use` blocks. Anything else — including the
sentinel `tool_result` blocks Anthropic does not currently return in
assistant responses, and any new block types the SDK adds (e.g.
`server_tool_use`, `mcp_tool_use`, `extended_thinking` variants, etc.) — is
silently dropped. There is no log, no metric, no test asserting the drop is
intentional.

**Fix:** At minimum, log at `DEBUG` when a block type is skipped so the
issue is diagnosable when a future provider response includes a new type:

```python
else:
    logger.debug(
        "assistant_message_from_api: dropping unrecognized block type %r",
        block_type,
    )
```

Or, more defensively, fall back to capturing the unknown block as a
`TextBlock(text=str(raw_block))` so downstream code at least sees that
content existed.

---

### WR-06: `prompt/runtime_prompt.py` exports `render_section` and `render_template` in `__all__` but no external consumer imports them

**File:** `backend/src/prompt/runtime_prompt.py:16-17`, `:21-52`
**Issue:** `__all__` advertises `render_section` and `render_template`
(lines 16-17). Both functions are defined in this module (lines 21-52) and
the only callers in the entire repository are *inside the same module*
(line 52: `render_section → render_template`; lines 67-75:
`build_runtime_system_prompt` uses `render_section`). They are not in
`prompt/__init__.py`'s `__all__`, no test references them, and the only
template they substitute today has a single hardcoded body
("Fast mode is enabled…") rendered with one boolean condition.

This is over-abstraction for one consumer and stale public API. Per
`CLAUDE.md` §2 "Simplicity First": "No abstractions for single-use code."

**Fix:** Either:

1. Drop `render_section` / `render_template` from `__all__` and keep them
   as `_render_section` / `_render_template` private helpers, or
2. Inline the one-line fast-mode rendering and delete both functions:

```python
sections: list[str] = [variables["base_prompt"]]
if settings.fast_mode:
    sections.append(
        "# Session Mode\nFast mode is enabled. Prefer concise replies, "
        "minimal tool use, and quicker progress over exhaustive exploration."
    )
```

Either choice removes 30+ lines of dead-ended template machinery.

## Info

### IN-01: `MultiAgentEventPrinter.raw_line` and `MultiAgentEventPrinter.summary` have no callers

**File:** `backend/src/message/event_printer.py:262-291`
**Issue:** Grep across `backend/src/` and `backend/tests/` returns zero
external callers for either method. `raw_line` is documented as "Used by
callers that don't produce `StreamEvent`s (e.g. the sweevo CLI tailing
pytest output in a sandbox)" — that caller has been refactored away.
`summary()` is described in the docstring at the top of the module but has
no consumer.
**Fix:** Delete both methods and update the module docstring (remove the
"Summary" bullet at lines 19-20).

---

### IN-02: `_depth` dict and `_run_to_agent` dict in `MultiAgentEventPrinter.__init__` are write-never, read-once-into-default

**File:** `backend/src/message/event_printer.py:194-195`, `:345`
**Issue:** `self._depth.get(run_id, 0)` at line 345 reliably returns 0
because nothing populates `_depth`. `self._run_to_agent` is never read or
written at all. This is dead state that survives because the dict accesses
look "used" syntactically.
**Fix:** Delete both lines and the `depth = self._depth.get(...)` read at
line 345 (replace with `depth = 0` or drop indent logic entirely). See
WR-01 for the documented-feature framing.

---

### IN-03: `prompt/system_prompt.py` is a one-line wrapper around `str.strip()`

**File:** `backend/src/prompt/system_prompt.py:6-15`
**Issue:** The module contains a single function:
```python
def build_system_prompt(agent_system_prompt: str | None = None) -> str:
    return (agent_system_prompt or "").strip()
```

Two callers in `prompt/runtime_prompt.py:62` and one in test files. This is
a thin abstraction over `(s or "").strip()` whose name implies it does more
than it does (callers reasonably expect "build" to assemble a prompt from
parts). Pre-existing, so per `CLAUDE.md` §3 ("clean up only your own mess"),
flagging as a *consolidation candidate* rather than a defect.
**Fix:** Optional. If kept, expand the docstring to clarify it is a
normalization helper, not an assembler. If removed, inline the strip at the
two call sites.

---

### IN-04: `format_background_start_detail` is a module-level helper that exists only because the printer's `emit()` does string-building inline

**File:** `backend/src/message/event_printer.py:142-153`
**Issue:** Used twice: by `event_printer.emit()` itself (line 250) and by
`tests/unit_test/test_engine/eval_agent_support.py:502`. Reasonable. No
defect — flagging for awareness only because the test usage means this
helper is part of the public surface and should keep its signature stable.

---

### IN-05: `event_printer.MultiAgentEventPrinter` uses local imports (`import time as _time`) inside `__init__` and `_line`

**File:** `backend/src/message/event_printer.py:185`, `:343-344`
**Issue:** `import time as _time` is repeated inside `__init__` (line 185)
and inside `_line` (line 343). The standard pattern is to import at the
module top. Marginal style nit but it adds a per-call import lookup on the
hot `_line` path (every printed line) — not measurable, but unusual. Note
that `_time` is shadowed: line 185 binds it as an instance helper alias to
`time.monotonic()`-providing module; line 343 re-imports it locally to do
the same. Pre-existing.
**Fix:** Move `import time` to the top of the module and drop the
function-local imports.

## Legacy / Dead Candidates (for follow-up cleanup pass)

This list collects orphans whose removal needs a small, scoped PR rather
than a behavior fix. They are referenced above but consolidated here so the
cleanup pass can target them in one bite.

| Symbol | Location | Status |
|--------|----------|--------|
| `MultiAgentEventPrinter.raw_line` | `message/event_printer.py:262-270` | zero callers in src/ or tests |
| `MultiAgentEventPrinter.summary` | `message/event_printer.py:276-291` | zero callers; summary counters never incremented |
| `MultiAgentEventPrinter._depth` | `message/event_printer.py:194`, `:345` | never written; always reads 0 |
| `MultiAgentEventPrinter._run_to_agent` | `message/event_printer.py:195` | never written, never read |
| `_AgentTotals.subagents_spawned` | `message/event_printer.py:160` | never incremented |
| `MultiAgentEventPrinter._format_run_id` | `message/event_printer.py:373-374` | identity function; could be inlined or removed |
| `render_section` | `prompt/runtime_prompt.py:39-52` | only internal caller |
| `render_template` | `prompt/runtime_prompt.py:21-36` | only internal caller (from `render_section`) |
| `prompt.system_prompt.build_system_prompt` | `prompt/system_prompt.py:6-15` | one-line wrapper around `(s or "").strip()` |
| Cross-package import | `message/agent_message_recorder.py:23` ← `prompt/message_recorder.py` | misplaced generic JSONL helper |
| Module docstring drift | `message/event_printer.py:12-20` | promises depth indentation, lineage, and `subagents_spawned` summary that aren't implemented |

**Suggested grouping for the cleanup pass:**

1. **Printer trim** — delete `raw_line`, `summary`, `_depth`, `_run_to_agent`,
   `subagents_spawned`, `_format_run_id`; update docstring accordingly.
2. **Prompt trim** — drop `render_section`/`render_template` from `__all__`
   (or delete them and inline the fast-mode block); decide whether
   `build_system_prompt` keeps its current shape.
3. **Move generic JSONL writer** out of `prompt/` to a neutral package and
   update both consumers (`message/agent_message_recorder.py:23`,
   `prompt/prompt_report_recorder.py:10`).

## Notes on what was checked and *not* flagged

A few things looked suspicious at first glance but turned out to be
intentional and are *not* defects:

- **`serialize_content_block` omits `does_terminate` and `metadata` on
  `ToolResultBlock`** (`messages.py:166-171`). The `does_terminate` docstring
  at `messages.py:42-45` explicitly says "Wire-irrelevant — never serialized
  to the provider," and `metadata` is engine-only state used by
  `agent_message_recorder` for local persistence. Correct as written.
- **`SystemNotificationBlock` serializes as a `text` block wrapped in
  `<system-reminder>` tags** (`messages.py:148-156`). Documented and
  intentional for provider-wire compatibility.
- **`ConversationMessage.to_api_param` filters out `ThinkingBlock`s**
  (`messages.py:124-137`). Documented as required by Anthropic.
- **`agent_message_recorder._record_message` swallows append exceptions at
  DEBUG level** (`agent_message_recorder.py:220-223`). Telemetry path —
  failing the run because a JSONL append failed would be worse than dropping
  the record. Acceptable.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
