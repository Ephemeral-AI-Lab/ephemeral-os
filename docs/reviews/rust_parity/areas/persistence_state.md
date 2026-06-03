# Persistence / state / db / types parity — Rust port audit

Area: agent-core persistence (`eos-db`, `eos-state`, `eos-types`, `parity/sqlite`).
Reviewed against Python ground truth `backend/src/db/`, `backend/src/task/`,
`backend/src/message/`, and `backend/src/workflow/_core/`.

Verdict in one line: the seven-table row model, the per-entity store transitions,
the OCC flip, the outcome-normalization split, and the message/content-block
schema are faithfully ported. There is **one genuine divergent dynamic** (a
silent drop of an empty-`task_id` outcome record on the attempt/workflow parse
path, forced by the non-empty `TaskId` newtype) and **two test-coverage gaps**
(the schema-parity test never diffs the live migration against ground truth; the
message schema is a static captured fixture with no live Rust type behind it).

---

## Ground truth

Python row models (`backend/src/db/models/`):
- `requests` — `request.py:17-38` (`id` VARCHAR(36) PK, `cwd`, `sandbox_id?`,
  `request_prompt`, `root_task_id?`, `status` default `"running"`, timestamps,
  `finished_at?`).
- `tasks` — `task.py:18-46` (`id` VARCHAR(96) PK, FK `request_id` ON DELETE
  CASCADE, `role`, `instruction`, `status`, nullable `workflow_id`/`iteration_id`/
  `attempt_id` (indexed), `agent_name?`, `needs` JSON default `list`, `outcomes`
  JSON default `list`, `terminal_tool_result?` JSON, timestamps; **no FK back to
  workflow/iteration/attempt** — those are loose indexed columns).
- `workflows` — `workflow.py:13-39` (`id` VARCHAR(36) PK, FK `request_id` CASCADE,
  `parent_task_id` NOT NULL indexed, `goal` Text, `status` VARCHAR(16),
  `iteration_ids` JSON, `outcomes` **Text (raw projection string, nullable)**,
  timestamps, `closed_at?`).
- `iterations` — `iteration.py:17-58` (PK, FK `workflow_id` CASCADE, `sequence_no`
  Integer, `creation_reason`, `goal`, `attempt_budget`, `status`, `attempt_ids`
  JSON, `deferred_goal?`, timestamps, `closed_at?`, `outcomes` Text nullable;
  `UniqueConstraint(workflow_id, sequence_no)` = `uq_iteration_workflow_sequence`).
- `attempts` — `attempt.py:17-55` (PK, FK `iteration_id` CASCADE, `workflow_id`
  (indexed, **no FK**), `attempt_sequence_no`, `stage`, `status`,
  `planner_task_id?`, `generator_task_ids`/`reducer_task_ids`/`outcomes` JSON,
  `deferred_goal?`, `fail_reason?` VARCHAR(48), timestamps, `closed_at?`;
  `UniqueConstraint(iteration_id, attempt_sequence_no)` =
  `uq_attempt_iteration_sequence`).
- `agent_runs` — `agent_run.py:17-43` (PK, FK `task_id` **unique** CASCADE,
  `initial_messages?` JSON, `agent_name`, `message_history?` JSON,
  `terminal_tool_result?` JSON, `token_count` Integer default 0, `error?`,
  `created_at`, `finished_at?`; **no `updated_at`**).
- `model_registrations` — `model_registration.py:13-33` (`id` Integer PK
  autoincrement, `key` unique, `label`, `class_path`, `kwargs_json` default `"{}"`,
  `is_active` Boolean default `False`, timestamps).

Stores / transitions (`backend/src/db/stores/`):
- `task_store.py`: `finish_request` is idempotent on a terminal request
  (`status in ("done","failed")` → return unchanged, `:97-98`);
  `set_task_status_if_current` raises `LookupError` when the row is missing
  (`:215`) and returns `None` on a status mismatch (`:216-217`) — two distinct
  signals; `upsert_task` is insert-or-full-field-update bumping `updated_at`.
- `iteration_store.py`: `close_succeeded` writes status+outcomes in one
  `db.commit()` (atomic, `:142-152`); `set_status`/`set_deferred_goal` leave a
  column unchanged when the arg is `None`; `list_for_workflow` orders by
  `sequence_no` ASC (`:110`).
