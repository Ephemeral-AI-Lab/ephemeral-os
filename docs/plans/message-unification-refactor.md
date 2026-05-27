# Message System Unification Refactor

**Status:** Approved (RALPLAN consensus — Planner → Architect → Critic APPROVE)
**Date:** 2026-05-27
**Scope:** `backend/src/{message,providers,engine/query}/` + all callers
**Net LOC target:** −80 to −140

---

## ADR

**Decision.** Unify message naming across the codebase: drop the `Api` prefix entirely, collapse the dual provider/engine event taxonomy into one (Option A), rename `ConversationMessage` → `Message`, and apply the prior naming-review fixes in one disciplined rename pass. Also raise the default `max_tokens` to 32768.

**Drivers.**
1. User mandate: one name per concept, no api/display/visible distinction.
2. Maintainability over cleverness.
3. LOC discipline — never 200 LOC where 100 will do. Pure renames stay 0-net; structural change only where it earns its keep.

**Alternatives considered.**
- *Rename-only (drop Api prefix, nothing else):* Rejected — leaves known naming bugs the user explicitly told us to fix.
- *Preserve provider/engine event seam (Provider* prefix on wire-side events):* Rejected by the user — the duplicated taxonomy doesn't earn its keep; one set of events is cleaner. Architect's identity-stamping concern accepted as a known trade-off (providers emit empty `agent_name`/`run_id`; `_stamp` in the loop fills them in).
- *Full restructure (package reshuffle, inline `MessageRequest`):* Rejected — over-scoped, violates LOC budget, widens protocol signature for marginal LOC.

**Why chosen.** Does exactly what the user asked, fixes a known backlog of naming smells in one atomic pass, mechanically safe (type checker is the proof). Option A collapse buys back the most LOC by deleting the 1:1 translation switch in `_consume_provider_stream`.

**Consequences.**
- Every import of `providers.types` and `message.stream_events` changes — mechanical.
- Provider clients (`anthropic_native`, `codex`) now import event types from `message/events.py`. Dependency arrow: `providers → message`. Accepted.
- JSONL keys change (`tool_id` → `tool_use_id`, `does_terminate` → `is_terminal`). One-time fixture update; no in-flight data.
- `ConversationMessage.text` semantics are not silently changed — the property is renamed to `.assistant_text` so the filter becomes honest.
- Default `max_tokens` rises from 4096 to 32768 — long tool-result loops stop getting truncated.

**Follow-ups.** None gating. Optional later: per-model `max_tokens` if Sonnet's 64K becomes load-bearing.

---

## Principles

1. **One name per concept.** A `Message` is a `Message` whether it's in the transcript, on the wire, or in a log.
2. **Names should not lie.** A property called `text` must return text; a flag called `does_terminate` must read as a state, not a question.
3. **Delete abstraction before renaming it** when the collapse is architecturally safe.
4. **Minimum diff, mechanical migration.** Pure renames must be one-shot `sed`-able with type-check verification — no behavioral drift slipped in under cover.
5. **Public surface shrinks, not grows.** Net change should reduce `__all__` size and module count.

---

## Phases

### Phase 1 — Collapse provider event taxonomy, drop Api prefix, settle `…Event` suffix

**Delete from `providers/types.py`:**
- `ApiTextDeltaEvent`
- `ApiThinkingDeltaEvent`
- `ApiToolUseDeltaEvent`
- `ApiMessageCompleteEvent`
- `ApiStreamEvent` (union)

**Rename in `providers/types.py`:**
- `ApiMessageRequest` → `MessageRequest` (kept as `@dataclass(frozen=True)`)
- `SupportsStreamingMessages.stream_message` signature now returns `AsyncIterator[StreamEvent]` (engine taxonomy).

**Rename in `message/stream_events.py` (renamed to `message/events.py`):**
- `AssistantTextDelta` → `AssistantTextDeltaEvent`
- `ThinkingDelta` → `ThinkingDeltaEvent`
- `AssistantMessageComplete` → `AssistantMessageCompleteEvent`
- `ToolExecutionStarted` → `ToolExecutionStartedEvent`
- `ToolExecutionCompleted` → `ToolExecutionCompletedEvent`
- `ToolExecutionProgress` → `ToolExecutionProgressEvent`
- `ToolExecutionCancelled` → `ToolExecutionCancelledEvent`
- `BackgroundTaskStarted` → `BackgroundTaskStartedEvent`
- Union `StreamEvent` keeps its name.

