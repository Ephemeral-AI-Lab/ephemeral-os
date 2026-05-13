---
created: 2026-05-13
scope: agents/, providers/, message/, prompt/, db/ reviews
status: open
---

# Deferred Cleanup Items

Items surfaced by the parallel reviews of `providers/`, `message/+prompt/`,
`agents/`, and `db/` that were **not** applied in the BLOCKER + dead-code
pass. Sources:

- `.planning/code-reviews/agents-REVIEW.md`
- `.planning/code-reviews/providers-REVIEW.md`
- `.planning/code-reviews/message-prompt-REVIEW.md`
- `.planning/code-reviews/db-REVIEW.md`

Already-shipped commits this pass (for reference): `ea1f102c`, `cfc1c6cd`,
`e667490b`, `ed04c59d`, `0e775f12`.

---

## A. Cross-package refactors (touch packages outside the reviewed four)

### A-1. db WR-05 — `ModelStore` readiness contract drift

- **Source:** `db-REVIEW.md` WR-05
- **Problem:** `ModelStore` reimplements `SyncStoreMixin` and exposes
  `is_available` instead of `is_ready`. Every other store inherits
  `SyncStoreMixin` and exposes `is_ready`.
- **External consumers** (after the providers cleanup):
  - `backend/src/config/model_config.py`
  - `backend/src/runtime/app_factory.py`
- **Fix sketch:** Make `ModelStore` inherit from `SyncStoreMixin`; delete the
  duplicate `__init__` / `initialize` / `_sf` / `is_available` body; either
  migrate the two callers to `is_ready` or alias `is_available = is_ready`
  on the inherited class.
- **Risk:** Small — two call sites, both same project.

### A-2. db WR-06 — Six dead ORM columns on Mission / Episode / Attempt

- **Source:** `db-REVIEW.md` WR-06 (and the related WR-09 / dead-DTO list)
- **Problem:** `context` and `summary` columns are defined on each of
  `MissionRecord`, `EpisodeRecord`, `AttemptRecord`. No store method ever
  writes them. The audit recorder reads them and gets `None` every time.
  DTO fields mirror the columns and are dropped silently by the
  `_to_dto` mappers.
- **Touch surface:**
  - `backend/src/db/models/{mission,episode,attempt}.py` — remove columns
  - `backend/src/task_center/mission/mission.py` — drop DTO fields
  - `backend/src/task_center/episode/episode.py` — drop DTO fields
  - `backend/src/task_center/attempt/state.py` — drop DTO fields
  - `backend/src/live_e2e/audit/recorder.py` — drop the always-None reads
  - `backend/src/db/engine.py` — append column names to `_DROPPED_COLUMNS`
    so dev DBs get patched
- **Risk:** Medium — the parallel session has been actively editing
  `task_center/attempt/*`. Wait for them to quiesce, or stage hunks with
  `git commit --only --` and verify diff size before commit.

### A-3. db WR-10 — `task_center_tasks.system_prompt` / `user_prompt`

- **Source:** `db-REVIEW.md` WR-10
- **Problem:** Same anti-pattern as WR-06 but on `TaskCenterTaskRecord`.
  Both columns are defined; nothing writes them; the audit recorder reads
  them and gets `None`.
- **Touch surface:**
  - `backend/src/db/models/task_center.py` — remove the two columns
  - `backend/src/live_e2e/audit/recorder.py` — drop reads
  - `backend/src/db/engine.py` — append to `_DROPPED_COLUMNS["task_center_tasks"]`
- **Risk:** Lower than WR-06 — does not touch task_center DTO files.

### A-4. message WR-04 — `message` depends on `prompt` for a generic helper

- **Source:** `message-prompt-REVIEW.md` WR-04
- **Problem:** `message/agent_message_recorder.py:23` imports
  `append_prompt_report_event` from `prompt/message_recorder.py`. The
  helper is a generic JSONL appender with nothing prompt-specific; the
  dependency direction (message ← prompt) inverts the natural domain
  layering (prompts are built from messages).