- `attempt_store.py`: `insert` defaults `stage=plan,status=running` (`:36-37`);
  `close` stamps `stage=closed`, sets status/fail_reason, conditionally writes
  outcomes, defaults `closed_at` to now (`:120-141`); `list_for_iteration` orders
  by `attempt_sequence_no` ASC. `_to_dto` parses `outcomes` via
  `parse_outcomes_record` (`:178`).
- `workflow_store.py`: `insert` defaults `status=open, iteration_ids=[],
  outcomes=None`; `set_status` conditional column writes; `list_for_parent_task`
  orders by `created_at` ASC (`:86`).
- `agent_run_store.py`: `finish_run` **unconditionally** assigns
  message_history/terminal_tool_result/token_count/error (passing `None` sets the
  column NULL, `:52-55`); missing run → `None` (`:50-51`).
- `model_store.py`: `register` deactivates-all only when `activate=True`
  (`:118-126`), otherwise updates fields and preserves `is_active`; `delete`
  promotes the oldest remaining row (`ORDER BY created_at ASC, id ASC`) when the
  deleted row was active (`:142-163`); `_resolve_env_placeholders` /
  `_redact_secrets` constants at `:19-58`.

Outcome normalization (`backend/src/workflow/_core/outcomes.py`) — the subtle part:
- `present_status(raw)`: `"done"` → `"success"`, else `"failed"` (`:39-40`).
- `_normalize_status(value)`: `"success"` → `"success"`, else `"failed"` (`:187-191`).
- `task_outcomes_from_row` (`:43-66`): for each record, `setdefault("task_id",
  task_id)` and `setdefault("status", present_status(task.status))` — i.e. a
  **missing** status is filled with `present_status` (`done`→success), a **present**
  status is later run through `_normalize_status` (`done`→failed). Role falls back
  to the task role.
- `parse_outcomes_record` (`:156-177`): the attempt/iteration/workflow path — **no
  status fill** (missing → `_normalize_status("")` → failed), **no role fallback**
  (missing/invalid → `"generator"`), `fallback_task_id = str(record.task_id or "")`.
- `_outcomes_from_record` (`:200-225`): **always emits exactly one outcome**, even
  when `task_id` resolves to `""` (`ExecutionTaskOutcome.task_id` is an unvalidated
  `str`). It never drops a record.
- `project_iteration_outcomes` (`:102-127`): closing (last) attempt only — passing
  → reducer successes; failed → failed generator/reducer; earlier attempts' reducer
  successes are never surfaced.

Message model (`backend/src/message/message.py`): five content blocks
(`Text/Thinking/ToolUse/ToolResult/SystemNotification`) discriminated on `type`;
`ToolResultBlock` carries `is_error`, `metadata`, and the engine-only `is_terminal`
marker (`:43-48`); `Message{role∈{user,assistant}, content:list[ContentBlock]}`.
Canonical captured schema: `parity/schemas/message.schema.json`.

Architecture corroboration: `docs/architecture/workflow/index.html` (Workflow→
Iteration→Attempt, reducer exit gate, store as coordination substrate).

---

## Rust mapping

| Python | Rust DTO / repo |
| --- | --- |
| `db/models/*.py` row models | `eos-db/src/rows.rs:23-116` (typed `*Row` structs) + DTOs in `eos-state/src/{request,task,workflow,iteration,attempt,agent_run,model}.rs` |
| `Base.metadata` canonical schema | `parity/sqlite/schema.sql` (captured) + **live** `eos-db/migrations/0001_initial.sql` |
| `task_store.py` | `eos-db/src/repositories/request_task.rs` (`SqlRequestTaskStore`, both `RequestStore`+`TaskStore`) |
| `workflow_store.py` | `eos-db/src/repositories/workflow.rs` |
| `iteration_store.py` | `eos-db/src/repositories/iteration.rs` |
| `attempt_store.py` | `eos-db/src/repositories/attempt.rs` |
| `agent_run_store.py` | `eos-db/src/repositories/agent_run.rs` |
| `model_store.py` | `eos-db/src/model_registry.rs` (`ModelRegistry`) |
| `workflow/_core/outcomes.py` (typed algebra) | `eos-state/src/outcomes.rs` |
| `workflow/_core/outcomes.py` (raw-record normalization) | **moved** to `eos-db/src/rows.rs:156-249` (the eos-db parse boundary, spec §6.8) |
| `message/message.py` | **No live Rust type in audited crates.** Verified by schema fixture `parity/schemas/message.schema.json` + snapshot `parity/tests/snapshots/schema_snapshots__message.snap` |
| store protocols | `eos-state/src/store.rs` (seven sealed `#[async_trait]` traits) |
| ids | `eos-types/src/ids.rs` (twelve non-empty `String` newtypes) |

