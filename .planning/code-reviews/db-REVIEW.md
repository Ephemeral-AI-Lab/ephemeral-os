---
phase: db (ad-hoc directory review)
reviewed: 2026-05-13T22:35:00Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - backend/src/db/__init__.py
  - backend/src/db/base.py
  - backend/src/db/engine.py
  - backend/src/db/models/__init__.py
  - backend/src/db/models/agent_run.py
  - backend/src/db/models/attempt.py
  - backend/src/db/models/context_packet.py
  - backend/src/db/models/episode.py
  - backend/src/db/models/mission.py
  - backend/src/db/models/model_registration.py
  - backend/src/db/models/task_center.py
  - backend/src/db/stores/__init__.py
  - backend/src/db/stores/agent_run_store.py
  - backend/src/db/stores/attempt_store.py
  - backend/src/db/stores/base.py
  - backend/src/db/stores/context_packet_store.py
  - backend/src/db/stores/episode_store.py
  - backend/src/db/stores/mission_store.py
  - backend/src/db/stores/model_store.py
  - backend/src/db/stores/task_center_store.py
findings:
  blocker: 2
  warning: 9
  info: 6
  total: 17
status: issues_found
---

# Phase db: Code Review Report

**Reviewed:** 2026-05-13T22:35:00Z
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

The `db/` directory is a focused SQLAlchemy persistence layer with one declarative base, seven ORM models, and seven store wrappers. Bugs are not the headline — correctness of stored data is fine. The headline is significant **dead/orphan surface** left over from incomplete migrations:

1. Six ORM columns (`context`, `summary` on each of Mission / Episode / Attempt) and two more (`system_prompt`, `user_prompt` on TaskCenterTaskRecord) are defined, allocated on the DTOs, exported via `_to_dto` (Mission/Episode/Attempt only via DTO defaults of `None` — see WR-04), and read by the audit recorder, but **never written by any store method**. They are always `None` end-to-end.
2. Migration metadata for the long-dropped `task_center_attempt` table is **redundantly distributed across three constants** (`_DROPPED_COLUMNS`, `_RENAMED_COLUMNS`, `_LEGACY_TABLES_TO_DROP`) where `_DROPPED_COLUMNS` is unreachable and `_RENAMED_COLUMNS` performs a column rename on a table about to be dropped on the same call.
3. The `AsyncStoreMixin` class, `get_async_engine()` accessor, and `get_async_session_factory()` accessor are exported infrastructure with **zero call sites in `backend/src/`** (only the engine internals and one test reset them). The entire async path is effectively dead.
4. `ModelStore` reimplements `SyncStoreMixin` rather than inheriting it, exposing `is_available` instead of `is_ready`. The two `is_available` / `is_ready` patterns are now inconsistently spread across the runtime.

There are no security issues, no obvious data-loss bugs, and no injection vectors (one DDL builds table names from `_LEGACY_TABLES_TO_DROP` — a hard-coded set of literals — so the use of f-string `text()` is safe). Findings cluster around migration debt and dead surface; cleanup is the priority.

## Critical Issues (BLOCKER)

### CR-01: `_DROPPED_COLUMNS["task_center_attempt"]` is unreachable code

**File:** `backend/src/db/engine.py:79-83`
**Issue:** `_add_missing_columns` (lines 213–248) iterates over `Base.metadata.sorted_tables` — which only contains tables backed by an ORM model. The `task_center_attempt` table has no ORM model (deleted; its replacement is the new `attempts` table backed by `AttemptRecord`), so this iteration never includes it. The `_DROPPED_COLUMNS["task_center_attempt"]` entry can therefore never be read. Additionally, this same table is queued for unconditional drop in `_LEGACY_TABLES_TO_DROP` two stanzas later (line 95-97). Carrying the `_DROPPED_COLUMNS` entry for a table that has no model and will be dropped anyway is dead code that signals migration debt to future readers and risks being copied as a pattern.
**Fix:**
```python
# Delete the entire block at engine.py:79-83
_DROPPED_COLUMNS: dict[str, set[str]] = {
    "agent_runs": {
        "compacted_history", "event_count", "input_query", "metadata",
        "reasoning", "response", "session_id", "started_at", "status",
    },
    "task_center_tasks": {
        "acceptance_criteria", "children", "closes_for", "evaluator_id",
        "handoff_note", "parent_id", "run_id", "spec", "summary", "title",
    },
    "task_center_runs": {
        "root_task_id",
    },
    # task_center_attempt entry removed — table is dropped by _drop_legacy_tables
}
```

### CR-02: `_RENAMED_COLUMNS["task_center_attempt"]` renames a column on a table that is about to be dropped