- **Fix sketch:** Move `append_prompt_report_event` and its `_json_default`
  helper to a neutral module (e.g., `common/jsonl.py` or
  `persist/append_jsonl.py`). Rename the function to `append_jsonl_event`.
  Update both call sites (`message/agent_message_recorder.py:23`,
  `prompt/prompt_report_recorder.py:10`).
- **Risk:** Low — surface is two import sites. New module is the work.

---

## B. Smaller behavior fixes (file-disjoint with current parallel work)

### B-1. db WR-07 — Dialect-blind pool config

- **Source:** `db-REVIEW.md` WR-07
- **Problem:** `create_engine(url, pool_size=5, max_overflow=10, ...)` is
  passed unconditionally. SQLite's default pool ignores these and
  SQLAlchemy emits noise. With `pool_pre_ping=True` on SQLite the
  pre-ping is a meaningless `SELECT 1`.
- **Touch surface:** `backend/src/db/engine.py` (the `create_engine` call
  inside `initialize_db`).
- **Fix sketch:**
  ```python
  from sqlalchemy.engine import make_url
  is_sqlite = make_url(url).drivername.startswith("sqlite")
  engine_kwargs: dict[str, Any] = {"echo": echo}
  if not is_sqlite:
      engine_kwargs["pool_pre_ping"] = pool_pre_ping
      engine_kwargs["pool_size"] = pool_size
      engine_kwargs["max_overflow"] = max_overflow
  _engine = create_engine(url, **engine_kwargs)
  ```

### B-2. db WR-11 — `ModelStore.delete` non-deterministic auto-promote

- **Source:** `db-REVIEW.md` WR-11
- **Problem:** When deleting an active model, `ModelStore` promotes the
  next row chosen by `query().first()` with no `order_by`. Insertion-order
  on SQLite, arbitrary on Postgres.
- **Touch surface:** `backend/src/db/stores/model_store.py:161-175`
- **Fix sketch:** add `.order_by(ModelRegistrationRecord.created_at)`
  before `.first()`, or remove the auto-promote and require an explicit
  `select_active` call.

### B-3. message WR-02 — `AssistantMessageComplete` missing run_id / agent_name

- **Source:** `message-prompt-REVIEW.md` WR-02
- **Problem:** The live engine constructs `AssistantMessageComplete`
  without passing `agent_name` / `run_id`. Both default to `""`. When the
  event reaches `MultiAgentEventPrinter._flush_buffers`, the empty
  `run_id` falls through to the "flush every lane for this agent" branch.
  For single-agent runs this happens to work; with subagent
  multiplexing it silently flushes interleaved lanes.
- **Touch surface:** `backend/src/engine/query/loop.py:327` (around the
  `yield AssistantMessageComplete(...)` site).
- **Fix sketch:** plumb `agent_name=context.agent_name` and
  `run_id=context.run_id` into the constructor — matches what
  `live_e2e/squad/runner.py:1075-1090` already does.

### B-4. message WR-05 — `assistant_message_from_api` silently drops unknown blocks

- **Source:** `message-prompt-REVIEW.md` WR-05
- **Problem:** Anthropic adds new content-block types over time
  (e.g. `server_tool_use`, `mcp_tool_use`, extended-thinking variants).
  `assistant_message_from_api` only handles `thinking`, `text`, `tool_use`
  and silently drops anything else. No log, no metric.
- **Touch surface:** `backend/src/message/messages.py:174-193`
- **Fix sketch:** add a `logger.debug("...: dropping unrecognized block
  type %r", block_type)` on the else branch.

### B-5. prompt IN-05 — Function-local `import time` in hot path

- **Source:** `message-prompt-REVIEW.md` IN-05
- **Problem:** `import time as _time` is repeated inside
  `MultiAgentEventPrinter.__init__` and `_line`. The `_line` import fires
  on every printed line.
- **Touch surface:** `backend/src/message/event_printer.py:185, 343-344`
- **Fix sketch:** Hoist `import time` to the top of the module; drop the
  function-local copies.

---

## C. Style / Info items (no defects, low priority)

### C-1. db IN-01 — Mixed JSON imports