Composition root: `eos-db/src/composition.rs` (`Database::open` → one `Arc<dyn
…Store>` per entity). PRAGMA `foreign_keys = ON` per connection (`migrations/
0001_initial.sql:5`, set in `pool.rs`).

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | SQLite schema = 7 target tables, 3 unique constraints (canonical capture) | match | none | `parity/sqlite/schema.sql:26-139` | `parity/tests/sqlite_schema.rs:16-55` | Test greps the *captured* `schema.sql` for 7 `CREATE TABLE` + the 3 named uniques. Holds. |
| 1b | Live migration `0001_initial.sql` matches the canonical create-all shape | partial | medium | `parity/sqlite/schema.sql:1-139` | `eos-db/migrations/0001_initial.sql:7-117` | Migration is the thing actually run; **nothing tests it against ground truth**. Benign type drift (TEXT vs VARCHAR/DATETIME/JSON, inline defaults, FK-`UNIQUE` on `agent_runs.task_id` instead of a separate `ix_agent_runs_task_id` index) is untested. See Disparity D2. |
| 2 | Task/Workflow/Iteration/Attempt row models + persisted transitions; store is the coordination substrate | match | none | `db/stores/*.py`; `workflow/_core/state.py:33-164` | `rows.rs:36-102`, `request_task.rs:131-239`, `workflow.rs:31-132`, `iteration.rs:31-186`, `attempt.rs:39-203` | Full-field upsert, OCC flip, atomic `close_succeeded`, append-id, ordered lists all present and faithful. |
| 2a | `set_task_status_if_current` OCC: `Ok(None)` on mismatch, error on missing | match | none | `task_store.py:215-217` | `request_task.rs:215-224` | Two distinct signals preserved (`Err(NotFound)` missing vs `Ok(None)` mismatch). |
| 2b | `finish_request` idempotent on terminal | match | none | `task_store.py:97-98` | `request_task.rs:110-112` | `status=="done"||"failed"` → return unchanged. |
| 2c | `close_succeeded` writes status+outcomes atomically | match | none | `iteration_store.py:142-152` | `iteration.rs:144-169` | Single UPDATE = atomic. |
| 2d | List orderings (`sequence_no` / `attempt_sequence_no` / `created_at` ASC) | match | none | `iteration_store.py:110`, `attempt_store.py:149`, `workflow_store.py:86` | `iteration.rs:176`, `attempt.rs:193`, `workflow.rs:122` | All ASC, identical key columns. |
| 2e | Outcome-normalization split (`present_status` vs `_normalize_status`) | match | none | `outcomes.py:39-40,187-191`; `task_outcomes_from_row:53-66` | `outcomes.rs:60-66`; `rows.rs:170-235` | done→success (fill) vs done→failed (present) split correctly reproduced; covered by `rows.rs:380-457` parity tests. |
| 2f | `finish_run` unconditionally overwrites nullable columns | match | none | `agent_run_store.py:52-55` | `agent_run.rs:69-72` | No COALESCE — `None` sets NULL, matching Python. |
| 2g | `model_store` delete promotes oldest active; register `is_active` semantics | match | none | `model_store.py:142-163,118-126` | `model_registry.rs:197-225,154-166` | `ORDER BY created_at ASC, id ASC`; `is_active` CASE preserves existing on `activate=False`. |
| 3 | Message / content-block model parity (`message.schema.json` + snapshots) | match | none | `message/message.py:14-91` | `parity/schemas/message.schema.json` + `parity/tests/snapshots/schema_snapshots__message.snap` | Snapshot == fixture == Pydantic model (5 blocks, discriminator, `is_terminal`/`metadata`). See Extra finding E1 for the coverage caveat. |
| 4 | No peer-to-peer agent comms — coordination only via persisted store | match | none | (architecture: store-only) | `eos-state/src/`, `eos-db/src/`, `eos-types/src/` (grep: no `tokio::net`/`reqwest`/`tonic`/`mpsc`/`broadcast`/`TcpStream`/`UnixStream`/`channel(`) | Audited crates expose only `Store` traits + SQL repos; no transport. |
| — | Empty-`task_id` outcome record handling (attempt/workflow parse path) | divergent | low | `outcomes.py:200-225` (always emits, `task_id=""`) | `rows.rs:198` (`task_id_raw.parse().ok()?` → **dropped**) | Forced by the non-empty `TaskId` newtype. See Disparity D1. |