**File:** `backend/src/db/engine.py:85-92` together with `:95-97`, `:321`, `:327`
**Issue:** The boot sequence at `initialize_db` is:
```
_rename_columns(_engine)        # line 321 — renames task_center_attempt.run_id -> task_center_run_id
_add_missing_columns(_engine)   # line 324
_drop_legacy_tables(_engine)    # line 327 — DROP TABLE task_center_attempt
```
Renaming a column on a legacy table immediately before unconditionally dropping the table is wasted work that obscures intent. If a future contributor adds reads from `task_center_attempt.task_center_run_id` (the renamed column) between rename and drop, the table is gone moments later. This is a footgun, not a correctness bug today — but the configuration is contradictory.
**Fix:** Drop the `task_center_attempt` entry from `_RENAMED_COLUMNS`:
```python
_RENAMED_COLUMNS: dict[str, dict[str, str]] = {
    "task_center_tasks": {
        "run_id": "task_center_run_id",
    },
    # task_center_attempt rename removed — table is dropped by _drop_legacy_tables
}
```
If there is a downstream reason this rename must run (e.g. some other tool reads the renamed table before `_drop_legacy_tables` fires), that reason should be documented inline; otherwise this is dead migration code.

## Warnings (WARNING)

### WR-01: `AsyncStoreMixin` has zero subclasses — dead class

**File:** `backend/src/db/stores/base.py:51-73`
**Issue:** Grep shows `AsyncStoreMixin` referenced only in its own definition and the module docstring. No store inherits from it. The class duplicates `__init__`, `initialize`, `initialized`, and `_sf` with `AsyncSession` types but is never used.
**Fix:** Delete the class. If the async machinery is intended to return, leave a comment noting the design but don't keep a 23-line dead class in the codebase. Per `.claude/CLAUDE.md` simplicity rule: "No features beyond what was asked."

### WR-02: `get_async_engine()` and async session-factory accessor have no production callers

**File:** `backend/src/db/engine.py:42-49` (accessors); `:28-29,:349-360` (state they expose)
**Issue:** `get_async_engine()` has zero call sites in `backend/src/`. `get_async_session_factory()` is referenced only by tests that reset module state. Yet `initialize_db` spends 12 lines constructing an async engine and async session factory (lines 334–361). The docstring says "Create async engine from the same URL for DispatcherStore" — but there is no DispatcherStore in this codebase any more (it was part of the deleted `_runtime`/`_rule_engine` infrastructure visible in the git status). The async engine is created, consumes a connection from the database pool (with `pool_size=5` etc., per WR-05), and is never used.
**Fix:** Remove the async-engine bootstrap from `initialize_db` (lines 334–361), the module-level `_async_engine` / `_async_session_factory`, both accessors, and the conditional import of `sqlalchemy.ext.asyncio`. If async is genuinely on the roadmap, gate the construction behind an explicit `db_settings.enable_async` flag.

### WR-03: `ModelStore` does not inherit from `SyncStoreMixin` — duplicates infrastructure, exposes inconsistent readiness API

**File:** `backend/src/db/stores/model_store.py:82-100`
**Issue:** `ModelStore.__init__`, `initialize`, `_sf` and the readiness property are copy-pasted from `SyncStoreMixin`. The only behavioral difference is that the property is called `is_available` instead of `is_ready`. The codebase consequently has **two readiness contracts**:
- `is_ready` — used by `task_center_store`, `agent_run_store`, `mission_store`, `episode_store`, `attempt_store` (all of `SyncStoreMixin`)
- `is_available` — used by `model_store` only

Callers in `backend/src/config/model_config.py`, `backend/src/providers/api/router.py`, `backend/src/providers/provider.py`, and `backend/src/runtime/app_factory.py` are forced to special-case `is_available` for `model_store` while using `is_ready` for everything else.

**Fix:**
```python
# model_store.py
from db.stores.base import SyncStoreMixin

class ModelStore(SyncStoreMixin):
    """CRUD operations for model registrations."""

    # Delete __init__, initialize, is_available, _sf
    # Existing call sites that read is_available need to be migrated to is_ready;
    # this is in scope for THIS cleanup, not a follow-up, because the duplication
    # is the bug.
```
This requires touching the four caller files listed above. If that scope is unacceptable, at minimum alias `is_available = is_ready` on `SyncStoreMixin` (like the existing `is_ready = initialized` alias on `:42`) and inherit.

### WR-04: Six ORM columns are never written but appear in DTOs and audit output