- **Source:** `db-REVIEW.md` IN-01
- **Problem:** Five of six JSON-using models import
  `from sqlalchemy.dialects.postgresql import JSON`; the sixth uses
  generic `from sqlalchemy import JSON`. Works either way; inconsistency
  signals cargo-culted dialect import.
- **Fix sketch:** Standardise on `from sqlalchemy import JSON` across all
  six models.

### C-2. db IN-02 — `ContextPacketStore.insert` returns input id

- **Source:** `db-REVIEW.md` IN-02
- **Problem:** Returns `packet.id` (input arg) rather than `record.id`.
  Defensive only — the model has no server-side id default today.
- **Fix sketch:** `db.refresh(record); return record.id`.

### C-3. db IN-03 — `_to_dict` model_id lookup chain

- **Source:** `db-REVIEW.md` IN-03
- **Problem:** `kwargs.get("id") or kwargs.get("model") or
  kwargs.get("model_id")` chain is asymmetric and silent-failure-prone
  if the value is `0`.
- **Fix sketch:** Document supported keys or assert exactly one is present.

### C-4. db IN-04 — Duplicated `cancel_for_compensation`

- **Source:** `db-REVIEW.md` IN-04
- **Problem:** `MissionStore.cancel_for_compensation` and
  `EpisodeStore.cancel_for_compensation` have identical bodies.
- **Fix sketch:** Optional — extract a generic helper or inline at the
  single call sites in `task_center/mission/`.

### C-5. prompt IN-03 — `build_system_prompt` is a 1-line wrapper

- **Source:** `message-prompt-REVIEW.md` IN-03
- **Problem:** `(s or "").strip()` wrapped in a function with two
  callers. Name implies "build" but it's a normalizer.
- **Fix sketch:** Either rename/redoc, or inline `.strip()` at the two
  call sites.

---

## D. Deliberately kept (decided, not deferred)

These were considered and rejected as cleanup targets:

- **providers LE-04** — `AuthenticationFailure` / `RateLimitFailure` /
  `RequestFailure` error subclasses. Tests assert on them, classification
  is useful telemetry. Kept.
- **agents WR-01** — `_coerce_positive_int` / `_coerce_bool` silent-coerce.
  Behavior preserved so existing agent profiles with stringly-typed
  values don't suddenly break.
- **providers IN-01** — `_translate_error` double-wrap defense. The
  existing `except EphemeralOSApiError: raise` guard already prevents the
  bug; adding `if isinstance(...)` is belt-and-braces.
- **providers IN-02** — `kwargs.get("api_key") or ""` idiom. Today
  immediately followed by `if not api_key: raise`, so behaviorally
  identical to the suggested form.
- **agents IN-04** — Inline forward-ref comment. Already added in
  commit `cfc1c6cd`.

---

## E. Subsumed / already resolved by the cleanup pass

- **db WR-09** — `_to_dto` mappers drop `context`/`summary`. Subsumed by
  A-2 (drop the columns) — if columns go, the DTO mapping question
  disappears.
- **message WR-01** — Documented printer features (`_depth`,
  `_run_to_agent`, `subagents_spawned`, lineage indent, `summary`)
  unimplemented. Resolved by deletion in commit `0e775f12`: dead state
  removed, docstring rewritten to describe what the code actually does.
- **agents IN-03** — `@runtime_checkable` on `AgentNotificationRule`.
  Cannot be removed; Pydantic uses isinstance for the list field
  validator. Documented in commit `cfc1c6cd`.

---

## Suggested execution order

1. **A-1, B-1, B-2** — file-disjoint, low-risk, small. Single commit each.
2. **B-3, B-4, B-5** — message + engine. Verify the parallel session is
   not touching `engine/query/loop.py` or `message/event_printer.py`
   before staging.
3. **A-3** — `task_center.system_prompt`/`user_prompt`. Touch surface
   stays inside db + live_e2e, no task_center DTO files.
4. **A-2** — Wait for the parallel session's task_center wave to commit
   before starting. Touches three task_center DTO files.
5. **A-4** — Larger refactor; do last or skip.
6. **C-1..C-5** — Cosmetic batch, optional.

Each step is independently testable. After each, run
`.venv/bin/pytest backend/tests/unit_test/test_<package>/` and confirm
green.