---

## Disparities

### D1 — Empty-`task_id` outcome record is silently dropped on the attempt/workflow parse path (divergent, low)

**Evidence.** Python `_outcomes_from_record` (`backend/src/workflow/_core/outcomes.py:200-225`)
always returns exactly one `ExecutionTaskOutcome`, even when the resolved
`task_id` is the empty string:
```python
task_id = str(record.get("task_id") or fallback_task_id or "")   # may be ""
...
return (ExecutionTaskOutcome(status=..., role=role, task_id=task_id, outcome=...),)
```
`ExecutionTaskOutcome.task_id` is an unvalidated `str` (`outcomes.py:32`), and
`to_record`/`records_json` (`:144-181`) serialize that empty id back to the
column, so a persisted `iteration.outcomes` / `attempt.outcomes` containing a
record with `task_id=""` is representable on the Python side.

The Rust port routes the attempt/workflow path through
`normalize_attempt_outcomes` (`agent-core/crates/eos-db/src/rows.rs:240-249`) with
`fallback_task_id = ""`, into `outcome_from_record`, which at `rows.rs:198` does:
```rust
let task_id = task_id_raw.parse().ok()?;   // empty/unparseable -> None -> record DROPPED
```
Because `TaskId::from_str("")` returns `CoreError::EmptyId`
(`eos-types/src/ids.rs:68-73`), an empty-id record produces `None` and is filtered
out entirely. The Rust test at `rows.rs:432-456` even pins this drop as intended
("missing task_id → dropped (unrepresentable empty TaskId)").

**Three-way disagreement.** Python emits `task_id=""`; the Rust `TaskId` newtype is
designed to forbid `""` (intentional type choice, `eos-types/src/ids.rs:18`); the
forced consequence is a silent drop. So this is *divergent-with-rationale*, not a
naive bug — but it is exactly the "silently drops a record" class this audit hunts,
so it must be surfaced, not blessed.

**Scope.** Only the `parse_outcomes_record` path (attempt + iteration + workflow
`outcomes` columns), where `fallback_task_id=""`. The task-column path is fine:
`task_outcomes_from_row` (`outcomes.py:57`) and `normalize_task_outcomes`
(`rows.rs:216-235`) both fill `task_id` from the owning task id first, so an empty
id only occurs when a record explicitly carries `"task_id": ""`.

**Why it matters.** A count mismatch: Python reads N records from a malformed
persisted `outcomes` blob, Rust reads N−k. If reducer/generator evidence was ever
written with a blank id, the iteration/workflow projection silently shrinks. Low
severity because the *first-party writer* (`records_json`/`to_record`) only writes
ids that came from real task rows, and the Rust serializer never emits `""`; the
gap is reachable only for externally-mutated or legacy rows.

**Suggested fix.** Either (a) document the drop as an accepted cutover behavior in
`rows.rs` and add a parity note that pre-cutover data is assumed to carry non-empty
ids, or (b) if byte-for-byte projection parity over arbitrary persisted blobs is
required, surface a `DbError::InvalidEnum`-style error (or a sentinel id) instead of
silently dropping, so the divergence is loud rather than silent.

### D2 — Schema-parity test never diffs the live migration against ground truth (partial, medium)

**Evidence.** `parity/tests/sqlite_schema.rs:16-55` only greps the *captured*
`parity/sqlite/schema.sql` for seven `CREATE TABLE` and the three named unique
constraints. The DDL actually executed at runtime is
`eos-db/migrations/0001_initial.sql`, which is never cross-checked against the
Python `Base.metadata.create_all` shape or against `schema.sql`. Concrete, currently
benign differences that the gap leaves untested:
- Column types: migration uses `TEXT`/`INTEGER` uniformly
  (`0001_initial.sql:8-116`); ground truth uses `VARCHAR(n)`/`DATETIME`/`JSON`/
  `BOOLEAN` (`schema.sql:26-139`). SQLite type affinity makes these equivalent
  today.
- `agent_runs.task_id` uniqueness: the migration expresses it inline as
  `TEXT NOT NULL UNIQUE` (`0001_initial.sql:96`); the canonical capture expresses
  it as a separate `CREATE UNIQUE INDEX ix_agent_runs_task_id`
  (`schema.sql:6`). Same constraint, different object; the index name does not
  exist in the migrated DB.
