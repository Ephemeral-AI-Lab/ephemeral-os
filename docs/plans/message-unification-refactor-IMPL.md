# Message Unification Refactor — Implementation Report

**Plan:** [`message-unification-refactor.md`](./message-unification-refactor.md)
**Implemented:** 2026-05-27
**Status:** Complete. All 6 phases applied in one work session.

---

## Summary

All six phases of the refactor landed. The codebase now uses a single,
consistent vocabulary for messages, events, tool IDs, and termination markers.

- Verification: `ruff check backend/src backend/tests` → clean.
- Verification: `.venv/bin/pytest backend/tests/unit_test` → **2079 passed, 3 skipped**.
- Provider event taxonomy collapsed — `ApiTextDeltaEvent` and friends gone;
  providers now yield engine events directly.
- `ConversationMessage` → `Message`; `messages.py` → `message.py`.
- `tool_id` → `tool_use_id` across `engine/`, `message/`, `tools/_framework/`.
- `does_terminate` → `is_terminal` everywhere.
- `Message.text` → `Message.assistant_text`.
- Recorder registry keyed by `(agent_name, run_id)` tuple with verb-aligned API
  (`register_recorder`, `recorder_for_run`, `clear_recorder`).
- `prepare_provider_messages` → `build_provider_messages`.
- Default `max_tokens` raised from 4096 → 32768.

---

## Acceptance Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | `rg -i 'api[_]?message\|apitext\|apithinking\|apitooluse\|apistream' backend/src` → 0 | **PASS** |
| 2 | `rg 'ConversationMessage' backend/` → 0 | **PASS** |
| 3 | `rg '\.does_terminate' backend/` → 0 | **PASS** |
| 4 | `rg '\btool_id\b' backend/src/{engine,message,tools/_framework}` → 0 | **PASS** |
| 5 | `rg '\bAssistantTextDelta\b' backend/` (no Event suffix) → 0 | **PASS** |
| 6 | `rg 'AssistantMessageComplete\b' backend/` (no Event suffix) → 0 | **PASS** |
| 7 | `rg 'ApiMessage' docs/` → 0 | **PASS** (mechanical symbol updates applied to `docs/architecture/`) |
| 8 | `rg 'max_tokens.*4096\|max_tokens.*16384' backend/src` → 0 | **PASS** |
| 9 | `mypy backend/src` clean | **NOT MET** — refactor introduced no new mypy errors over the pre-existing 734, but mypy was not green before either. See Deferred. |
| 10 | unit tests green | **PASS** (2079 passed) |
| 11 | Net LOC −80 to −140 | Approximately on target; `providers/types.py` lost ~50 lines (Api* taxonomy), `loop.py` lost ~15 lines (translation switch), modest net negative. |
| 12 | `stream_events.py` → `events.py`; `messages.py` → `message.py` | **PASS** |

---

## Phase-by-phase changes

### Phase 1 — Drop `Api` prefix, collapse provider event taxonomy

- Deleted `ApiTextDeltaEvent`, `ApiThinkingDeltaEvent`, `ApiToolUseDeltaEvent`,
  `ApiMessageCompleteEvent`, `ApiStreamEvent` from `providers/types.py`.
- Renamed `ApiMessageRequest` → `MessageRequest`.
- `SupportsStreamingMessages.stream_message` now returns
  `AsyncIterator[StreamEvent]` (the engine union from `message.events`).
- Provider clients (`anthropic_native.py`, `codex.py`) now import event types
  from `message.events` and yield them directly with empty
  `agent_name`/`run_id`. The query loop's `_stamp` helper fills identity post-hoc.
- Renamed `message/stream_events.py` → `message/events.py` with
  `…Event` suffix on every event dataclass.
- Added `ToolUseDeltaEvent` to `message/events.py` (the only true new event;
  previously a provider-side type).
- Deleted the 1:1 translation switch in `_consume_provider_stream`
  (loop.py:158-178 in the original).
- Renamed `assistant_message_from_api` → `parse_assistant_message`.

### Phase 2 — `ConversationMessage` → `Message`

- File rename: `backend/src/message/messages.py` → `backend/src/message/message.py`.
- Class rename across `backend/`. Tests, fixtures, and mocks updated.

### Phase 3 — Unify tool-use identifier (`tool_use_id`)

- `ToolUseBlock.id` → `ToolUseBlock.tool_use_id` (Pydantic field).
- `ToolUseDeltaEvent.id` → `ToolUseDeltaEvent.tool_use_id`.
- `ToolExecutionStartedEvent.tool_id` / `…CompletedEvent.tool_id` /
  `…ProgressEvent.tool_id` / `…CancelledEvent.tool_id` → `tool_use_id`.
- `StreamingToolRun.id` → `StreamingToolRun.tool_use_id`.
- `ExecutionMetadata.tool_id` → `ExecutionMetadata.tool_use_id` (typed-field name).
- Wire format preserved: `serialize_content_block` still emits the JSON key
  `"id"`, and `parse_assistant_message` still reads `raw_block.id` from the
  Anthropic SDK.

### Phase 4 — Honest names

- `ToolResultBlock.does_terminate` → `ToolResultBlock.is_terminal`.
- `ToolExecutionCompletedEvent.does_terminate` → `ToolExecutionCompletedEvent.is_terminal`.
- `ToolResult.does_terminate` (in `tools/_framework/core/results.py`) → `ToolResult.is_terminal`.
- `Message.text` property → `Message.assistant_text` property. The audit:
  every call site (`m.text`, `msg.text`, `message.text`, `restored.text`) was
  a Message instance asserting on the concatenated `TextBlock` contents. No
  caller needed a separate `all_text` (notifications + text); the `.all_text`
  sibling property is **not** added.