**Files:**
- `backend/src/db/models/mission.py:47-48` (`context`, `summary`)
- `backend/src/db/models/episode.py:55-56` (`context`, `summary`)
- `backend/src/db/models/attempt.py:50-51` (`context`, `summary`)
- DTOs: `backend/src/task_center/mission/mission.py:32-33`, `backend/src/task_center/episode/episode.py:42-43`, `backend/src/task_center/attempt/state.py:49-50`
- Audit reads (always None): `backend/src/live_e2e/audit/recorder.py:65-66,:83-84,:108-109`

**Issue:** Grep across `backend/src` finds **zero call sites** that assign `record.context = …` or `record.summary = …` for `MissionRecord`/`EpisodeRecord`/`AttemptRecord`. The store mappers do not propagate them (see WR-08), so even if some external code set them, the DTOs would silently mask the value with their `None` defaults. The audit recorder reads them and writes `null` into the artifact rows for every mission/episode/attempt event. The columns exist in production schema (since `_add_missing_columns` adds them) and consume space.

**Fix:** Remove the columns from the ORM (`models/mission.py`, `models/episode.py`, `models/attempt.py`), remove the matching DTO fields, and update the audit recorder to stop emitting them. If they are genuinely planned future state, this is dead-by-anticipation per `.claude/CLAUDE.md` §2 and should be deferred to the change that wires the writes.

### WR-05: `pool_size` / `max_overflow` passed unconditionally — silently ignored for SQLite

**File:** `backend/src/db/engine.py:307-313, :349-355`
**Issue:** `create_engine(url, pool_size=5, max_overflow=10, …)` is dialect-blind. SQLite's default `SingletonThreadPool` ignores `pool_size`/`max_overflow` and SQLAlchemy emits a `SADeprecationWarning` ("max_overflow option being applied to a non-pool"). Worse: with `pool_pre_ping=True` on SQLite the ping is a meaningless `SELECT 1`. This is not a bug in production (postgres uses these), but it pollutes test output and gives a false sense that the dev/test SQLite DB has connection-pool semantics it does not.
**Fix:**
```python
from sqlalchemy.engine import make_url
parsed_url = make_url(url)
is_sqlite = parsed_url.drivername.startswith("sqlite")

engine_kwargs: dict[str, Any] = {"echo": echo}
if not is_sqlite:
    engine_kwargs["pool_pre_ping"] = pool_pre_ping
    engine_kwargs["pool_size"] = pool_size
    engine_kwargs["max_overflow"] = max_overflow
_engine = create_engine(url, **engine_kwargs)
```
Apply the same gate to the async-engine call site (if WR-02 is not addressed).

### WR-06: `EpisodeStore.list_for_missions` is dead — no callers

**File:** `backend/src/db/stores/episode_store.py:111-130`
**Issue:** Grep returns one hit — its own definition. No production or test code calls it. Sibling method `list_for_mission` (singular) is the live API.
**Fix:** Delete the method.

### WR-07: `AttemptStore.list_for_episodes`, `AgentRunStore.list_runs_for_tasks`, `TaskCenterStore.list_tasks_for_attempts` are dead — no callers

**Files:**
- `backend/src/db/stores/attempt_store.py:149-164`
- `backend/src/db/stores/agent_run_store.py:76-85`
- `backend/src/db/stores/task_center_store.py:227-245`