- Inline `DEFAULT '[]'` / `DEFAULT '{}'` / `DEFAULT 0` on the migration JSON/count
  columns (`0001_initial.sql:29,30,81,82,83,101,112,113`) have no counterpart in
  the captured DDL.

**Why it matters.** The seven-table/three-constraint test would still pass if a
future migration silently dropped a column default, flipped a CASCADE, or renamed a
column — it only inspects the hand-maintained capture, not the migrated database.

**Suggested fix.** Add a test that opens an in-memory SQLite via `Database::open`,
introspects `sqlite_master` / `PRAGMA table_info`, and asserts table+column+constraint
presence against an expected set derived from `schema.sql`. This closes the loop
between the captured ground truth and the executed migration.

---

## Extra findings

- **E1 — Message parity is a static fixture, not a live Rust type.** `grep` for
  `struct Message` / `enum ContentBlock` / `struct ToolResultBlock` across
  `eos-types/src`, `eos-state/src`, `eos-db/src` returns nothing; the only Rust
  consumers of these blocks live in `eos-engine`. `parity/schemas/message.schema.json`
  is a captured Pydantic schema pinned by `parity/tests/schema_snapshots.rs:21-43`
  (`insta`), and the `.snap` is byte-identical to it. So Invariant 3 is satisfied
  *as specified* (the artifact matches the Python model), but the snapshot can only
  catch a fixture edit — it cannot catch drift in a Rust `Message`/`ContentBlock`
  type because none exists in the audited crates. Persistence of message content is
  via the opaque `agent_runs.initial_messages` / `message_history` JSON columns
  (`rows.rs:108,110`, decoded as `Vec<JsonObject>`), which never structurally
  validate the block shape on read/write.

- **E2 — `append_iteration_id` / `append_attempt_id` are more concurrency-safe than
  Python (positive divergence).** Python does a read-modify-write
  (`list(record.iteration_ids or []); ids.append(...)`, `iteration_store.py:59-61`,
  `workflow_store.py:53-55`) which races under concurrent appends; Rust appends
  in-SQL with `json_insert(COALESCE(col,'[]'),'$[#]',?)` in a single atomic UPDATE
  (`iteration.rs:77`, `workflow.rs:72`). Same tail-append ordering, stronger
  atomicity. Not a regression.

- **E3 — `Task` DTO drops the `created_at`/`updated_at` columns by design.** The
  table has both timestamps (`schema.sql:120-121`), but `TaskRow`
  (`rows.rs:50-51`) and the `Task` DTO (`eos-state/src/task.rs:65-97`) omit them,
  matching the Python `Task` dataclass (`task/task.py:38-51`), which also has no
  timestamp fields. `sqlx::FromRow` ignores the extra columns. Faithful.

- **E4 — `Request.status` is a free `String` on both sides** (`rows.rs:30`,
  `request.py:27`), not an enum, so `finish_request` accepts arbitrary status
  strings — intentional parity, no validation gap introduced.

- **E5 — `model_registrations.id` autoincrement parity.** Python uses `Integer`
  (not `BigInteger`) explicitly for SQLite autoincrement
  (`model_registration.py:18-20`); Rust migration uses
  `INTEGER PRIMARY KEY AUTOINCREMENT` (`0001_initial.sql:108`) and `i64` in the row
  (`model_registry.rs:44`). Match.

---

## Open questions

1. **Timestamp wire format on a shared-DB cutover.** Python persists `DateTime` via
   SQLAlchemy and serializes with `.isoformat()` (e.g. `"+00:00"`,
   `task_store.py:24-26`); the Rust migration stores timestamps as `TEXT` encoded by
   sqlx's `time::OffsetDateTime` (`0001_initial.sql:3-4`). Pure-Rust round-trips are
   fine, but I could not verify byte-identical string encoding without running both
   stacks against one DB file. If a phased cutover ever reads Python-written rows
   from Rust (or vice versa), confirm the RFC3339 encodings agree (offset spelling,
   fractional-second precision). Flagged as a question, not a finding.

2. **Is an empty-`task_id` outcome record ever persisted in practice?** D1's severity
   hinges on this. The first-party writer path does not produce one; confirm no
   tool/engine code writes a `{"task_id": ""}` outcome record before downgrading D1
   to informational.

3. **No `eos-state`/`eos-db` test asserts the FK CASCADE behavior end-to-end** (e.g.
   deleting a `request` cascades to `tasks`/`workflows`). The PRAGMA is set and the
   FKs are declared, but I found no integration test exercising a cascade. Likely
   covered at the runtime layer (out of this area's scope), but worth confirming.