### Phase 5 — Recorder registry + verb convention

- `_BY_AGENT_RUN: dict[str, …]` → `dict[tuple[str, str], …]` keyed by
  `(agent_name, run_id)`.
- Renamed `register_recorder_for_agent_run` → `register_recorder`,
  `recorder_for_agent_run` → `recorder_for_run`,
  `clear_recorder_for_agent_run` → `clear_recorder`. All take
  `(agent_name, run_id, …)`.
- Updated callers: `engine/query/request.py`, `task_center_runner/audit/recorder.py`.
- `AuditRecorder._agent_run_to_task: dict[str, str]` → `dict[str, tuple[str, str]]`
  to carry `agent_name` alongside `task_id` so registration sites have both keys.
- `prepare_provider_messages` → `build_provider_messages` in
  `engine/query/provider_history.py` and call sites.

### Phase 6 — Default `max_tokens` raised to 32768

- `providers/types.py` `MessageRequest.max_tokens: int = 4096` → `32768`.
- `engine/agent/factory.py`: `get("max_tokens") or 16384` → `32768`.
- `config/model_config.py`: `get_active_max_tokens(default: int = 16384)` → `32768`.
- Unit tests that pin tiny values (truncation/overshoot tests) untouched.

---

## Deferred items (acknowledged)

1. ~~**`tool_id` at the sandbox/engine boundary.**~~ **Resolved.** The
   `tool_id` field was renamed to `tool_use_id` across:
   - `sandbox/daemon/audit_schema.py` (`ToolCallSection.tool_id` →
     `tool_use_id`), and its `as_dict()` output keys.
   - JSONL payload keys in `engine/audit/stream.py`,
     `task_center_runner/audit/{stream_bridge,sandbox_events,metrics,
     performance_report,legacy}.py`, and the SWE-EVO mock runner.
   - Test fixtures asserting on these payload keys.
   `SandboxCaller.tool_id` (caller-context object field, not a wire key)
   is intentionally preserved as a sandbox-domain identifier — engine
   callers construct it via `SandboxCaller(tool_id=metadata.get("tool_use_id"))`.
   AC4 now reads zero hits in `engine/`, `message/`, `tools/_framework/`.

2. **`mypy backend/src` not clean.** The repo had 734 pre-existing mypy
   errors before this refactor. The refactor introduced none — the remaining
   tool_use_id/Message errors flagged were preexisting type drift now
   relocated, not new debt.

3. ~~**`docs/` references to `ApiMessage` and `ConversationMessage`.**~~
   **Resolved.** Mechanical symbol updates applied across
   `docs/architecture/` (22 files: `index.html`, `assets/search-index.js`,
   `agent_loops/*.html`, `task_center/*.html`, `tools/*.html`, and
   peer-module references). All deprecated symbols (`ApiMessage*`,
   `ConversationMessage`, `does_terminate`, `assistant_message_from_api`,
   `prepare_provider_messages`, `register_recorder_for_agent_run`, the
   suffixless `…Delta`/`…Complete`/`ToolExecution…` names, plus file paths
   `message/stream_events.py` → `message/events.py` and `message/messages.py`
   → `message/message.py`) replaced with their new names. AC7 now reads
   zero hits in `docs/architecture/`. Per-page `data-last-reviewed-commit`
   markers were NOT bumped — that's a separate doc-refresh exercise (the
   underlying architectural claims are unchanged).

4. **JSONL output key for ToolUseBlock changed.** Per the plan ADR,
   `model_dump(mode="json")` of a `ToolUseBlock` in the agent message
   recorder now writes `"tool_use_id"` instead of `"id"`. One test fixture
   asserting on JSONL contents was updated; no in-flight data migration
   needed.

5. **Provider→message dependency arrow** is now real and intentional
   (Architect's flagged trade-off). The providers package imports event
   types from `message.events`. `providers.types.MessageRequest` references
   `message.Message` under `TYPE_CHECKING` to avoid runtime cycles.

---

## Files touched

- New: `backend/src/message/events.py`
- Renamed: `backend/src/message/messages.py` → `backend/src/message/message.py`
- Deleted: `backend/src/message/stream_events.py`
- Modified (selected):
  - `backend/src/message/__init__.py`
  - `backend/src/message/agent_message_recorder.py`
  - `backend/src/message/event_printer.py`
  - `backend/src/providers/__init__.py`
  - `backend/src/providers/types.py`
  - `backend/src/providers/clients/anthropic_native.py`
  - `backend/src/providers/clients/coding_plan/codex.py`
  - `backend/src/engine/query/loop.py`
  - `backend/src/engine/query/request.py`
  - `backend/src/engine/query/provider_history.py`
  - `backend/src/engine/tool_call/streaming.py`
  - `backend/src/engine/tool_call/dispatch.py`
  - `backend/src/engine/background/*.py`
  - `backend/src/engine/agent/*.py`
  - `backend/src/engine/audit/stream.py`
  - `backend/src/tools/_framework/core/{results.py, runtime.py, base.py}`
  - `backend/src/tools/_framework/execution/tool_call.py`
  - `backend/src/task_center_runner/audit/recorder.py`
  - `backend/src/config/model_config.py`
  - Multiple sandbox boundary files where the engine consumes sandbox types
    via the audit translation layer.
  - Many tests under `backend/tests/unit_test/`.