**Issue:** Each method has exactly one grep hit (its own definition). Each is the plural cross-parent bulk variant of a live singular method (`list_for_episode`, `list_runs_for_task` — though `list_runs_for_task` doesn't exist, indicating `list_runs_for_tasks` was a speculative API never wired up). The grep also confirms no test exercises these methods.
**Fix:** Delete the three methods. For `AgentRunStore.list_runs_for_tasks`, also delete the `_serialize_run_summary` helper at lines 12-22 — it is the function's only consumer, so removing the method strands the helper.

### WR-08: Store `_to_dto` mappers drop `context` / `summary` even when read from DB

**Files:**
- `backend/src/db/stores/attempt_store.py:180-201` — `_to_dto` for Attempt
- `backend/src/db/stores/episode_store.py:190-206` — `_to_dto` for Episode
- `backend/src/db/stores/mission_store.py:126-138` — `_to_dto` for Mission

**Issue:** The DTO classes have `context: str | None = None` and `summary: str | None = None` fields with defaults. The `_to_dto` mappers **omit** these from their kwargs, so even if a `MissionRecord.context` somehow got populated (e.g. by a future caller or by a manual UPDATE), reading it via `MissionStore.get(...)` would yield `Mission.context = None`. This is silent data loss between persistence and DTO layers. The audit recorder reads the column directly off the record (bypassing the DTO), so it can see the value; any store consumer cannot.

This is interlocked with WR-04: today the columns are never written, so the DTO drop never manifests. But the asymmetry is a footgun — fix the DTO mapping in the same change that decides whether to keep the columns. If keeping, propagate; if dropping (recommended), remove the DTO field too.

**Fix (if keeping columns):**
```python
# attempt_store.py _to_dto — add at end of Attempt(...) kwargs:
context=record.context,
summary=record.summary,
# Same for episode_store and mission_store.
```

### WR-09: `task_center_tasks.system_prompt` / `user_prompt` columns are never written

**Files:**
- ORM: `backend/src/db/models/task_center.py:93-94`
- Store: `backend/src/db/stores/task_center_store.py:148-197` (`upsert_task` — does not accept these parameters), `:44-60` (`_serialize_task` — does not include them)
- Audit reads (always None): `backend/src/live_e2e/audit/recorder.py:125-126`

**Issue:** Grep across `backend/src/task_center` finds zero writes to `system_prompt` / `user_prompt` on `TaskCenterTaskRecord`. `upsert_task` does not accept them as parameters. `_serialize_task` does not return them. Only `live_e2e/audit/recorder.py:_serialize_task` reads them, and gets None every time. Same anti-pattern as WR-04 but on a different table.
**Fix:** Remove the columns from `task_center.py` model and from the audit recorder serializer. If they are genuinely needed (Stage 6 fix-executor comment at `:95-96` suggests adjacent recovery-wiring intent), wire the write path in the same change.

## Info (INFO)

### IN-01: `from sqlalchemy.dialects.postgresql import JSON` is inconsistent with `db/models/context_packet.py`

**File:** `backend/src/db/models/agent_run.py:9`, `attempt.py:12`, `episode.py:12`, `mission.py:14`, `task_center.py:13` use `from sqlalchemy.dialects.postgresql import JSON`; `backend/src/db/models/context_packet.py:11` uses the generic `from sqlalchemy import JSON`.
**Issue:** Five of six JSON-using models import the postgres-dialect JSON type; the sixth uses the generic. The postgres-dialect JSON falls back to generic on SQLite (which is how tests run), so this works — but the inconsistency is jarring and signals that the dialect-specific import was likely cargo-culted (postgres JSONB would be the only reason to dialect-pin, and that's not what's being imported).
**Fix:** Standardise on `from sqlalchemy import JSON` across all six models, or document why the postgres-dialect import is needed.

### IN-02: `ContextPacketStore.insert` has no `db.refresh(record)` before returning the id

**File:** `backend/src/db/stores/context_packet_store.py:24-36`
**Issue:** Minor: `insert` returns `packet.id` (the input arg) rather than `record.id`. If the model ever grows a server-side default or trigger-mutated id, the function silently returns the wrong value. Today the id is set client-side and the model has no server default for it, so this is purely defensive.
**Fix:**
```python
return record.id  # was: return packet.id
```
(or accept the closure on `packet.id` as documented invariant — but then the docstring should call it out.)

### IN-03: `ModelStore.delete` "promote first" logic is fragile

**File:** `backend/src/db/stores/model_store.py:161-175`
**Issue:** When deleting an active model, the store re-promotes whichever row `query().first()` returns. With no explicit `order_by`, this depends on the database's default row ordering, which is undefined for SQL and may be insertion-order on SQLite and arbitrary on Postgres. Two concurrent deletes could promote different rows.
**Fix:** Either add an explicit `order_by(ModelRegistrationRecord.created_at)` so the choice is deterministic, or remove the auto-promote and require an explicit `select_active` follow-up.

### IN-04: `_to_dict` model_id lookup chain is silent-failure-prone

**File:** `backend/src/db/stores/model_store.py:76`
**Issue:** `kwargs.get("id") or kwargs.get("model") or kwargs.get("model_id")` resolves to the first truthy of three keys. If the value at `kwargs["id"]` is a numeric `0` or the empty string (improbable but legal for `Any`), the chain falls through silently. The fallback is also asymmetric: callers writing `kwargs["model_id"]` succeed, callers writing `kwargs["model_name"]` (which an autocomplete might offer) silently get `None`.
**Fix:** Document the supported keys, or assert that exactly one is present. Low priority — not in any current hot path.

### IN-05: `cancel_for_compensation` exists on both `MissionStore` and `EpisodeStore` with identical bodies (modulo type)

**Files:** `backend/src/db/stores/mission_store.py:111-124`, `backend/src/db/stores/episode_store.py:132-145`
**Issue:** Two near-identical methods. Each is called from exactly one site in `task_center/mission/starter.py` and `task_center/mission/handler.py`. The duplication isn't a bug, but the pattern would benefit from a single generic helper or from being inlined.
**Fix:** Optional. Leave for a future refactor; flagging only for the dead-candidate list since it's adjacent to the cleanup pass.

### IN-06: `_serialize_run_summary` and `SerializedRow` type alias are inconsistent JSON shapes

**Files:** `backend/src/db/stores/agent_run_store.py:12-22` vs `backend/src/db/stores/task_center_store.py:20`
**Issue:** `task_center_store.py` defines `SerializedRow = dict[str, Any]` and uses it consistently for the request/run/task serializers. `agent_run_store.py` returns plain `dict[str, Any]` from `_serialize_run_summary` without using the alias. Minor consistency nit. (Also: if WR-07's deletion of `list_runs_for_tasks` is accepted, this helper goes away entirely.)
**Fix:** Skip if WR-07 is applied. Otherwise harmonise.

---

## Legacy / Dead Candidates (Cleanup Pass)

This is the consolidated list for the follow-up cleanup commit. Everything below is verified-zero-call-sites in `backend/src` (and where noted, in `backend/tests`).

### Dead columns (ORM-defined, never written by any store, only read by audit recorder as `None`)

| Table | Column | Defined in | Audit read | Verdict |
|---|---|---|---|---|
| `missions` | `context` | `db/models/mission.py:47` | `live_e2e/audit/recorder.py:65` | Always None — drop |
| `missions` | `summary` | `db/models/mission.py:48` | `live_e2e/audit/recorder.py:66` | Always None — drop |
| `episodes` | `context` | `db/models/episode.py:55` | `live_e2e/audit/recorder.py:83` | Always None — drop |
| `episodes` | `summary` | `db/models/episode.py:56` | `live_e2e/audit/recorder.py:84` | Always None — drop |
| `attempts` | `context` | `db/models/attempt.py:50` | `live_e2e/audit/recorder.py:108` | Always None — drop |
| `attempts` | `summary` | `db/models/attempt.py:51` | `live_e2e/audit/recorder.py:109` | Always None — drop |
| `task_center_tasks` | `system_prompt` | `db/models/task_center.py:93` | `live_e2e/audit/recorder.py:125` | Always None — drop |
| `task_center_tasks` | `user_prompt` | `db/models/task_center.py:94` | `live_e2e/audit/recorder.py:126` | Always None — drop |

When dropping, add each name to `_DROPPED_COLUMNS` in `db/engine.py` so existing dev DBs get patched.

### Dead DTO fields (mirror of dead columns)

- `Mission.context`, `Mission.summary` — `task_center/mission/mission.py:32-33`
- `Episode.context`, `Episode.summary` — `task_center/episode/episode.py:42-43`
- `Attempt.context`, `Attempt.summary` — `task_center/attempt/state.py:49-50`

### Dead store methods (no callers in src or tests)

- `EpisodeStore.list_for_missions` — `episode_store.py:111-130`
- `AttemptStore.list_for_episodes` — `attempt_store.py:149-164`
- `AgentRunStore.list_runs_for_tasks` — `agent_run_store.py:76-85` (plus its only consumer `_serialize_run_summary` at `:12-22`)
- `TaskCenterStore.list_tasks_for_attempts` — `task_center_store.py:227-245`

### Dead infrastructure

- `AsyncStoreMixin` class — `db/stores/base.py:51-73`
- `_async_engine` module global — `db/engine.py:28`
- `_async_session_factory` module global — `db/engine.py:29`
- `get_async_engine()` accessor — `db/engine.py:42-44`
- `get_async_session_factory()` accessor — `db/engine.py:47-49`
- Async-engine construction block in `initialize_db` — `db/engine.py:334-361`
- Top-level `import importlib.util` (line 10) becomes unused if async block is removed

### Dead migration metadata

- `_DROPPED_COLUMNS["task_center_attempt"]` entry — `db/engine.py:79-83`
- `_RENAMED_COLUMNS["task_center_attempt"]` entry — `db/engine.py:89-92`

(Note: keep `_LEGACY_TABLES_TO_DROP = {"task_center_attempt"}` at `:95-97` — that's the one piece of the trifecta that actually fires.)

### Suspect but not verified-dead (manual review needed)

- `ModelStore.is_available` vs `SyncStoreMixin.is_ready` — duplicated readiness contract (WR-03). Decision required: pick one.
- `ContextPacketStore.insert` returning input id rather than persisted id (IN-02). Pick a contract.

---

_Reviewed: 2026-05-13T22:35:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