**Rename in `message/messages.py`:**
- `assistant_message_from_api` → `parse_assistant_message`

**Update `engine/query/loop.py`:** delete the 1:1 translation switch in `_consume_provider_stream` (loop.py:158-178). Provider clients yield engine events directly with empty `agent_name`/`run_id`; `_stamp` fills identity post-hoc.

**Update provider clients:**
- `providers/clients/anthropic_native.py`: import and yield `AssistantTextDeltaEvent`, `ThinkingDeltaEvent`, `ToolUseDeltaEvent` (new — split from `AssistantMessageCompleteEvent` tool blocks? — see note), `AssistantMessageCompleteEvent`.
- `providers/clients/coding_plan/codex.py`: same.

**Note on `ApiToolUseDeltaEvent`:** Currently this provider-side event triggers early tool dispatch. After collapse it becomes `ToolUseDeltaEvent` (new engine event) — keep it as a distinct event because it's not a `ToolExecutionStartedEvent` (no execution yet, just stream arrival). Add to `message/events.py`.

**LOC impact:** ~−60 (translation switch deleted, dataclass dedup).

### Phase 2 — `ConversationMessage` → `Message`

Pure class rename. File `backend/src/message/messages.py` → `backend/src/message/message.py`. Update `message/__init__.py` `__all__`.

LOC: 0 net.

### Phase 3 — Unify tool-use identifier

| Old | New |
|---|---|
| `ToolUseBlock.id` | `ToolUseBlock.tool_use_id` |
| `ToolExecutionStartedEvent.tool_id` | `tool_use_id` |
| `ToolExecutionCompletedEvent.tool_id` | `tool_use_id` |
| `ToolExecutionProgressEvent.tool_id` | `tool_use_id` |
| `ToolExecutionCancelledEvent.tool_id` | `tool_use_id` |
| `ToolUseDeltaEvent.id` | `tool_use_id` |

**Scope guard:** Limit the rewrite to `backend/src/{engine,message,tools/_framework}`. Sandbox/daemon code may use `tool_id` as a sandbox-domain concept — confirm via `rg '\btool_id\b' backend/src/sandbox` before any wider sweep. Out of scope for this refactor.

LOC: ~0 net.

### Phase 4 — Honest names

- `ToolResultBlock.does_terminate` → `is_terminal`
- `ToolExecutionCompletedEvent.does_terminate` → `is_terminal`
- `Message.text` property → `Message.assistant_text` (currently filters out `ThinkingBlock` + `SystemNotificationBlock`).

**Audit gate:** Before commit, run `rg '\.text\b' backend/src backend/tests | rg -i 'conversationmessage|message\.text|msg\.text'` — list all call sites. If any caller actually wanted *all* visible text (including notifications), add a sibling `.all_text` property in the same commit. Land the audit result in the commit message.

LOC: ~0 net (possibly +5 if `.all_text` is added).

### Phase 5 — Recorder key + verb convention

- `_BY_AGENT_RUN: dict[str, AgentMessageJsonlRecorder]` → `dict[tuple[str, str], AgentMessageJsonlRecorder]` keyed by `(agent_name, run_id)`.
- `register_recorder_for_agent_run(agent_run_id, recorder)` → `register_recorder(agent_name, run_id, recorder)`.
- Same shape change for `recorder_for_agent_run` and `clear_recorder_for_agent_run`.
- `prepare_provider_messages` → `build_provider_messages` (adopt `build_*` for pure constructors; `_make_*` reserved for factories returning callables).

**Pre-commit check:** `rg '_BY_AGENT_RUN|agent_run_id' backend/` — verify no caller serializes/splits the old joined-string key shape.

LOC: ~0 net.

### Phase 6 — Raise default `max_tokens` to 32768

| File | Change |
|---|---|
| `backend/src/providers/types.py:44` | `MessageRequest.max_tokens: int = 4096` → `32768` |
| `backend/src/engine/agent/factory.py:371` | `get("max_tokens") or 16384` → `or 32768` |
| `backend/src/config/model_config.py:79` | `get_active_max_tokens(default: int = 16384)` → `32768` |

**Rationale:** Claude Opus 4.x supports 32K standard output; Sonnet 4.x supports 64K. 32K is the safe ceiling that works across the model family without per-model branching and stays inside every model's context window when paired with reasonable inputs. Unit tests that pin tiny values (`max_tokens=100`, `=1`, `=32`) are intentional truncation/overshoot tests — leave them alone.

LOC: ~0 net (one-line per file).

---

## Verification

