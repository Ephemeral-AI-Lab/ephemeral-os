# Independent Verification — Persistence / state / db / types parity (agent-core)

Verifier opened every file; trusted nothing. Python = ground truth, Rust must
match. Bilateral `file:line` evidence below. Tests actually run (see footer).

## Invariant verdict table

| # | Invariant | Status | Severity | Evidence (rust ↔ python) |
|---|---|---|---|---|
| 1 | SQLite schema = 7 target tables + 3 unique constraints (canonical capture) | confirmed_match | none | rs `parity/tests/sqlite_schema.rs:19-32` (7 `CREATE TABLE`, exact count) + `:45-54` (full constraint tuples incl. `ix_agent_runs_task_id`). py canonical `parity/sqlite/schema.sql:26-139`, captured from `Base.metadata.create_all` (`parity/_capture/capture.py:175-209`). Test PASSES. |
| 2 | Task/Workflow/Iteration/Attempt row models + persisted transitions (store is coordination substrate) | confirmed_match | none | rs typed rows `eos-db/src/rows.rs:23-116`; `request_task.rs:131-239`, `workflow.rs:31-132`, `iteration.rs:31-186`, `attempt.rs:39-203` (full-field upsert, append-id via `json_insert`, ordered lists, atomic close). py stores `db/stores/*.py`; DTOs `workflow/_core/state.py`. Integration round-trips PASS. |
| 3 | OCC `set_task_status_if_current`: `Err(NotFound)` on missing, `Ok(None)` on mismatch | confirmed_match | none | rs `request_task.rs:215-221` missing → `Err(DbError::NotFound)`; `:222-224` `current.status != enum_to_db(expected)` → `Ok(None)`. py `task_store.py:209-211` `LookupError` missing; `:212-213` `if record.status != expected_status: return None`. |
| 4 | `finish_request` idempotent on a terminal request | confirmed_match | none | rs `request_task.rs:110-112` `status=="done"\|\|"failed"` → `Ok(Some(row))` unchanged. py `task_store.py:96-98` `if record.status in ("done","failed"): return _serialize_request(record)`. |
| 5 | `close_succeeded` writes status+outcomes atomically | confirmed_match | none | rs `iteration.rs:153-156` single `UPDATE … SET status='succeeded', outcomes=?`. py `iteration_store.py:143-152` both assignments inside one `db.commit()`. |
| 6 | List orderings (sequence_no / attempt_sequence_no / created_at ASC) | confirmed_match | none | rs `iteration.rs:176` `ORDER BY sequence_no ASC`; `attempt.rs:193` `ORDER BY attempt_sequence_no ASC`; `workflow.rs:122` `ORDER BY created_at ASC`. py `iteration_store.py:108`, `attempt_store.py:148`, `workflow_store.py:84` all `.asc()`. |
| 7 | Outcome-normalization split: `present_status` (fill missing, done→success) vs `_normalize_status` (present, done→failed) | confirmed_match | none | rs `eos-state/src/outcomes.rs:60-66` `present_status`; `eos-db/src/rows.rs:170-175` `normalize_status`; applied at `rows.rs:227-231` (`contains_key("status")` ? present : fill). py `outcomes.py:39-40` `present_status`; `:187-191` `_normalize_status`; `:58` `setdefault("status", present_status(...))`. Test `rows.rs:380-457` PASSES. |
| 8 | `finish_run` unconditionally overwrites nullable columns (None → NULL) | confirmed_match | none | rs `agent_run.rs:69-72` `UPDATE … message_history=?, terminal_tool_result=?, token_count=?, error=?` — direct binds, NO `COALESCE`. py `agent_run_store.py:50-54` `record.message_history = message_history` (unconditional). |
| 9 | `set_task_status*` is conditional (COALESCE) — NOT the same as finish_run | confirmed_match | none | rs shared SQL `request_task.rs:18-21` `outcomes = COALESCE(?, outcomes), terminal_tool_result = COALESCE(?, …)`. py `task_store.py:188-191` / `:225-228` `if outcomes is not None: …`. (Distinct from #8; correctly mirrored.) |
| 10 | model `delete` promotes oldest active; `register` is_active semantics | confirmed_match | none | rs `model_registry.rs:215-218` `UPDATE … is_active=1 WHERE id=(SELECT … ORDER BY created_at ASC, id ASC LIMIT 1)`; `:165` `is_active = CASE WHEN excluded.is_active=1 THEN 1 ELSE existing END`. py `model_store.py:152-159` `ORDER BY created_at ASC, id ASC`; `:118-126` deactivate-all + activate only when `activate=True`. Test PASSES. |
| 11 | Message / content-block fixture↔snapshot parity (5 blocks, discriminator, is_terminal/metadata) | confirmed_match | none | rs fixture `parity/schemas/message.schema.json` is byte-identical to snapshot `parity/tests/snapshots/schema_snapshots__message.snap`; both carry 5 blocks, the `type` discriminator mapping, and `ToolResultBlock.is_terminal`/`metadata`. Pinned by `schema_snapshots.rs:20-35`. py source `message/message.py:14-91`, captured via `model_json_schema()` (`capture.py:55-80`). Test PASSES. **Caveat: see NF-1 (self-referential test).** |
| 12 | No peer-to-peer agent comms — coordination only via persisted store state | confirmed_match | none | rs grep over `eos-state/src`, `eos-db/src`, `eos-types/src` for `tokio::net\|reqwest\|tonic\|TcpStream\|UnixStream\|mpsc::\|broadcast::\|::channel(\|oneshot::channel`: only false hits ("sequence" comments). Store trait surface `eos-state/src/store.rs:46-309` is pure CRUD; no channel/network methods. py CLAUDE.md + `docs/architecture/workflow/index.html` (store-only). |
| 13 | Empty-task_id outcome record handling on attempt/workflow parse path | confirmed_disparity (low) | low | See D1 — genuine divergence. |

### Cross-check the investigator did NOT make (verified independently)

`updated_at` write parity: the Python store methods (`append_attempt_id`,
`set_status`, `close`, `set_planner_task_id`, …) do **not** explicitly assign
`updated_at`, but every SQLAlchemy model declares
`onupdate=lambda: datetime.now(UTC)` (`models/{task,workflow,iteration,attempt,
request,model_registration}.py`), so ORM bumps it on any UPDATE. The Rust repos
set `updated_at = ?` explicitly in each UPDATE. Behavior matches. (`append_*`
also bumps `updated_at` in Rust — `iteration.rs:78`, `workflow.rs:73`; in Python
the `onupdate` fires on the JSON column change. Match.)

## Disparity adjudication

### D1 — empty-task_id outcome record silently dropped (attempt/workflow path) — CONFIRMED (low)
- rs `eos-db/src/rows.rs:198` `let task_id = task_id_raw.parse().ok()?;` → `TaskId::from_str("")` returns `CoreError::EmptyId` (`eos-types/src/ids.rs:66-75`) → `None` → `filter_map` drops the record. `normalize_attempt_outcomes` (`rows.rs:240-249`) passes `fallback_task_id=""`, so a record with no `task_id` is unrecoverable and dropped.
- py `outcomes.py:200-225` `_outcomes_from_record` ALWAYS emits one `ExecutionTaskOutcome`; `task_id = str(record.get("task_id") or fallback_task_id or "")` can be `""` and is still emitted.
- Verdict: **confirmed**, severity **low**. The system's own serializer (`to_record`, `outcomes.py:144-153`) always writes a non-empty `task_id` from a typed `ExecutionTaskOutcome.task_id`, so an empty-id record only arises from externally-authored/corrupt JSON. The task-column path (`normalize_task_outcomes`) is unaffected because its `owning_task_id` fallback is the (always non-empty) owning task id. Pinned as intended by `rows.rs:434-456` (the `{"role":"generator","outcome":"orphan"}` case asserted dropped).

### D2 — schema-parity test never diffs the live migration against ground truth — CONFIRMED (low/medium)
- rs The parity test `parity/tests/sqlite_schema.rs:16-55` only greps the canonical `schema.sql`. The live migration `eos-db/migrations/0001_initial.sql` is what actually runs (`pool.rs:63` `sqlx::migrate!().run(...)`), and it differs in declared shape:
  - `TEXT`/`INTEGER` uniformly vs canonical `VARCHAR(N)`/`DATETIME`/`JSON`/`BOOLEAN` (`0001_initial.sql:8-117`).
  - `agent_runs.task_id TEXT NOT NULL UNIQUE` inline (`:96`) — no named `ix_agent_runs_task_id` UNIQUE INDEX.
  - `model_registrations.id INTEGER PRIMARY KEY AUTOINCREMENT` (`:112`) vs canonical `INTEGER NOT NULL … PRIMARY KEY` (no AUTOINCREMENT) — changes rowid-reuse-after-delete semantics.
  - inline `DEFAULT '[]'`/`'{}'`/`0` on JSON/int columns (canonical has no defaults).
- The only test touching the migration, `integration.rs:373-410` `migrations_create_schema`, asserts merely (a) the 7 tables exist, (b) three renamed columns are selectable, (c) `PRAGMA foreign_keys=1`. It does NOT diff column types, the 3 unique constraints, the index name, or AUTOINCREMENT.
- Verdict: **confirmed**, severity **low** (functionally) / **medium** (coverage). SQLite type affinity makes `VARCHAR→TEXT`, `DATETIME→NUMERIC` benign, and the inline `UNIQUE` enforces the same task_id uniqueness as the named index — so constraint *behavior* matches. The gap is real but the runtime semantics are equivalent; this is a test-coverage hole, not a runtime break.

## New findings

- **NF-1 (medium, message parity test):** `schema_snapshots.rs:20-35` is
  **self-referential** — it reads the committed Python-captured
  `schemas/*.json` and snapshots each file against its own
  `.snap`. It pins the Python fixture against drift but validates **no Rust
  type**. So invariant #11 ("Rust message model matches the schema") is not
  actually tested by this crate; only fixture stability is. Stated plainly so a
  reader does not over-read "byte-identical / pinned by test".

- **NF-2 (low, cross-area — route to eos-llm-client verifier):** The live Rust
  `ContentBlock` (`eos-llm-client/src/message.rs:62-67`) renames Python
  `ThinkingBlock` (`type:"thinking"`) to `ContentBlock::Reasoning` serializing as
  `"reasoning"` (GC-llm-client-01), with a one-way `#[serde(alias="thinking")]`.
  This is OUT OF SCOPE for eos-db/state/types (no Message type exists here) and
  is intentional + documented, so it is **not** a persistence-layer disparity.
  But it has a persistence-visible consequence: these blocks are stored verbatim
  as opaque JSON in `agent_runs.message_history`/`initial_messages` via
  `json_col` (`agent_run.rs:67-71`, `rows.rs:350-352`). A Rust-written transcript
  emits `"reasoning"` where Python wrote `"thinking"`; Python's
  `Literal["thinking"]` would **reject** `"reasoning"` on read, so Rust→Python
  read-compat for that block is broken. eos-db itself is faithful (stores
  whatever bytes it is given); the rename is owned upstream. Flagging for the
  llm-client area, not adjudicating it here.

## Overall verdict

Rust persistence fidelity for this area is **high**. Every load-bearing dynamic
— OCC (`Err(NotFound)` missing / `Ok(None)` mismatch), `finish_request`
terminal idempotency, atomic `close_succeeded`, the `present_status` vs
`_normalize_status` outcome split, `finish_run` null-overwrite vs
`set_task_status` COALESCE, list orderings, model-delete oldest-promotion +
register is_active CASE, and store-only coordination (no P2P) — is correctly
mirrored with matching constants/operators, and the relevant tests
(`attempt_outcomes_parity`, `task_outcomes_parity`,
`model_registry_active_and_resolve`, `migrations_create_schema`,
`sqlite_schema`, `schema_snapshots`) all pass. No FALSE MATCH found: nothing the
investigator called "match" is actually broken within this area. The two
investigator disparities (D1 empty-task_id drop, D2 migration-never-diffed) are
both real and both correctly rated low. The only items the investigator's
phrasing under-described — the self-referential schema-snapshot test (NF-1) and
the upstream `thinking→reasoning` rename (NF-2) — are not in-area defects; NF-2
in particular must NOT be escalated into a persistence break (it would be a
false alarm). Net: confirmed.

---
Tests run by verifier (all green):
- `cargo test -p eos-parity --test sqlite_schema --test schema_snapshots` → 3 passed
- `cargo test -p eos-db` → 13 unit + 7 integration passed