```bash
.venv/bin/ruff check backend/src backend/tests
.venv/bin/mypy backend/src
.venv/bin/pytest backend/tests/unit_test -x
.venv/bin/pytest backend/tests/unit_test/test_providers backend/tests/unit_test/test_engine \
                 backend/tests/unit_test/test_message backend/tests/unit_test/test_tools -x
```

## Acceptance Criteria (falsifiable)

1. `rg -i 'api[_]?message|apitext|apithinking|apitooluse|apistream' backend/src` → zero non-comment hits.
2. `rg 'ConversationMessage' backend/` → zero hits.
3. `rg '\.does_terminate' backend/` → zero hits.
4. `rg '\btool_id\b' backend/src/{engine,message,tools/_framework}` → zero hits.
5. `rg '\bAssistantTextDelta\b' backend/` → zero hits (verifies the `…Event` suffix was applied).
6. `rg 'AssistantMessageComplete\b' backend/` → zero hits.
7. `rg 'ApiMessage' docs/` → zero hits (or document remaining hits in commit message).
8. `rg 'max_tokens.*4096|max_tokens.*16384' backend/src` → zero hits in non-test code.
9. `mypy backend/src` clean.
10. `.venv/bin/pytest backend/tests/unit_test -x` green.
11. Net diff: `−80` to `−140` LOC across `backend/src/{message,providers,engine/query}/` (±20 tolerance).
12. `backend/src/message/stream_events.py` renamed to `events.py`; `backend/src/message/messages.py` renamed to `message.py`.

## Commit Plan (atomic, each green on ruff + mypy + unit)

1. `refactor(message): collapse provider event taxonomy, drop Api prefix, settle Event suffix` *(Phase 1; ~−60 LOC)*
2. `refactor(message): rename ConversationMessage → Message, messages.py → message.py` *(Phase 2; 0 LOC)*
3. `refactor(message): unify tool_use_id across blocks and engine events` *(Phase 3; 0 LOC)*
4. `refactor(message): does_terminate → is_terminal; .text → .assistant_text` *(Phase 4; 0 LOC)*
5. `refactor(message): recorder registry keyed by (agent_name, run_id); build_provider_messages` *(Phase 5; 0 LOC)*
6. `refactor(message): raise default max_tokens to 32768` *(Phase 6; 0 LOC)*

## Out of Scope (explicit)

- Package reshuffling (`message/`, `prompt/`, `engine/query/` layout).
- Stream event payload changes (renames only — no field changes beyond `tool_use_id` and `is_terminal`).
- `MessageRequest` API additions or protocol-signature changes.
- Sandbox/daemon `tool_id` renames (separate scope; sandbox-domain concept).
- Architecture doc rewrites beyond mechanical symbol updates.
- Per-model `max_tokens` (single global default of 32768).

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Provider clients now import from `message/` — dependency arrow inverted | Accepted trade-off per Option A; clients only import event dataclasses, no engine logic |
| `_stamp` identity contract leaks if provider yields events with non-empty `agent_name`/`run_id` | Providers MUST emit empty strings; assert at the top of `_stamp` if needed |
| Sandbox `tool_id` collateral damage | Scope guard `backend/src/{engine,message,tools/_framework}` + pre-rename `rg` confirm in sandbox |
| `.text` rename hides a legitimate caller | Pre-commit 8-site audit; add `.all_text` sibling property if any caller needs it |
| Recorder key change breaks an external serializer | Pre-commit `rg _BY_AGENT_RUN` + `rg agent_run_id` to confirm no joined-string consumer |
| Parallel agent merge conflicts | Atomic commits per phase; each phase is a `sed`-able pure rename so rebase is mechanical |
| `max_tokens=32768` blows context window with very large inputs | 32K out of 200K window leaves 168K for input — fine for all current agents; revisit if a high-input agent regresses |

---

## Execution Notes for the Implementer

- Work commit-by-commit. Each commit must pass `ruff` + `mypy` + unit tests independently.
- Use `rg` for the rename pass; `sed` only for trivial single-token swaps.
- The Phase 1 collapse is the riskiest commit — review the `_stamp` call site (`engine/query/loop.py:386-401`) carefully to confirm identity-stamping still fires for provider-emitted events.
- The audit in Phase 4 may reveal that one or two callers want all visible text. If so, add `.all_text` as a sibling property in the same commit.
- Do NOT touch test fixtures wholesale — only update fixtures whose assertions reference renamed symbols.
- Do NOT add backwards-compatibility shims, aliases, or deprecation warnings. This is a clean break.
