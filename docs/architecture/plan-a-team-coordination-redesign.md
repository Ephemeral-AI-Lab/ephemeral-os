# Plan A: Team Coordination Redesign — Task Center Architecture

**Status:** IMPLEMENTED — Updated 2026-04-13 to reflect final implementation decisions  
**Date:** 2026-04-12  
**Branch:** `codex/pydantic-benchmark-loop`  
**Author:** Architecture session  

---

## Table of Contents

1. [Design Goals & Constraints](#1-design-goals--constraints)
2. [Diagnosis: What the Current System Over-Engineers](#2-diagnosis-what-the-current-system-over-engineers)
3. [Architecture Overview](#3-architecture-overview)
4. [Data Model](#4-data-model)
5. [Task Center (Shared Context Log)](#5-task-center-shared-context-log)
6. [Plan & Execution](#6-plan--execution)
7. [Submission & PostAgentHook](#7-submission--postagenthook)
8. [Context Sharing & Inheritance](#8-context-sharing--inheritance)
9. [OCC, Code Intelligence & Exploration Cache](#9-occ-code-intelligence--exploration-cache)
10. [Toolkit Assignment](#10-toolkit-assignment)
11. [Task-Agnostic Flows](#11-task-agnostic-flows)
12. [Migration Phases](#12-migration-phases)
13. [Deletion Inventory](#13-deletion-inventory)
14. [PostgreSQL Infrastructure](#14-postgresql-infrastructure)

---

## 1. Design Goals & Constraints

### Hard Constraints

| # | Constraint |
|---|-----------|
| HC-1 | Planner's role is planning only. All agents adopt the ephemeral principle (no state between invocations). |
| HC-2 | Must integrate with existing OCC (Arbiter, Ledger) and Code Intelligence (LSP, SymbolIndex, CI Service). |
| HC-3 | PostAgentHook must always run successfully after agent completion. Submission is guaranteed. |
| HC-4 | `query.py` loop logic stays untouched (minimal 2-line gate in `_has_submission()` only). |

### Design Goals

| # | Goal | How This Plan Achieves It |
|---|------|--------------------------|
| G-1 | Simplest possible design | Replace 7-layer indirection with 2: agent calls tool → executor reads metadata |
| G-2 | High-speed changing codebase | Arbiter + Ledger detect real-time file contention; Task Center notes are append-only, never stale by design |
| G-3 | High parallelism | Append-only Task Center needs no read locks; Arbiter serializes only overlapping file edits, not agent coordination |
| G-4 | Perfect context sharing | Two read filters (deps, parent chain) + Ledger-based file change awareness. No tiered dedup. |
| G-5 | Task agnostic | Same dispatcher, same executor, same Task Center for greenfield, bugfix, feature, rebuild |
| G-6 | High-granularity decomposition | Planner submits fine-grained TaskSpecs. Nested planners create sub-plans. Budget limits prevent explosion. |
| G-7 | Subagent swarm output | Subagent results flow into Task Center as notes. Parent reads them via tag filter. No artifact store indirection. |
| G-8 | PostgreSQL as coordination kernel | One mature database replaces work queue (SKIP LOCKED), event bus (LISTEN/NOTIFY), lock manager (advisory locks), search index (GIN + FTS), and crash recovery (WAL). No custom coordination infrastructure. |

---

## 2. Diagnosis: What the Current System Over-Engineers

```
CURRENT: 7 layers between "planner decides" and "agent does it"

  Planner output
    → Posthook LLM (submit_plan_agent)        ← DELETED: extra LLM call
    → SubmitPlanTool validation                ← SIMPLIFIED: single pass
    → Phase A validation                       ← MERGED into single pass
    → Plan.from_dict deserialization           ← SIMPLIFIED: TaskSpec
    → Phase B validation (dispatcher-time)     ← DELETED: redundant
    → Task creation + Briefing attachment  ← SIMPLIFIED: no Briefing type
    → 3-tier briefing rendering + dedup        ← REPLACED: Task Center

PLAN A: 2 layers

  Planner calls submit_plan() tool
    → Single-pass validation + TaskSpec creation
    → Executor reads Task Center for context
```

### Components Removed vs Kept

| Component | Verdict | Rationale |
|-----------|---------|-----------|
| `Briefing` dataclass | DELETE | Replaced by `Note` in Task Center |
| `DependencyArtifact` dataclass | DELETE | Deps read from Task Center directly |
| `InMemoryArtifactStore` | DELETE | Task Center stores prose, not binary artifacts |
| 3-tier briefing renderer | DELETE | Single `task_center.context_for()` replaces 180 lines |
| `canonical_scope` + coherence tokens (briefing layer) | DELETE | Tags + scope_paths replace canonical scopes |
| `scout_briefings.py` (pressure, freshness, auto-promotion) | DELETE | Was briefing-layer concept, not OCC |
| Atlas service + store + model + freshness | DELETE | Optional `TaskCenterCache` for cross-run reuse |
| 5 posthook agent definitions | DELETE | Submission tools go to work agents directly |
| `agent_posthook.py` (execute_with_posthook) | DELETE | Deterministic `_posthook()` in executor |
| Phase A + Phase B validation split | MERGE | Single validation pass |
| Plan normalization (name inference) | SIMPLIFY | Leaner roster resolution |
| `Arbiter` (per-file OCC) | **KEEP** | Core write coordination |
| `Ledger` (edit audit log) | **KEEP** | Core edit history |
| `scope_packets.py` (contention snapshots) | **DELETE** | Arbiter catches conflicts at edit time; agents don't need pre-flight contention reports |
| `coordination.py` (scope helpers) | **DELETE** | `task.scope_paths` is read directly; no packet building needed |
| `CIToolkit` (LSP, grep, glob) | **KEEP** | Core code intelligence |
| `DaytonaToolkit` (sandbox ops) | **KEEP** | Core file I/O + codeact |
| `SubagentToolkit` (run_subagent) | **KEEP** | Planner exploration |
| `query.py` loop | **KEEP** | 2-line gate in `_has_submission()` only |
| `_has_submission()` mechanism | **KEEP + gate** | `posthook_enabled` flag |
| `Dispatcher` DAG + ready queue | **SIMPLIFY** | Replaced by PG-backed `SKIP LOCKED` queue (Section 14.6). No in-memory DAG state. |
| `Executor` pop-ready loop | **SIMPLIFY** | Remove `execute_with_posthook`, add `_posthook()` |

---

## 3. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│ TeamRun                                                            │
│                                                                    │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │ PGDispatcher │   │ Task Center  │   │ CI Service               │ │
│  │ SKIP LOCKED  │   │ append-only  │   │  ├─ Arbiter (file OCC)   │ │
│  │ + budgets    │   │ note log     │   │  ├─ Ledger (edit audit)  │ │
│  └──────┬───────┘   └──────┬───────┘   │  ├─ SymbolIndex          │ │
│         │                  │           │  └─ LSP client            │ │
│    pop_ready()        read / post      └────────────┬─────────────┘ │
│         │                 │                        │               │
│  ┌──────▼─────────────────▼────────────────────────▼─────────┐    │
│  │ Executor  (N concurrent workers)                           │    │
│  │                                                            │    │
│  │  1. Pop ready Task                                     │    │
│  │  2. Build agent context:                                   │    │
│  │       task.task              ← planner's prose instruction   │    │
│  │       task_center.context_for(task)  ← deps + parent + ledger  │    │
│  │  3. Spawn EphemeralAgent with role-scoped toolkits         │    │
│  │  4. query.py loop runs (UNTOUCHED)                         │    │
│  │       agent uses code_intel, sandbox tools (EXISTING)      │    │
│  │       agent posts notes to Task Center (INTERMEDIATE)      │    │
│  │       agent calls done() or submit_plan() (TERMINAL)       │    │
│  │       _has_submission() gate → exits early if submitted    │    │
│  │  5. _posthook() runs (DETERMINISTIC, always)               │    │
│  │  6. Dispatch result to PGDispatcher                                 │    │
│  └────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
```

### Rationale

| Design choice | Why it suits dynamic/parallel/swarm workloads |
|---------------|-----------------------------------------------|
| Append-only Task Center | Reads never lock. N agents post concurrently. No contention on the knowledge layer. |
| Arbiter for file-level OCC | Contention is serialized only at the file level, not at the context level. Two agents editing different files never block each other. |
| Single executor → runner call | One LLM invocation per task, not two (no posthook agent). Halves LLM cost for every team agent. |
| Arbiter + Ledger at tool level | OCC catches file conflicts at edit time — no pre-flight contention report needed. Agents just work; the Arbiter serializes overlapping writes automatically. |
| Tags on notes | Subagent swarm output tagged by scope. Consumers filter by tag, not by artifact ID. Scales to hundreds of notes without dedup machinery. |

---

## 4. Data Model

### 4.1 Note (replaces Briefing + DependencyArtifact)

```python
@dataclass
class Note:
    """One entry in the Task Center. The only context primitive."""
    id: str                        # uuid
    task_id: str                   # who wrote it
    agent_name: str                # which agent
    content: str                   # plain text, any format, any length
    timestamp: float               # wall clock
    scope_paths: list[str] = field(default_factory=list)  # file/dir scope for filtering
    parent_note_id: str | None = None  # optional threading (subagent → parent)
```

**Why `scope_paths` on Note:** The PostgreSQL schema stores `scope_ltree` on `task_notes`, and tools like `post_note(content, scope_paths?)`, `search_context(scope_paths?)`, and `ExplorationCache.check()` all rely on per-note scope metadata. Without `scope_paths` on the in-memory `Note`, the in-memory TaskCenter cannot reproduce the same scope-filtered reads that PostgreSQL provides, causing same-run context sharing to diverge from search/cache behavior. The field defaults to empty (unscoped notes are visible to all queries).

**Rationale:** One type replaces `Briefing` (100→120 lines), `DependencyArtifact` (122→129 lines), and `InMemoryArtifactStore` (~100 lines). LLMs parse prose better than JSON schemas. Agents post what they know; consumers read what's relevant.

### 4.2 TaskSpec (replaces WorkItemSpec)

```python
@dataclass
class TaskSpec:
    """One item in a plan. What the planner submits."""
    id: str                                          # local reference ID
    task: str                                        # plain text instruction (THE briefing)
    agent: str                                       # agent name or role hint
    deps: list[str] = field(default_factory=list)    # IDs this depends on
    scope_paths: list[str] = field(default_factory=list)  # file/dir hints for OCC + note scoping
    cascade_policy: str = "cancel"                   # "cancel" | "retry_first" | "continue"
```

**Comparison with current `WorkItemSpec`:**

| Field | Current `WorkItemSpec` | New `TaskSpec` | Change |
|-------|----------------------|----------------|--------|
| `agent_name` | str | `agent` (str) | Renamed, accepts role hints |
| `payload` | dict (structured) | `task` (str, prose) | **Schema → prose** |
| `local_id` | str | `id` (str) | Renamed |
| `deps` | list[str] | list[str] | Same |
| `notes` | str | Absorbed into `task` | Merged |
| `timeout_seconds` | float | Removed | Executor default |
| `kind` | TaskKind | Removed | Inferred from agent role |
| `briefings` | list[Briefing] | Removed | `task` field IS the briefing |
| — | — | `scope_paths` (new) | OCC integration |
| — | — | `cascade_policy` (new) | Controls dependent behavior on failure |

**Rationale:** `task` replaces both `payload` and `briefings`. The planner writes one prose description instead of populating a JSON schema + attaching separate Briefing objects. `scope_paths` feeds both the OCC layer (Arbiter) and note scoping (PostgreSQL ltree + GiST index).

### 4.3 Task (simplified)

```python
@dataclass
class Task:
    id: str
    team_run_id: str
    agent_name: str
    status: TaskStatus         # pending | ready | running | done | failed | cancelled
    task: str                      # plain text — what to do
    deps: list[str]                # task IDs
    scope_paths: list[str]         # file/dir paths for OCC
    scope_ltree: list = field(default_factory=list)  # ltree conversion (derived, PG-only)
    cascade_policy: str = "cancel"       # "cancel" | "retry_first" | "continue"
    parent_id: str | None = None
    root_id: str = ""
    depth: int = 0
    pending_dep_count: int = 0            # decremented on dep completion; 0 = ready
    retry_count: int = 0
    max_retries: int = 2
    agent_run_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None
```

**Removed fields:** `kind`, `payload`, `briefings`, `dep_artifacts`, `artifact_ref`, `local_id`, `timeout_seconds`, `replan_source_id`.

**Rationale for each removal:**

| Removed field | Why safe to remove |
|---------------|-------------------|
| `kind` | Inferred from agent role: planner → expandable, everything else → atomic |
| `payload` | Replaced by `task` (prose) + `scope_paths` (OCC) |
| `briefings` | Replaced by `task` field + Task Center notes |
| `dep_artifacts` | Deps read from Task Center at context-build time, not snapshot at promotion time |
| `artifact_ref` | No artifact store. Agent output is a Note in Task Center. |
| `local_id` | Merged into `id` lifecycle (local during plan, resolved by dispatcher) |
| `timeout_seconds` | Use executor-level default. Agent `tool_call_limit` is the real budget. |
| `replan_source_id` | Replanner reads failure context from Task Center, not from a back-pointer |

### 4.4 Plan (simplified)

```python
@dataclass
class Plan:
    tasks: list[TaskSpec]
    rationale: str | None = None
```

### 4.5 ReplanPlan (simplified)

```python
@dataclass
class ReplanPlan:
    add_tasks: list[TaskSpec] = field(default_factory=list)
    cancel_ids: list[str] = field(default_factory=list)
```

### 4.6 BudgetConfig (simplified)

```python
@dataclass
class BudgetConfig:
    max_tasks: int = 50
    max_depth: int = 4
    max_plan_size: int = 50
    max_retries_per_item: int = 2
    max_replans_per_run: int = 5
    max_note_bytes: int = 100_000       # per-note size cap in Task Center
    max_total_note_bytes: int = 5_000_000  # aggregate cap
```

**Removed:** `max_artifact_bytes`, `max_total_artifact_bytes`, `max_briefing_bytes`, `max_shared_briefings`, `max_reviewers_per_plan`, `require_reviewer_for_plan_size`.

**Rationale:** Artifact and briefing limits are irrelevant (no artifacts, no briefings). Reviewer constraints were plan-validation rules, not budget — they move into the single validation pass.

---

## 5. Task Center (Shared Context Log)

### 5.1 Data Structure

```python
class TaskCenter:
    """Append-only shared context log. Replaces ProjectContext,
    InMemoryArtifactStore, and 3-tier briefing system.

    PostgreSQL is the source of truth when a NoteStore is attached.
    All writes go to PG via NoteStore. Reads query PG through the
    same store. An in-memory fallback list is retained for no-PG mode
    (tests, local dev without PostgreSQL)."""

    def __init__(self, goal: str = "", user_request: str = "",
                 note_store: NoteStore | None = None,
                 team_run_id: str = ""):
        self._notes: list[Note] = []          # fallback for no-PG mode
        self._note_store = note_store
        self._team_run_id = team_run_id
        self.goal = goal
        self.user_request = user_request

    async def post(self, note: Note) -> None:
        """Insert a note. When PG-backed, writes to NoteStore (awaited).
        Otherwise appends to the in-memory fallback list."""
        if not self._store_backed():
            self._notes.append(note)
            return
        record = TaskNoteRecord(
            id=uuid.UUID(note.id), team_run_id=self._team_run_id,
            task_id=note.task_id, agent_name=note.agent_name,
            content=note.content,
            scope_paths=list(note.scope_paths) if note.scope_paths else [],
            scope_ltree=[path_to_ltree(p) for p in note.scope_paths] if note.scope_paths else [])
        await self._note_store.insert(record)

    async def read(self, *,
                   authors: list[str] | None = None,
                   scope_paths: list[str] | None = None,
                   since: float | None = None,
                   limit: int | None = None) -> list[Note]:
        """Query notes. PG-backed: delegates to NoteStore.query().
        No-PG: filters the in-memory list."""
        if self._store_backed():
            records = await self._note_store.query(
                self._team_run_id, task_ids=authors,
                scope_paths=scope_paths, since=since, limit=limit)
            return [self._note_from_record(r) for r in records]
        # In-memory fallback (no PG)
        results = list(self._notes)
        if authors:
            results = [n for n in results if n.task_id in set(authors)]
        if scope_paths:
            results = [n for n in results if self._matches_scope(n.scope_paths, scope_paths)]
        if since is not None:
            results = [n for n in results if n.timestamp >= since]
        if limit is not None and limit > 0:
            results = results[-limit:]
        return results

    async def context_for(self, task: Task, *,
                          file_change_store: Any | None = None,
                          task_lookup: Callable[[str], Awaitable['Task | None']] | None = None,
                          max_context_bytes: int = 200_000) -> str:
        """Build context string for a task. Fixed priority order:
        task (never trimmed) → deps → file changes (FileChangeStore) → parent.
        Implementation in Section 8.2."""
        ...
```

**Implementation note — FileChangeStore replaces Arbiter parameter:** The original design specified an `Arbiter` object for file change awareness. The implementation passes `file_change_store` instead — the durable `FileChangeStore` (sync SQLAlchemy, backed by the `file_changes` table) provides cross-process visibility and crash recovery, while the Arbiter's in-memory ring buffer is limited to the current process. The `task_lookup` callable replaces the raw `pool` parameter for parent chain walks — cleaner than inline SQL.

**Multi-process consistency:** PostgreSQL is the single source of truth. Notes written by process A are immediately visible to process B — no hydration step, no sync protocol. `context_for()` and `search_context` both read from the same `task_notes` table, so they always agree on note visibility. The append-only table and `BRIN` index on `created_at` make reads cheap even under high write concurrency.

### 5.2 Why Append-Only

| Property | Benefit for dynamic/parallel workloads |
|----------|---------------------------------------|
| No locks | list.append() is GIL-atomic. No read locks on append-only list. Zero contention on any path. |
| No mutation | A note posted at t=1 is still valid at t=100. No invalidation tracking needed. |
| No dedup | LLMs naturally handle overlapping prose. The 3-tier dedup machinery (canonical scopes, seen_scopes, seen_refs) is deleted. |
| Monotonic timestamps | `since` filter gives "what's new since I last looked" for free. |
| Simple checkpoint | `snapshot()` = `list(self._notes)`. No artifact store serialization. |
| Monotonic knowledge | Agents that start later see strictly more context. Knowledge never decreases — a formal property that append-only + immutable notes guarantee by construction. |

### 5.3 Task Center vs Current System — Size Comparison

| Component | Current (lines) | Task Center (lines) |
|-----------|----------------|-------------------|
| `Briefing` + `DependencyArtifact` | ~60 | `Note` (~15) |
| `InMemoryArtifactStore` | ~100 | `TaskCenter.post/read` (~40) |
| `briefings.py` (3-tier renderer) | ~180 | `context_for()` (~50) |
| `scout_briefings.py` (pressure/freshness) | ~300 | Deleted (0) |
| `canonicalize.py` | ~50 | Tag matching (~5) |
| `project.py` (ProjectContext) | ~100 | `TaskCenter.goal/user_request` (~5) |
| `share_briefing.py` tool | ~150 | `PostNoteTool` (~30) |
| `inspect_inherited_context.py` tool | ~80 | `ReadNotesTool` (~20) |
| Atlas service + store + model + freshness + identity | ~400 | Optional `TaskCenterCache` (~50) |
| **Total** | **~1,420** | **~215** |

---

## 6. Plan & Execution

### 6.1 Planner Flow

```
┌─────────────────────────────────────────────────────┐
│ Planner (ephemeral, planning-only)                   │
│                                                      │
│ Toolkits:                                            │
│   code_intelligence (READ-ONLY)                      │
│   subagent (spawn explorer for pre-plan recon)       │
│   task_center_read (read notes)                      │
│   submission (submit_plan ONLY)                      │
│                                                      │
│ CANNOT: write files, run shell, call done()          │
│                                                      │
│ Flow:                                                │
│   1. Read Task Center for existing context           │
│   2. Optionally spawn explorer subagents              │
│   3. Read explorer findings from Task Center         │
│   4. Decompose work into TaskSpecs                   │
│   5. Call submit_plan(tasks=[...], rationale="...")   │
│      → tool writes to metadata["submitted_output"]   │
│      → _has_submission() gate → loop exits           │
└─────────────────────────────────────────────────────┘
```

**Rationale (HC-1):** Planner has no `sandbox_operations` toolkit. It literally cannot `write_file()`, `edit_file()`, `codeact()`, or `shell()`. The only terminal tool in its toolkit is `submit_plan()`. If it doesn't call it, the posthook fails the task.

### 6.2 Single-Pass Plan Validation

Merges current Phase A + Phase B into one pass, run inside `SubmitPlanTool.execute()`:

```
SubmitPlanTool.execute(arguments, context)
  │
  ├── 1. Structural checks
  │     - Plan non-empty (unless sub-planner)
  │     - Items ≤ max_plan_size
  │     - ID uniqueness
  │     - Dep refs valid (within plan or known external IDs)
  │     - No cycles (iterative DFS)
  │
  ├── 2. Agent resolution
  │     - Exact name match → use it
  │     - Role hint → resolve via roster
  │     - Unknown → error
  │
  ├── 3. Kind inference
  │     - Agent role == "planner" → expandable
  │     - All others → atomic
  │
  ├── 4. Budget check
  │     - tasks_used + len(tasks) ≤ max_tasks
  │     - max_depth not exceeded
  │
  ├── 5. Note size check
  │     - Each task.task ≤ max_note_bytes
  │
  └── 6. Write to metadata
        context.metadata["submitted_output"] = Plan(tasks, rationale)
```

**Rationale:** One pass instead of two. Phase B was re-running Phase A checks with graph context — but `known_external_dep_ids` is already available in metadata (the executor pre-populates it). No reason to split.

### 6.3 Executor Flow

```
Executor.run_forever()
  │
  WHILE not cancelled:
  │
  ├── task_id = dispatcher.pop_ready()
  │
  ├── _run_one(task_id):
  │   │
  │   ├── 1. (task already RUNNING — pop_ready set status atomically)
  │   │
  │   ├── 2. Build context
  │   │     query_ctx.tool_metadata["posthook_enabled"] = True
  │   │     query_ctx.user_message = task_center.context_for(task)
  │   │
  │   ├── 3. await self.runner(defn, query_ctx)
  │   │     # query.py loop runs. UNTOUCHED.
  │   │     # Agent works, posts notes, calls terminal tool.
  │   │
  │   ├── 4. _posthook(query_ctx, defn)         ← ALWAYS RUNS
  │   │     │
  │   │     ├── metadata["submitted_output"] exists?
  │   │     │     YES → return it
  │   │     │     NO  → role-aware fallback
  │   │     │           planner  → FAIL
  │   │     │           worker   → auto-extract summary
  │   │     │
  │   │     └── Returns: Plan | Summary | Retry | Replan | Failure
  │   │
  │   └── 5. _dispatch(task_id, result)
  │         │
  │         ├── Plan     → validate + insert TaskSpecs into DAG
  │         ├── Summary  → mark DONE, post summary note to Task Center
  │         ├── Retry    → reset PENDING, increment retry_count
  │         ├── Replan   → fail item, spawn replanner
  │         └── Failure  → mark FAILED, cascade-cancel dependents
  │
  └── Loop
```

### 6.4 Retry & Replan

**Retry** — agent decides, deterministic routing:

```
Developer encounters transient failure
  → calls request_retry(reason="sandbox timeout")
  → tool writes RetryRequest to metadata["submitted_output"]
  → _has_submission() → loop exits
  → _posthook reads RetryRequest
  → dispatcher: if retry_count < max_retries → reset PENDING
                 else → FAIL + cascade
  → reason posted to Task Center (available on retry)
```

**Replan** — agent decides, replanner decomposes:

```
Developer realizes task is mis-scoped
  → calls request_replan(reason="auth is 3 services, need separate tasks")
  → tool writes ReplanRequest to metadata["submitted_output"]
  → _posthook reads ReplanRequest
  → dispatcher: fail task, spawn replanner at same depth/parent
  → Replanner reads Task Center:
      - failed agent's reason + suggestion
      - other siblings' results (if any)
  → Replanner calls submit_replan(add_tasks=[...], cancel_ids=[...])
  → dispatcher: atomically cancel old + insert new + promote ready
```

**Rationale:** The work agent has the most context about what went wrong. A separate decision posthook LLM (current `decision_submit_retry`) re-processes the same information with less context. Giving the decision to the work agent is both simpler and more accurate.

### 6.5 Cascade Policy

When a task fails, its dependents' behavior is controlled by `cascade_policy` on the **dependent** TaskSpec:

| Policy | Behavior | Use case |
|--------|----------|----------|
| `cancel` (default) | Cancel all dependents immediately | Strict dependency chains where downstream is meaningless without upstream |
| `retry_first` | Retry the failed task up to `max_retries` before cascading | Transient failures (network, sandbox timeout) where retry is cheap |
| `continue` | Mark dep as failed but let dependent start anyway, with failure context injected | Best-effort tasks where partial results are useful (e.g., tests can run even if one module failed to build) |

```python
# In _dispatch():
if result is Failure:
    dependents = await dispatcher.get_dependents(task_id, run_id)
    for dep in dependents:
        match dep.cascade_policy:
            case "cancel":
                await dispatcher.cancel(dep.id, run_id)
            case "retry_first":
                if task.retry_count < task.max_retries:
                    await dispatcher.retry(task_id, run_id)
                    return  # don't cascade yet
                await dispatcher.cancel(dep.id, run_id)
            case "continue":
                # Inject failure context, let dependent proceed
                task_center.post(Note(
                    task_id=dep.id, agent_name="system",
                    content=f"Warning: dependency {task_id} failed: "
                            f"{task.failure_reason}. Proceed with caution."))
```

### 6.6 External-Change-Triggered Replanning

Agent-initiated `request_replan()` handles cases where the agent discovers its task is mis-scoped. But in a fast-changing codebase, external changes (another team's commit, CI pipeline update) can invalidate the planner's decomposition before tasks execute.

**Detection:** The executor checks for scope-level changes before starting each task:

```python
async def _check_scope_validity(self, task: Task, run_id: str) -> bool:
    """Check if files in task's scope changed externally since plan creation.

    'External' means the edit was NOT made by any agent_run_id belonging
    to this team run. file_changes.agent_id stores the agent_run_id
    (the unique session identity of the agent that made the edit), so
    we compare it to tasks.agent_run_id — not tasks.id (which is the
    task identity, a different namespace)."""
    fc_store: FileChangeStore = self._file_change_store
    external_changes = await fc_store.external_changes_in_scope(
        run_id, [path_to_ltree(p) for p in task.scope_paths],
        task.created_at)
    return len(external_changes) == 0
```

**Response:** If scope is invalidated, the executor injects a warning note into the task's context rather than auto-replanning (which could cause cascading replans). The agent sees the warning and can call `request_replan()` if the changes are incompatible:

```
## Warning: scope changes detected since plan creation
The following files in your scope were modified externally:
- src/auth/session.py (by external commit, 45s ago)
Review these changes before proceeding. Call request_replan()
if your task is no longer valid.
```

This keeps the agent in control of the replan decision while ensuring it has the information to make it.

---

## 7. Submission & PostAgentHook

### 7.1 Submission Tools (Terminal)

Each role gets exactly the terminal tools it needs:

| Tool | Who calls it | What it writes to metadata |
|------|-------------|--------------------------|
| `submit_plan(tasks, rationale)` | planner, replanner | `Plan` |
| `done(summary)` | developer, reviewer | `SubmittedSummary` |
| `request_retry(reason)` | developer, reviewer | `RetryRequest` |
| `request_replan(reason, suggestion?)` | developer, reviewer | `ReplanRequest` |
| `submit_replan(add_tasks, cancel_ids)` | replanner | `ReplanPlan` |

All submission tools:
1. Validate arguments (structural, not LLM)
2. Write to `context.metadata["submitted_output"]`
3. Post summary note to Task Center (for siblings to read)
4. Return `ToolResult` (the loop continues to the `_has_submission()` check, which exits)

### 7.2 `_has_submission()` Gate

```python
# query.py — the ONLY change (2 lines)
def _has_submission(metadata: ExecutionMetadata | None) -> bool:
    if metadata is None:
        return False
    if not metadata.extras.get("posthook_enabled"):    # ← NEW: gate
        return False                                     # ← NEW: gate
    return any(
        key.startswith("submitted_") and value is not None
        for key, value in metadata.extras.items()
    )
```

**Behavior by agent type:**

| Agent type | `posthook_enabled` | `_has_submission()` behavior |
|------------|-------------------|------------------------------|
| Standalone (no team) | Not set | Always returns `False`. Loop never exits for this reason. |
| Team agent (work) | `True` | Checks for `submitted_*` keys. Exits early when agent submits. |
| Subagent (via run_subagent) | `True` | Same as team agent. |

### 7.3 PostAgentHook — Deterministic Guarantee

```
                  await self.runner(defn, ctx)
                             │
                     runner returns (any reason)
                             │
                             ▼
                  ┌─────────────────────┐
                  │  _posthook(ctx, defn) │
                  │  ALWAYS RUNS          │
                  │  DETERMINISTIC         │
                  │  NO LLM               │
                  │                       │
                  │  metadata[            │
                  │   "submitted_output"] │
                  │       │               │
                  │    ┌──▼──┐            │
                  │    │found│            │
                  │    └┬───┬┘            │
                  │    YES  NO            │
                  │     │    │            │
                  │     ▼    ▼            │
                  │  return ┌──────────┐  │
                  │    it   │ defn.role │  │
                  │         └──┬────┬──┘  │
                  │        planner worker │
                  │            │     │    │
                  │            ▼     ▼    │
                  │         FAIL  extract │
                  │               last    │
                  │               message │
                  │               as      │
                  │               Summary │
                  └─────────────────────┘
                             │
                    Submission (guaranteed)
                             │
                             ▼
                    _dispatch(task_id, result)
```

**Why runner exit reason doesn't matter:**

| Loop ended because | `submitted_output` in metadata? | `_posthook` result |
|----|----|----|
| Agent called `done()` → `_has_submission()` exit | YES | Returns the submission |
| Agent called `submit_plan()` → same | YES | Returns the plan |
| Agent called `request_retry()` → same | YES | Returns retry request |
| Model returned stop (no tool calls) | NO | Role-aware fallback |
| `tool_call_limit` exhausted | NO | Role-aware fallback |
| Runner exception | N/A | Executor catches, calls `dispatcher.fail()` |

**Rationale:** The posthook is a function call in the executor, not a conditional event handler. It is structurally guaranteed to run. It produces a result in every case. Current system has TWO points of LLM failure (work agent + posthook agent). This design has ONE (work agent) with a deterministic backstop.

### 7.4 What Gets Deleted

```
DELETED:
  hooks/agent_posthook.py                    (232 lines)
    - execute_with_posthook()
    - resolve_posthook_definition()
    - PosthookConfig
    - _assert_serializer_has_no_skills()
    - stamp_posthook_metadata_key()
    - read_posthook_output()

  team/builtins/agents/submit_plan_agent.md
  team/builtins/agents/submit_summary_agent.md
  team/builtins/agents/submit_replan_agent.md
  team/builtins/agents/decision_submit_retry.md
  team/builtins/agents/decision_submit_replan.md

  tools/posthook/toolkits.py                 (posthook toolkit classes)

  PosthookConfig field on AgentDefinition

REWRITTEN:
  team/runtime/executor.py._run_one()        (direct runner + _posthook)
  team/runtime/executor.py._posthook()       (~25 lines, deterministic)

MOVED (not deleted, relocated):
  tools/posthook/submit_plan.py    → tools/submission/submit_plan.py
  tools/posthook/submit_summary.py → tools/submission/done.py
  tools/posthook/types.py          → tools/submission/types.py
  (logic preserved, host agent changes from posthook to work agent)
```

---

## 8. Context Sharing & Inheritance

### 8.1 Design Principle: Two Filters + Ledger

Agents need three kinds of context. Each comes from the right source:

| Need | Source | Mechanism |
|------|--------|-----------|
| What upstream produced | Task Center | Dep filter: notes from dependency tasks |
| What changed in my files | Arbiter | `arbiter.changes_since()`: actual file edits in scope (Arbiter owns the edit ring buffer) |
| Why this task exists | Task Center | Parent chain: walk parent_id up to root |

No sibling tag filtering. No dedup machinery. No canonical scopes.

**Rationale:** Sibling awareness via notes is advisory and degrades under parallelism (siblings post after context is built). File-level awareness via Ledger is ground truth and always current. Agents need to know what **changed in the codebase**, not what siblings **wrote about** the codebase.

### 8.2 Context Rendering

```python
async def context_for(self, task: Task, *,
                      file_change_store: Any | None = None,
                      task_lookup: Callable[[str], Awaitable['Task | None']] | None = None,
                      max_context_bytes: int = 200_000) -> str:
    """Build context string for a task. Fixed priority order:
    task (never trimmed) → deps → file changes (FileChangeStore) → parent.

    Uses FileChangeStore.changes_since() for file change awareness (durable,
    cross-process visible via PG). task_lookup resolves parent chain via
    callable instead of raw SQL pool."""
    budget = max_context_bytes
    sections = []

    # Priority 1: The task itself (never trimmed)
    task_section = f"## Your task\n{task.task}"
    if task.scope_paths:
        task_section += f"\n\nScope: {', '.join(task.scope_paths)}"
    sections.append(task_section)
    budget -= len(task_section.encode())

    # Priority 2: Dep notes (structural -- what upstream produced)
    # Direct deps only -- not transitive. If A → B → C, C sees B's
    # notes but not A's. Transitive inclusion would explode context
    # size and duplicate information (B already incorporated A's output).
    # Deduplicate to latest note per dep (many notes from one dep
    # would bloat context; we only care about the most recent summary).
    if task.deps and budget > 0:
        dep_notes = await self.read(authors=task.deps)
        if dep_notes:
            by_dep: dict[str, Note] = {}
            for n in dep_notes:
                by_dep[n.task_id] = n
            dep_notes = list(by_dep.values())
            dep_section = self._render_notes("Context from dependencies", dep_notes)
            dep_bytes = len(dep_section.encode())
            if dep_bytes <= budget:
                sections.append(dep_section)
                budget -= dep_bytes
            else:
                sections.append(self._truncate_section(
                    "Context from dependencies", dep_notes, budget))
                budget = 0

    # Priority 3: Recent file changes in scope (from FileChangeStore -- ground truth)
    if file_change_store is not None and budget > 0 and task.scope_paths:
        created_ts = task.created_at.timestamp() if task.created_at else 0.0
        changes = file_change_store.changes_since(created_ts)
        scoped = [e for e in changes
                  if any(e.file_path.startswith(p.rstrip('/'))
                         for p in task.scope_paths)]
        if scoped:
            lines = [f"- {e.file_path} ({e.edit_type} by {e.agent_id}, "
                     f"{int(time.time() - e.timestamp)}s ago)"
                     for e in scoped]
            change_section = "## Recent changes in your scope\n" + "\n".join(lines)
            change_bytes = len(change_section.encode())
            if change_bytes <= budget:
                sections.append(change_section)
                budget -= change_bytes

    # Priority 4: Parent chain (strategic -- why this task exists)
    if task.parent_id and budget > 0:
        parent_ids = await self._parent_chain_ids(task, task_lookup=task_lookup)
        parent_notes = await self.read(authors=parent_ids)
        if parent_notes:
            parent_section = self._render_notes("Parent context", parent_notes)
            parent_bytes = len(parent_section.encode())
            if parent_bytes <= budget:
                sections.append(parent_section)
            else:
                sections.append(self._truncate_section(
                    "Parent context", parent_notes, budget))

    return "\n\n".join(sections)
```

#### Design note: dep note dedup (latest-per-dep)

A progressive disclosure layer (`_dep_note_index` returning lightweight `(id, agent, summary, timestamp)` tuples, then selectively loading full content) was considered but rejected as marginal. The current design already handles the expensive case:

- **Direct deps only** — `_dep_notes` fetches immediate dependencies, not transitive. The note set is bounded by plan fan-out, which is typically small.
- **Byte-budget truncation** — `_render_notes_truncated` degrades gracefully when dep notes exceed the budget.
- **In-memory reads** — no I/O cost to filter; a two-phase fetch saves nothing in the single-process case.

The one scenario that *can* bloat is a single dependency posting many incremental notes (e.g., a long-running explorer logging progress). The fix is simpler than a full index layer — dedup to **latest note per dep task**:

```python
dep_notes = await self._dep_notes(task, pool)
# Keep only the latest note per dep (usually the completion summary)
seen: dict[str, Note] = {}
for n in dep_notes:
    seen[n.task_id] = n  # last wins (notes are append-ordered)
dep_notes = list(seen.values())
```

This targets the actual problem (many notes from one dep) without adding an abstraction layer. A two-phase index would become worthwhile only if `TaskCenter` moves to a persistent store where fetching full content has real I/O cost.

### 8.2.1 Automatic Context Freshness Warning

`context_for()` builds a snapshot at task start that can go stale during long-running tasks. LISTEN/NOTIFY (Section 14.7) handles file-level changes, but dep notes or new sibling completions are invisible after context is built. To close this gap, the executor injects a freshness check tool that agents can call before committing large changes:

```python
class ContextFreshnessCheckTool(BaseTool):
    """Check if context has changed since task started. Available to all roles.
    Agents SHOULD call this before committing multi-file changes."""
    name = "context_changed_since"

    async def execute(self, arguments, context):
        task = context.metadata["current_task"]
        run_id = context.metadata["team_run_id"]
        since = task.started_at.timestamp()
        note_store: NoteStore = context.metadata["note_store"]
        fc_store: FileChangeStore = context.metadata["file_change_store"]
        task_store: TaskStore = context.metadata["task_store"]

        # Check for new dep notes since context was built
        new_dep_notes = await note_store.count_since(
            run_id, task_ids=task.deps, since=since)

        # Check for new sibling completions
        new_siblings = await task_store.count_done_siblings(
            run_id, parent_id=task.parent_id,
            exclude_id=task.id, since=since)

        # Check scope changes (supplements LISTEN/NOTIFY)
        scope_changes = await fc_store.count_changes_in_scope(
            run_id, [path_to_ltree(p) for p in task.scope_paths],
            exclude_agent=task.agent_run_id, since=since)

        stale = new_dep_notes > 0 or new_siblings > 0 or scope_changes > 0
        return {
            "stale": stale,
            "new_dep_notes": new_dep_notes,
            "new_sibling_completions": new_siblings,
            "scope_changes_by_others": scope_changes,
            "suggestion": "Re-read affected files and check Task Center "
                          "for new context before committing." if stale else None,
        }
```

This tool is registered in the `search` toolkit (available to all roles). Agent prompts include: "Before committing changes to multiple files, call `context_changed_since()` to check if your context is still current."

### 8.3 Context Priority & Overflow

Fixed priority order (hardcoded, not configurable — add configurability only if evidence shows different orderings improve outcomes).

| Priority | Section | Source | Trim policy |
|----------|---------|--------|-------------|
| 1 (never trimmed) | Your task | `task.task` + `task.scope_paths` | Never |
| 2 | Dep notes | Task Center (dep filter) | Keep most recent, trim oldest |
| 3 | File changes | Ledger (scope filter) | Keep most recent N changes |
| 4 | Parent chain | Task Center (parent walk) | Keep root rationale, trim middle |

**Rationale for priority order:**
- Agent must know WHAT to do (task) -- always
- Agent must know WHAT upstream produced (deps) -- structural dependency
- Agent must know WHAT changed in its files (ledger) -- collision avoidance
- Agent should know WHY it exists (parent) -- strategic, nice-to-have

### 8.4 Detailed Flow: Dependency Context (Parent to Child)

```
Root planner submits:
  Task P: "Implement user API"  (planner, expandable)
  P posts rationale note: "Decomposing into schema + endpoints + tests"

Sub-planner P runs, submits:
  Task A: "Create user schema"    parent=P
  Task B: "Implement endpoints"   parent=P, deps=[A]

When A starts, context_for(A) builds:

  ## Your task
  Create user schema
  Scope: src/db/

  ## Parent context
  ### planner (P)
  Decomposing into schema + endpoints + tests

When A finishes and B starts, context_for(B) builds:

  ## Your task
  Implement endpoints
  Scope: src/api/

  ## Context from dependencies
  ### developer (A)
  Created migration: users table with id, email, name, created_at.
  File: src/db/migrations/001_users.py

  ## Recent changes in your scope
  (none -- A edited src/db/, B's scope is src/api/)

  ## Parent context
  ### planner (P)
  Decomposing into schema + endpoints + tests
```

### 8.5 Detailed Flow: Parallel Agents with Ledger Awareness

```
Planner submits:
  Task A: "Fix auth timeout"      scope_paths=["src/auth"]  deps=[]
  Task B: "Fix auth retry logic"  scope_paths=["src/auth"]  deps=[]
  Task C: "Verify auth module"    scope_paths=["src/auth"]  deps=[A, B]

Timeline:
  t=1  A starts, B starts (parallel, no deps)
       Both see empty Ledger (no prior changes)
  t=2  A edits session.py -> Ledger.record("src/auth/session.py", agent_A)
  t=3  B calls scope_changed_since(["src/auth"], since=t1)
       -> Returns: session.py edited by agent_A 1s ago
       B now knows A touched session.py, avoids conflicting edit
  t=4  A finishes (done), B finishes (done)
  t=5  C starts (both deps satisfied):
       context_for(C) includes:
         Dep notes: A's summary + B's summary (from Task Center)
         Ledger: session.py edited by A, middleware.py edited by B
         C has full picture to verify both changes
```

**Key difference from old design:** B discovers A's work through the **Ledger** (actual file changes), not through sibling notes (agent prose). The Ledger is ground truth -- it records what actually happened to files, not what an agent chose to write about.

### 8.6 Detailed Flow: Subagent Swarm Output

```
Planner spawns 3 explorer subagents via run_subagent():

  Explorer-1: "Read src/auth/"  -> posts Note(scope_paths=["src/auth"], content="...")
  Explorer-2: "Read src/api/"   -> posts Note(scope_paths=["src/api"], content="...")
  Explorer-3: "Read src/db/"    -> posts Note(scope_paths=["src/db"], content="...")

All three run concurrently. All post to Task Center.

Planner reads Task Center after explorers complete:
  task_center.read(scope_paths=["src/auth", "src/api", "src/db"])
  -> gets all three explorers' findings
  -> decomposes work based on full picture

Later, Developer assigned to "Fix auth timeout" (scope_paths=["src/auth"]):
  context_for(developer_task) includes:
    Dep notes: explorer's note about src/auth/ (if explorer is a dep)
    Or: planner included explorer findings in the task description
```

**Rationale:** Subagent output flows through the same Task Center as everything else. No special artifact store, no structured contracts. Explorers post prose. The planner reads and synthesizes. Consumers get context through deps, not broadcast.

### 8.7 Comparison with Current 3-Tier System

| Aspect | Current (3-tier briefings) | Plan A (Task Center + Ledger) |
|--------|--------------------------|-------------------------------|
| Parent to child | `task.briefings` (Tier 3, explicit) | Parent chain filter on Task Center |
| Dep to consumer | `task.dep_artifacts` (Tier 2, snapshot at PENDING->READY) | Dep filter on Task Center (read at context-build time) |
| Sibling awareness | `project_context.shared_briefings` (Tier 1, canonical_scope) | Ledger: actual file changes in scope |
| Dedup mechanism | 3-tier priority + `seen_scopes` + `seen_refs` + `_claim()` | Dedupe by `note.id` (trivial) |
| Freshness | `scout_artifact_invalidated()`, coherence tokens, pressure scoring | Notes are immutable. Ledger is ground truth. |
| Overflow | `max_briefing_bytes` per-item truncation | Priority-based budget with per-section trim |
| Code size | ~1,420 lines across 9 files | ~250 lines in 1 file |

### 8.8 Dynamic Environment Awareness (Consolidated View)

A fast-changing codebase means the world can change while an agent is working. Four mechanisms handle this at four timescales:

| Timescale | Mechanism | How it works |
|-----------|-----------|-------------|
| **Pre-start** | `_check_scope_validity()` (Section 6.6) | Executor checks if files in the task's scope changed externally since the plan was created. If so, injects a warning note — agent decides whether to `request_replan()`. |
| **At start** | `context_for()` (Section 8.2) | Builds a snapshot including recent file changes from `arbiter.changes_since(task.created_at)`. Agent starts with full picture of what changed since it was planned. |
| **Mid-task (pull)** | `context_changed_since()` tool (Section 8.2.1) | Agent calls this before committing large changes. Returns new dep notes, sibling completions, and scope file changes since task started. |
| **Mid-task (push)** | `LISTEN/NOTIFY` (Section 14.7, deferred) | Real-time `SystemReminderBlock` injected into agent conversation when another agent edits files in its scope. Debounced to 5-second batches. |
| **At edit time** | Arbiter OCC (Section 9.3) | Hard backstop. Content-hash token validation catches stale edits with zero false negatives. Agent gets error, re-reads file, retries. |

**Concrete example — file edited out from under an agent:**

```
t=0  Agent A reads src/auth/session.py (hash=abc)
     Arbiter issues token(session.py, hash=abc, agent_A)
t=1  Agent B edits session.py → hash changes to def
     Ledger records the edit
t=2  Agent A tries to edit session.py
     Arbiter.validate_token(token, session.py, hash=def) → FAIL (abc ≠ def)
     Agent A gets error: "File changed since you read it"
     Agent A re-reads session.py (sees B's changes)
     Agent A re-issues token with new hash, edits successfully
```

No agent coordination needed. The Arbiter serializes at the file level, and token validation ensures no edit is ever applied against stale content.

---

## 9. OCC, Code Intelligence & Exploration Cache

### 9.1 Design Principle: Two Layers, Not Three

The current system has three coordination layers: briefings (knowledge), scope packets (contention awareness), and Arbiter (file locks). But agents are ephemeral workers — they cannot wait, queue, or reschedule based on a contention report. The dispatcher handles sequencing via deps. The Arbiter catches actual conflicts at edit time. Scope packets are a pre-flight contention report for an agent that cannot act on it. **Delete them.**

```
              +-------------------------+
              | KNOWLEDGE LAYER          |
              | (Task Center)            |
              |                          |
              | "What do agents know?"   |
              |                          |
              | Explorer found X.        |
              | Developer fixed Y.       |
              | Validator failed Z.      |
              +------------+-------------+
                           |
                answers "what to do"
                           |
              +------------v-------------+
              | EXECUTION LAYER          |
              | (Arbiter + Ledger)       |
              |                          |
              | "Serialize file edits"   |
              |                          |
              | Token -> Lock -> Edit    |
              | -> Validate -> Record    |
              +--------------------------+
```

**Rationale:** Two layers, two concerns, zero coupling:
- Task Center handles knowledge divergence (agents learn at different rates)
- Arbiter handles conflict prevention (serializes overlapping file writes)
- No middle layer needed. Deps handle sequencing. Arbiter handles collisions.

### 9.2 What Stays, What Goes

```
code_intelligence/
  editing/
    arbiter.py           UNTOUCHED  per-file OCC tokens + locks
    ledger.py            UNTOUCHED  edit audit ring buffer
    patcher.py           UNTOUCHED  edit application
    merge.py             UNTOUCHED  conflict resolution
    time_machine.py      UNTOUCHED
  routing/
    scope_packets.py     DELETE     agents can't act on contention reports
    service.py           UNTOUCHED  CI service
    query_router.py      UNTOUCHED
    backend_protocol.py  UNTOUCHED
  analysis/
    symbol_index.py      UNTOUCHED
    tree_cache.py        UNTOUCHED
  lsp/
    client.py            UNTOUCHED
  atlas/                 DELETE     replaced by ExplorationMemory
    service.py
    store.py
    model.py
    persistence.py
    freshness.py
    identity.py

tools/daytona_toolkit/
    coordination.py      DELETE     task.scope_paths read directly where needed
```

### 9.3 OCC During Agent Execution

The Arbiter and Ledger operate inside tool execution, not at the context level:

```
Agent calls edit_file("src/auth/session.py", ...)
  |
  +-- DaytonaToolkit.edit_tool:
  |     1. Arbiter.issue_token(session.py, hash, agent_id)
  |     2. Arbiter.acquire_file_lock(session.py)
  |     3. Apply edit
  |     4. Arbiter.validate_token(token_id, session.py, new_hash)
  |     5. Arbiter.release_file_lock(session.py)
  |     6. Ledger.record(session.py, agent_id, "edit")
  |     7. Arbiter.record_edit(session.py, agent_id)
  |
  +-- If another agent edited session.py since token was issued:
        -> validate_token fails -> content hash mismatch
        -> Agent gets error -> can read fresh content and retry
```

This is completely independent of the Task Center. The Arbiter is per-file, real-time, and operates inside the tool call. No change needed.

**File-level write serialization with region-level OCC:** The file-level `threading.Lock` (`acquire_file_lock`) serializes the physical write I/O — only one agent writes at a time. However, the OCC layer is smarter than pure file-level: when a token's content hash mismatches (another agent edited the file since the token was issued), `_resolve_pending_write()` in `service.py` performs **line-range-based merge** via `detect_edit_window()` + `merge_non_overlapping_edit()`. If the specific target lines are unchanged in the current file, the edit succeeds despite the hash mismatch. Only overlapping-range edits are rejected.

| Scenario | Result |
|----------|--------|
| Agent A edits lines 1-10, Agent B edits lines 50-60 | **Both succeed** — non-overlapping merge |
| Agent A edits lines 1-10, Agent B edits lines 5-15 | B rejected — overlapping range, must re-read and retry |
| No other agent touched the file | Token hash matches, edit succeeds immediately |

This gives effectively region-level OCC with file-level write serialization — maximizing parallel throughput while keeping the lock implementation simple. The planner further mitigates contention by assigning disjoint `scope_paths` (Section 9.7's `QueryEditHistoryTool` predicts hotspots at decomposition time).

**Rationale for no scope packets in agent prompts:** The Arbiter catches conflicts at edit time with zero false negatives. Deps already prevent agents from starting before their predecessors finish. Showing a contention report to an ephemeral agent that cannot reschedule itself adds prompt noise without actionable benefit. If we later find agents need contention awareness, it can be added as a lazy tool (`check_contention(paths)`) rather than eager prompt injection.

### 9.4 Explorer: Prose-Based Code Understanding

**What explorer solves:** N developers need to understand the same code area. Without explorer, each reads the same files independently — Nx the tool calls, Nx the LLM input tokens. One explorer serves all N consumers via Task Center.

```
WITHOUT explorer:                     WITH explorer:

Dev A: read_file x 15 --+            Explorer: read_file x 15 -> Note
Dev B: read_file x 15   +-- 45       Dev A: read note    --+
Dev C: read_file x 15 --+            Dev B: read note      +-- 15 + 3 reads
                                      Dev C: read note    --+
```

**Plan A makes explorer MORE useful than current.** Current explorer must produce `{target_paths, files, entry_points, scope_coverage, gaps}`. In Plan A, explorer writes prose — it can note things that don't fit a JSON schema:

```
Current explorer output (constrained by contract):
  {"files": [...], "entry_points": [...], "scope_coverage": 0.8}

Plan A explorer output (free prose):
  "The auth module has 3 files. session.py has a complex state machine
   (lines 40-120) -- careful, there's a known race condition noted in a
   TODO at line 87. middleware.py is straightforward except the bare
   except at line 87 which swallows TimeoutError. tokens.py is clean."
```

The second is more useful to a developer. LLMs consume prose better than JSON schemas.

### 9.5 Exploration Cache (Replaces Atlas)

**What Atlas solves:** Don't re-explore unchanged code across runs. The current Atlas achieves this with ~400 lines across 6 files. The actual mechanism is a content-addressed cache. Everything else is overhead.

**Exploration Cache** — the entire value of Atlas in ~60 lines:

```python
class ExplorationMemory:
    """Cross-run note cache. Content-addressed. Replaces Atlas."""

    async def check(self, scope_paths: list[str], sandbox) -> list[Note] | None:
        """Return cached notes if files haven't changed. None = re-explore."""
        content_hash = await self._hash_files(scope_paths, sandbox)
        key = self._cache_key(scope_paths, content_hash)
        cached = self._store.get(key)
        if cached is None:
            return None
        return [Note(**n) for n in cached]

    async def save(self, scope_paths: list[str], notes: list[Note], sandbox):
        """Cache notes after explorer completes."""
        content_hash = await self._hash_files(scope_paths, sandbox)
        key = self._cache_key(scope_paths, content_hash)
        self._store.set(key, [asdict(n) for n in notes])

    def _cache_key(self, scope_paths: list[str], content_hash: str) -> str:
        scope_str = "|".join(sorted(scope_paths))
        return hashlib.sha256(
            f"{scope_str}:{content_hash}".encode()
        ).hexdigest()[:24]
```

Content hash IS the freshness check. No subsystem model, no auto-promotion, no coherence tokens, no complex persistence model.

### 9.6 Planner's Exploration Flow

The planner gets one tool to check the cache before spawning explorers:

```python
class CheckExplorationMemoryTool(BaseTool):
    """Planner checks if a scope was recently explored."""
    name = "check_exploration_memory"

    async def execute(self, arguments, context):
        scope_paths = arguments["paths"]
        cache = context.metadata.get("exploration_memory")
        cached_notes = await cache.check(scope_paths, context.sandbox)
        if cached_notes:
            for note in cached_notes:
                context.task_center.post(note)  # sync — list.append is GIL-atomic
            return {"status": "cached", "note_count": len(cached_notes)}
        return {"status": "needs_exploration"}
```

The flow:

```
Planner starts
  |
  +-- For each scope it wants to understand:
  |     |
  |     +-- check_exploration_memory(["src/auth/"])
  |     |     |
  |     |     +-- CACHED -> notes loaded into Task Center
  |     |     |              skip explorer (save ~15 tool calls)
  |     |     |
  |     |     +-- NEEDS_EXPLORATION -> spawn explorer subagent
  |     |           Explorer posts findings -> notes auto-saved to cache
  |     |
  |     +-- Read Task Center -> has findings either way
  |
  +-- submit_plan(tasks=[...])
```

**Cache behavior in a fast-changing codebase:** Content hash won't match if files changed since last exploration. Returns `None`. Explorer re-explores. The cache is self-invalidating with zero staleness tracking.

**Cache is optional and zero-cost when unused:** If `ExplorationCache` is not wired up, `check_exploration_cache` always returns `needs_exploration`. Explorer runs every time. No harm.

### 9.7 Planner Conflict Prediction

**What it solves:** Scope packets gave agents pre-flight contention reports they couldn't act on (agents can't reschedule). But the **planner** can act — it can restructure decomposition to avoid overlapping scopes. The Ledger's historical edit data, stored in PostgreSQL, gives the planner cross-run intelligence about which files are contentious.

```python
class QueryEditHistoryTool(BaseTool):
    """Planner queries Ledger history to predict scope conflicts."""
    name = "query_edit_history"

    async def execute(self, arguments, context):
        paths = arguments["paths"]
        fc_store: FileChangeStore = context.metadata["file_change_store"]
        rows = await fc_store.contention_hotspots(
            [path_to_ltree(p) for p in paths], limit=10)
        return [{"file": r.file_path,
                 "agents_touched": r.agent_count,
                 "total_edits": r.edit_count} for r in rows]
```

**Usage in planner prompt:**

```
Before decomposing, check edit history for contention hotspots:
  query_edit_history(paths=["src/payment/"])
  -> shared/utils.py: 3 agents, 7 edits (historical)

Planner action: assign shared/utils.py to a single task
or sequence it explicitly before parallel work.
```

**Why this is the right layer for contention awareness:**

| Layer | Can restructure work? | Has edit history? | Result |
|---|---|---|---|
| Agent (worker) | No — ephemeral, can't reschedule | No — sees only its task | Scope packets were here (wrong layer, deleted) |
| Planner | **Yes** — chooses decomposition | **Yes** — via PostgreSQL | Conflict prediction is here (right layer) |

### 9.8 Atlas to Exploration Cache Comparison

| Current Atlas (6 files, ~400 lines) | Exploration Cache (~60 lines) |
|------|------|
| `atlas/service.py` -- lookup_subsystems, persist_scout_brief | `ExplorationMemory.check/save` |
| `atlas/store.py` -- SQL persistence, chunk storage | Simple key-value store |
| `atlas/model.py` -- ORM model | Not needed (key-value) |
| `atlas/persistence.py` -- durable storage | Built into cache |
| `atlas/freshness.py` -- reuse status, staleness checks | Content hash comparison (3 lines) |
| `atlas/identity.py` -- project_key_for() | Scope paths are the identity |
| `tools/atlas/lookup.py` -- planner-facing tool | `CheckExplorationMemoryTool` (~20 lines) |

### 9.9 Files Deleted in This Section

| File | Lines | Replacement |
|------|-------|-------------|
| `code_intelligence/routing/scope_packets.py` | ~190 | Deleted -- Arbiter handles conflicts at edit time |
| `tools/daytona_toolkit/coordination.py` | ~290 | Deleted -- `task.scope_paths` read directly where needed |
| `code_intelligence/atlas/service.py` | ~120 | `ExplorationMemory` (~40 lines) |
| `code_intelligence/atlas/store.py` | ~80 | Simple key-value store |
| `code_intelligence/atlas/model.py` | ~50 | Deleted |
| `code_intelligence/atlas/persistence.py` | ~80 | Built into cache |
| `code_intelligence/atlas/freshness.py` | ~50 | Content hash (3 lines) |
| `code_intelligence/atlas/identity.py` | ~20 | Deleted |
| `tools/atlas/lookup.py` | ~80 | `CheckExplorationMemoryTool` (~20 lines) |
| **Total removed** | **~960** | **~60 new** |

---

## 10. Toolkit Assignment

### 10.1 Agent Types

Plan A reduces `agent_type` from three values to two. Posthook agents are deleted entirely (Section 7.4):

| Type | Description | Dispatching | Can spawn subagents? |
|------|-------------|-------------|---------------------|
| `"agent"` | Regular team-mode agents (planner, developer, reviewer, replanner) | Dispatched as tasks through PGDispatcher → Executor | Planner/replanner: yes (explorer only). Others: no. |
| `"subagent"` | Focused worker subagents (explorer) | Spawned inline via `run_subagent()` tool | No |

**Deleted:** `"posthook"` — all 5 posthook agents are replaced by the deterministic `_posthook()` function (Section 7.3).

### 10.2 Per-Role Toolkit Matrix (Dispatched Roles)

Explorer is a subagent spawned via `run_subagent()`, not a dispatched task. Its toolkits come from its agent definition (Section 10.3), not from `toolkits_for_role()`. This matrix covers only dispatched roles:

| Toolkit | Planner | Developer | Reviewer | Replanner |
|---------|:-------:|:---------:|:--------:|:---------:|
| `code_intelligence` | read (blocked: ci_read_file, ci_edit_hotspots) | full | full | read (blocked: ci_read_file) |
| `sandbox_operations` | -- | full | full | -- |
| `subagent` | spawn (explorer only) | -- | -- | -- |
| `context` | read-only (blocked: post_note) | full | full | read-only (blocked: post_note) |
| `submission` | submit_plan | done, retry, replan | done, retry, replan | submit_replan |

**Implementation note — toolkit consolidation:** The original design specified 5 new toolkits (`task_center_read`, `task_center_write`, `exploration_memory`, `edit_history`, `search`). The implementation consolidates to 2:
- `context` — single unified toolkit merging task_center + search + exploration_memory. Contains `PostNoteTool`, `ReadNotesTool` (with `keyword` param absorbing `search_context`), `ContextChangedSinceTool` (absorbing `scope_changed_since`), and `CheckExplorationMemoryTool`. Role-based read/write restrictions are enforced via `blocked_tools` in agent definitions (e.g., planners block `post_note`) rather than separate toolkit classes.
- `submission` — unchanged from design.

The `memory` toolkit was absorbed into `context`. `query_edit_history` is backed by `FileChangeStore.contention_hotspots()` but not yet wrapped as a tool. No other capability was removed — tools are bundled into fewer registration names for simplicity.

### 10.3 Explorer (Subagent)

Explorer is the only subagent type. It is NOT dispatched through the executor — it runs inline within the planner's turn via `run_subagent()`.

**Flow:**
1. Planner/replanner calls `run_subagent(agent_name="explorer", prompt="...")`
2. Explorer spawns as a background task within the caller's turn
3. Explorer reads code via `code_intelligence` (read-only) and posts notes to Task Center
4. Explorer returns its result via the `run_subagent` return envelope (not via `done()`)
5. Planner reads explorer's findings from Task Center

**Caller restriction (preserved from current system):** Only `planner` and `replanner` can call `run_subagent()`, and they can ONLY spawn `explorer`. This is enforced by `SCOUT_ONLY_CALLERS` policy, not by toolkit assignment.

**Explorer's toolkits** (defined in agent definition, not in `toolkits_for_role()`):

| Toolkit | Access |
|---------|--------|
| `code_intelligence` | read-only |
| `context_read` | yes |
| `context_write` | yes (posts exploration findings) |

Explorer has NO `submission` toolkit — it does not call `done()`, `request_retry()`, or any terminal tool. Its result is captured by the `run_subagent` infrastructure and returned to the caller.

### 10.4 New Toolkits (2)

**`context` toolkit** (replaces `context_inheritance`, `context_sharing`, `team_context`, `search`, `atlas`):

Single unified toolkit. Role-based read/write restrictions are enforced via `blocked_tools` in agent definitions (e.g., planners and replanners block `post_note`) rather than separate read/write classes.

| Tool | Blocked for | Description |
|------|:----------:|------------|
| `read_notes(authors?, scope_paths?, keyword?, limit?)` | — | Read/search notes with optional keyword filter (absorbs former `search_context`). |
| `context_changed_since()` | — | Check if context is stale: scope changes, dep notes, sibling completions since task started. Absorbs former `scope_changed_since`. |
| `post_note(content, scope_paths?)` | planner, replanner | Post a note to Task Center. Inherits task scope_paths by default. |
| `check_exploration_memory(paths)` | — | Check if scope was recently explored. Returns `cached` or `needs_exploration`. |

**Note:** `query_edit_history` is backed by `FileChangeStore.contention_hotspots()` but not yet wrapped as a tool. When implemented, it will be added to this toolkit.

**`submission` toolkit** (replaces 5 posthook toolkit classes):

| Tool | Available to | Description |
|------|-------------|-------------|
| `submit_plan(tasks, rationale)` | planner | Submit plan. Terminal. |
| `done(summary)` | developer, reviewer | Signal completion. Terminal. |
| `request_retry(reason)` | developer, reviewer | Request retry. Terminal. |
| `request_replan(reason, suggestion?)` | developer, reviewer | Request replan. Terminal. |
| `submit_replan(add_tasks, cancel_ids)` | replanner | Submit replan. Terminal. |

### 10.5 Toolkit Factory Changes

```python
# tools/core/factory.py -- registration updates

# DELETED registrations:
#   context_inheritance, context_sharing, team_context, atlas
#   submit_plan_posthook, submit_summary_posthook,
#   posthook_submit_retry, posthook_submit_replan, submit_replan_posthook

# NEW registrations:
register_toolkit_class("submission", SubmissionToolkit)
register_toolkit_class("context", ContextToolkit)  # unified read/write/memory

# UNCHANGED:
#   sandbox_operations, code_intelligence, subagent
```

### 10.6 Role Resolution

Toolkit assignment is handled in agent definitions and the context builder rather than a standalone `toolkits_for_role()` function. The effective mapping is:

```
planner:    code_intelligence, subagent, context, submission          (blocked_tools: post_note, ci_read_file, ci_edit_hotspots)
developer:  sandbox_operations, code_intelligence, context, submission
reviewer:   sandbox_operations, code_intelligence, context, submission
replanner:  code_intelligence, context, submission                   (blocked_tools: post_note, ci_read_file)
```

### 10.7 Complete Tool Inventory

**10 new tools in 3 toolkits**, replacing ~15+ tools/toolkits from the old system:

| # | Tool | Toolkit | Available to | Terminal? | Description |
|---|------|---------|-------------|:---------:|-------------|
| 1 | `read_notes` | `context_read` | all roles | no | Read/search Task Center notes by author, scope, keyword |
| 2 | `context_changed_since` | `context_read` | all roles | no | Check staleness: scope changes + dep notes + sibling completions |
| 3 | `post_note` | `context_write` | developer, reviewer, explorer | no | Post note to Task Center with optional scope |
| 4 | `check_exploration_memory` | `memory` | planner | no | Check cross-run exploration cache; returns `cached` or `needs_exploration` |
| 5 | `query_edit_history` | `memory` | planner | no | Query cross-run edit patterns to predict scope conflicts |
| 6 | `submit_plan` | `submission` | planner | **yes** | Submit plan of TaskSpecs |
| 7 | `done` | `submission` | developer, reviewer | **yes** | Signal task completion with summary |
| 8 | `request_retry` | `submission` | developer, reviewer | **yes** | Request task retry with reason |
| 9 | `request_replan` | `submission` | developer, reviewer | **yes** | Request replan with reason and optional suggestion |
| 10 | `submit_replan` | `submission` | replanner | **yes** | Submit replan (add/cancel tasks) |

**What these replace:**

| Old tool/toolkit | New equivalent |
|-----------------|----------------|
| `share_briefing` | `post_note` |
| `inspect_inherited_context` | `read_notes` |
| `atlas/lookup` | `check_exploration_memory` |
| `scope_packets` tools | Deleted (Arbiter handles at edit time) |
| `coordination.py` helpers | Deleted (`task.scope_paths` read directly) |
| 5 posthook toolkit classes | `submission` toolkit (5 tools, no LLM) |
| `search_context` (standalone) | `read_notes` with `keyword` parameter |
| `scope_changed_since` (standalone) | `context_changed_since` (unified check) |

---

## 11. Task-Agnostic Flows

### 11.1 Greenfield: Empty Project, Build From Scratch

```
User: "Build a REST API for user management"

Planner reads Task Center: empty (no prior context)
Planner does NOT spawn explorers (nothing to explore)
Planner calls submit_plan:

  TaskSpec(id="schema",  task="Create user table migration with id, email, name, created_at",
           agent="developer", scope_paths=["src/db/"])
  TaskSpec(id="api",     task="Implement CRUD endpoints: GET/POST/PUT/DELETE /users",
           agent="developer", deps=["schema"], scope_paths=["src/api/"])
  TaskSpec(id="test",    task="Write integration tests for user API",
           agent="reviewer", deps=["api"], scope_paths=["tests/"])
```

**Why it works:** No explorer, no atlas lookup, no "subsystem discovery". Planner goes straight to work decomposition. Same dispatcher, same executor, same Task Center.

### 11.2 Existing Project: Bug Fix

```
User: "Fix the login timeout bug"

Planner checks exploration cache: miss (first time seeing src/auth/)
Planner spawns explorer subagent:
  run_subagent(agent_name="explorer", prompt="Read src/auth/. Find timeout handling.")

Explorer posts to Task Center:
  Note(content="auth/session.py:42 has 30s timeout. middleware.py:87 has bare
   except that swallows TimeoutError.", scope_paths=["src/auth"])

Planner reads Task Center: sees explorer's finding
Planner calls submit_plan:

  TaskSpec(id="fix", task="Fix: middleware.py:87 bare except should re-raise
   TimeoutError after logging. session.py timeout is correct at 30s.",
           agent="developer", scope_paths=["src/auth/middleware.py"])
  TaskSpec(id="verify", task="Run auth test suite. Verify timeout propagates.",
           agent="reviewer", deps=["fix"], scope_paths=["tests/test_auth.py"])
```

**Why it works:** Planner optionally spawns explorer. Explorer posts prose (no JSON contract). Planner reads prose and plans accordingly. Explorer findings cached for next run.

### 11.3 Existing Project: Feature Implementation

```
User: "Add OAuth2 support to the auth module"

Planner checks exploration cache:
  src/auth/  -> CACHED (explored 3 min ago, files unchanged)
  src/api/routes/ -> NEEDS_EXPLORATION

Planner spawns 1 explorer (not 2 -- cache saved one):
  Explorer: "Read src/api/routes/ for existing endpoint patterns"

Planner reads Task Center (has both cached + fresh findings), decomposes:

  TaskSpec(id="model",    task="Add OAuth2Provider model and token storage",
           agent="developer", scope_paths=["src/auth/models.py"])
  TaskSpec(id="flow",     task="Implement OAuth2 authorization code flow",
           agent="developer", deps=["model"],
           scope_paths=["src/auth/oauth2.py"])
  TaskSpec(id="endpoint", task="Add /auth/oauth2/callback and /auth/oauth2/authorize",
           agent="developer", deps=["flow"],
           scope_paths=["src/api/routes/auth.py"])
  TaskSpec(id="verify",   task="Test OAuth2 flow end-to-end",
           agent="reviewer", deps=["endpoint"],
           scope_paths=["tests/test_oauth2.py"])
```

### 11.4 High-Parallelism Decomposition

```
User: "Refactor the payment module into microservice-ready components"

Planner spawns explorers for all payment submodules:
  Explorer-1: "Read src/payment/billing/"
  Explorer-2: "Read src/payment/invoicing/"
  Explorer-3: "Read src/payment/gateway/"

After explorers report, planner decomposes with maximum parallelism:

  TaskSpec(id="billing",   task="Extract billing into standalone module...",
           agent="developer",
           scope_paths=["src/payment/billing/"])
  TaskSpec(id="invoicing", task="Extract invoicing into standalone module...",
           agent="developer",
           scope_paths=["src/payment/invoicing/"])
  TaskSpec(id="gateway",   task="Extract gateway into standalone module...",
           agent="developer",
           scope_paths=["src/payment/gateway/"])
  TaskSpec(id="verify",    task="Run full payment test suite...",
           agent="reviewer", deps=["billing", "invoicing", "gateway"],
           scope_paths=["tests/test_payment/"])
```

**Why parallelism is safe:**
1. **Non-overlapping scope_paths** -- Planner assigned disjoint scopes. No file-level contention.
2. **Ledger-based awareness** -- if billing developer edits a shared file, the Ledger records it. Other developers see it via `scope_changed_since()` or in their next `context_for()` call.
3. **Arbiter as backstop** -- if scopes unexpectedly collide, the Arbiter catches it at edit time with token validation. Agent re-reads and retries. Rare but recoverable.

**What happens if scopes collide:**

```
billing developer discovers it needs to edit src/payment/shared/utils.py
  -> Arbiter.issue_token("shared/utils.py", hash, billing_agent)
  -> billing developer edits shared/utils.py
  -> Ledger.record("shared/utils.py", billing_agent)

invoicing developer also needs to edit src/payment/shared/utils.py
  -> Arbiter.issue_token("shared/utils.py", hash, invoicing_agent)
  -> hash MATCHES current content (billing edit committed)
      OR hash MISMATCHES (billing edit in progress)
  -> If mismatch: edit tool returns error, agent re-reads file
  -> Arbiter.acquire_file_lock() serializes the write
```

OCC handles the collision at file level. No coordination needed in the Task Center or plan.

---

## 12. Migration Phases

### Phase 1: Task Center + Submission Tools + Exploration Cache

**Scope:** New code, no deletions. Can coexist with current system.

**Prerequisite:** Run PostgreSQL schema migration (Section 14.4) to create `task_notes`, `file_changes`, `tasks`, `exploration_memory` tables with ltree extension and partition lifecycle functions.

| Step | Files | Description |
|------|-------|-------------|
| 1a | `team/task_center.py` | Implement `Note`, `TaskCenter` (post, read, context_for, snapshot, restore) |
| 1b | `tools/submission/done.py` | `DoneTool` -- writes summary to metadata + posts note |
| 1c | `tools/submission/submit_plan.py` | `SubmitPlanTool` -- single-pass validation, writes Plan to metadata |
| 1d | `tools/submission/request_retry.py` | `RequestRetryTool` -- writes RetryRequest to metadata |
| 1e | `tools/submission/request_replan.py` | `RequestReplanTool` -- writes ReplanRequest to metadata |
| 1f | `tools/submission/submit_replan.py` | `SubmitReplanTool` -- writes ReplanPlan to metadata |
| 1g | `tools/submission/types.py` | Submission type definitions (moved from posthook/types.py) |
| 1h | `tools/task_center/toolkit.py` | `PostNoteTool`, `ReadNotesTool`, toolkit classes |
| 1i | `tools/exploration_memory/toolkit.py` | `CheckExplorationMemoryTool`, `ExplorationMemory` |
| 1j | `tools/edit_history/toolkit.py` | `QueryEditHistoryTool` (planner conflict prediction) |
| 1k | `tools/search/toolkit.py` | `SearchContextTool`, `ScopeChangedSinceTool` (PostgreSQL FTS + ltree) |
| 1l | `tools/core/factory.py` | Register new toolkits |

### Phase 2: Query Engine Gate

**Scope:** 2 lines in `_has_submission()`.

| Step | Files | Description |
|------|-------|-------------|
| 2a | `engine/core/query.py` | Add `posthook_enabled` gate to `_has_submission()` |

### Phase 3: Executor Rewrite

**Scope:** Replace `execute_with_posthook` with direct runner + `_posthook()`.

| Step | Files | Description |
|------|-------|-------------|
| 3a | `team/runtime/executor.py` | Rewrite `_run_one()`: direct runner call, `_posthook()` function |
| 3b | `team/runtime/context_builder.py` | Rewrite to use `task_center.context_for()` |

### Phase 4: Data Model Migration

**Scope:** Simplify `Task`, `Plan`, replace `WorkItemSpec` with `TaskSpec`.

| Step | Files | Description |
|------|-------|-------------|
| 4a | `team/models.py` | New `TaskSpec`, simplified `Task`, simplified `Plan`, `ReplanPlan` |
| 4b | `team/planning/validation.py` | Single-pass validation (merge Phase A + B) |
| 4c | `team/runtime/dispatcher.py` | Rewrite as `PGDispatcher` backed by PostgreSQL `SKIP LOCKED` (Section 14.6) |

### Phase 5: Deletion

**Scope:** Remove all replaced components.

| Step | Files to delete | Lines removed |
|------|----------------|---------------|
| 5a | `hooks/agent_posthook.py` | 232 |
| 5b | `team/context/briefings.py` | 179 |
| 5c | `team/context/scout_briefings.py` | ~300 |
| 5d | `team/context/canonicalize.py` | ~50 |
| 5e | `team/artifacts/store.py` | ~100 |
| 5f | `code_intelligence/atlas/` (entire directory) | ~400 |
| 5g | `code_intelligence/routing/scope_packets.py` | ~190 |
| 5h | `tools/daytona_toolkit/coordination.py` | ~290 |
| 5i | `tools/atlas/` (entire directory) | ~80 |
| 5j | `tools/posthook/` (entire directory) | ~400 |
| 5k | `tools/team_context/share_briefing.py` | ~150 |
| 5l | `tools/team_context/inspect_inherited_context.py` | ~80 |
| 5m | 5 posthook agent .md definitions | ~200 |
| 5n | Posthook-related skills/playbooks | ~100 |
| | **Total deletions** | **~2,751** |

### Phase 6: Agent Definition Cleanup

| Step | Files | Description |
|------|-------|-------------|
| 6a | `agents/types.py` | Remove `PosthookConfig` field from `AgentDefinition` |
| 6b | `team/builtins/agents/*.md` | Update toolkit lists to use new toolkit names |
| 6c | `skills/bundled/content/` | Simplify playbooks (remove contract references) |
| 6d | Tests | Update all team/posthook/briefing tests |

---

## 13. Deletion Inventory

### Files Deleted Entirely

| File | Lines | Replaced by |
|------|-------|-------------|
| `hooks/agent_posthook.py` | 232 | `executor._posthook()` (~25 lines) |
| `team/context/briefings.py` | 179 | `task_center.context_for()` (~50 lines) |
| `team/context/scout_briefings.py` | ~300 | Deleted (notes are immutable, no freshness tracking) |
| `team/context/canonicalize.py` | ~50 | Tag matching in Task Center (~5 lines) |
| `team/artifacts/store.py` | ~100 | `TaskCenter.post/read` (~40 lines) |
| `code_intelligence/atlas/service.py` | ~120 | `ExplorationMemory` (~40 lines) |
| `code_intelligence/atlas/store.py` | ~80 | Simple key-value store |
| `code_intelligence/atlas/model.py` | ~50 | Deleted |
| `code_intelligence/atlas/persistence.py` | ~80 | Built into cache |
| `code_intelligence/atlas/freshness.py` | ~50 | Content hash (3 lines) |
| `code_intelligence/atlas/identity.py` | ~20 | Deleted |
| `code_intelligence/routing/scope_packets.py` | ~190 | Deleted (Arbiter handles at edit time) |
| `tools/daytona_toolkit/coordination.py` | ~290 | Deleted (`task.scope_paths` read directly) |
| `tools/atlas/lookup.py` | ~80 | `CheckExplorationMemoryTool` (~20 lines) |
| `tools/posthook/base.py` | ~60 | Merged into submission tools |
| `tools/posthook/types.py` | ~50 | `tools/submission/types.py` (moved) |
| `tools/posthook/submit_plan.py` | ~120 | `tools/submission/submit_plan.py` (simplified) |
| `tools/posthook/submit_summary.py` | ~60 | `tools/submission/done.py` (simplified) |
| `tools/posthook/submit_replan.py` | ~60 | `tools/submission/submit_replan.py` (simplified) |
| `tools/posthook/request_retry.py` | ~40 | `tools/submission/request_retry.py` |
| `tools/posthook/request_replan.py` | ~40 | `tools/submission/request_replan.py` |
| `tools/posthook/toolkits.py` | ~100 | `tools/submission/toolkit.py` |
| `tools/team_context/share_briefing.py` | ~150 | `tools/task_center/toolkit.py` (PostNoteTool) |
| `tools/team_context/inspect_inherited_context.py` | ~80 | `tools/task_center/toolkit.py` (ReadNotesTool) |
| 5 posthook agent `.md` files | ~200 | Deleted |

### Net Impact

| Metric | Current | Plan A | Delta |
|--------|---------|--------|-------|
| Context/briefing layer | ~1,420 lines | ~215 lines | **-1,205** |
| Posthook agent stack | ~630 lines | ~25 lines (_posthook function) | **-605** |
| Submission tools | ~530 lines | ~300 lines (simplified) | **-230** |
| Atlas | ~400 lines | ~60 lines (ExplorationMemory) | **-340** |
| Scope packets + coordination | ~480 lines | 0 lines (deleted) | **-480** |
| New: Task Center | 0 | ~215 lines | **+215** |
| New: Submission toolkit | 0 | ~100 lines | **+100** |
| New: Exploration Cache toolkit | 0 | ~60 lines | **+60** |
| New: PG infrastructure (14.1–14.11) | 0 | ~275 lines | **+275** |
| **Total** | **~3,460** | **~1,250** | **-2,210 lines** |

---

## 14. PostgreSQL Infrastructure

### 14.1 Design Principle: PostgreSQL as Coordination Kernel

Most multi-agent frameworks build custom coordination infrastructure — in-memory message queues, shared state objects, custom lock managers. This design uses PostgreSQL as the universal coordination substrate:

| Coordination need | Custom approach (typical) | PostgreSQL primitive |
|---|---|---|
| Work queue | In-memory priority queue + lock | `FOR UPDATE SKIP LOCKED` |
| Event bus | Redis pub/sub, custom callbacks | `LISTEN / NOTIFY` |
| Distributed file locking | In-memory lock manager | Advisory locks |
| Knowledge search | Vector DB, custom index | GIN + full-text search |
| Time-range queries | Custom ring buffer | BRIN index |
| Set/hierarchy membership | Custom tag matching | `ltree` + GiST |
| Crash recovery | Checkpoint + replay | WAL (built-in) |

One database replaces an entire microservice-style coordination stack. Every component in this plan — dispatcher, Ledger, Task Center, Arbiter, ExplorationMemory — reads and writes PostgreSQL. The in-memory layer is a performance cache, not a source of truth.

**Trade-off acknowledged:** PostgreSQL becomes a single point of failure — if it's down, all coordination primitives fail simultaneously. This is accepted because: (1) a single managed PG instance is operationally simpler than 7 independent subsystems, (2) PG has mature HA solutions (streaming replication, patroni) that protect all primitives at once, and (3) the in-memory cache allows agents already running to complete their current task even during a brief PG outage.

### 14.2 Async Engine & Store Pattern

The existing codebase uses **synchronous** SQLAlchemy (`create_engine` + `psycopg`). The team coordination layer adds an **async** engine alongside it — the executor already uses `asyncio` (`async def run_query()`), and `LISTEN/NOTIFY` fundamentally requires async connections.

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

def create_team_engine(max_agents: int):
    """Async engine for team coordination. Coexists with the existing
    sync engine used by SessionStore, AgentRunStore, etc.

    Uses asyncpg as the async driver (psycopg is sync-only in the
    existing codebase). SQLAlchemy handles connection pooling.

    Budget: max_agents (query connections)
          + 1 (shared LISTEN/NOTIFY connection with in-process fan-out)
          + 4 (headroom for dispatcher, cache, health checks)
    """
    engine = create_async_engine(
        DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=max_agents + 5,
        max_overflow=4,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
```

**Store pattern:** Each domain gets a Store class, matching the existing codebase pattern (`SessionStore`, `AgentRunStore`, etc.):

```python
class NoteStore:
    """Task Center persistence. Follows existing Store pattern."""

    def initialize(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def insert(self, note: TaskNoteRecord) -> None:
        async with self._sf() as db:
            db.add(note)
            await db.commit()

    async def query_by_deps(self, run_id: str, task_ids: list[str]) -> list[TaskNoteRecord]:
        async with self._sf() as db:
            stmt = (select(TaskNoteRecord)
                    .where(TaskNoteRecord.team_run_id == run_id,
                           TaskNoteRecord.task_id.in_(task_ids))
                    .order_by(TaskNoteRecord.created_at))
            return list((await db.execute(stmt)).scalars().all())

    async def search_fts(self, run_id: str, query: str,
                         scope_ltrees: list[str] | None, limit: int) -> list:
        """Full-text + ltree search. Uses text() — no ORM equivalent."""
        async with self._sf() as db:
            result = await db.execute(text("""
                SELECT task_id, agent_name, content, scope_ltree, created_at
                FROM task_notes
                WHERE team_run_id = :run_id
                  AND to_tsvector('english', content)
                      @@ plainto_tsquery('english', :query)
                  AND (:scopes::ltree[] IS NULL OR EXISTS (
                      SELECT 1 FROM unnest(scope_ltree) AS s
                      WHERE s <@ ANY(:scopes::ltree[])))
                ORDER BY created_at DESC LIMIT :lim
            """), {"run_id": run_id, "query": query,
                   "scopes": scope_ltrees, "lim": limit})
            return result.fetchall()


class FileChangeStore:
    """Ledger persistence. Follows existing Store pattern."""

    def initialize(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def insert(self, record: FileChangeRecord) -> None:
        async with self._sf() as db:
            db.add(record)
            await db.commit()

    async def changes_in_scope(self, run_id: str, scope_ltrees: list[str],
                                since: float) -> list:
        """ltree descendant match. Uses text() — PG-specific operator."""
        async with self._sf() as db:
            result = await db.execute(text("""
                SELECT file_path, agent_id, edit_type, created_at
                FROM file_changes
                WHERE team_run_id = :run_id
                  AND path_ltree <@ ANY(:scopes::ltree[])
                  AND created_at > to_timestamp(:since)
                ORDER BY created_at DESC
            """), {"run_id": run_id, "scopes": scope_ltrees, "since": since})
            return result.fetchall()
```

**What uses ORM vs `text()`:**

| Query type | Approach | Why |
|---|---|---|
| Note INSERT/SELECT by ID/deps | **ORM** (`db.add`, `select().where()`) | Standard CRUD, no PG-specific features |
| Task INSERT (plan insertion) | **ORM** (`db.add_all()`) | Bulk insert of model objects |
| Task SELECT by status | **ORM** (`select().where(status == 'ready')`) | Standard filter |
| `pop_ready()` SKIP LOCKED | **`text()`** | Atomic UPDATE+subquery+SKIP LOCKED has no ORM equivalent |
| `mark_done()` conditional UPDATE | **`text()`** | Arithmetic decrement + conditional promotion |
| FTS search (`tsvector`) | **`text()`** | PG-specific full-text operators |
| Scope queries (`ltree <@`) | **`text()`** | PG-specific hierarchical containment |
| `LISTEN/NOTIFY` | **raw asyncpg connection** | No SQLAlchemy abstraction exists |
| Advisory locks | **`text()`** | `SELECT pg_advisory_lock()` |

**LISTEN/NOTIFY access:** SQLAlchemy async exposes the underlying asyncpg connection for PG-specific features:

```python
async with engine.connect() as conn:
    raw = await conn.get_raw_connection()
    asyncpg_conn = raw.driver_connection
    await asyncpg_conn.add_listener(channel, callback)
```

**Alternative for large swarms:** Instead of one LISTEN connection per worker, use a single shared listener connection with in-process fan-out to workers. This caps LISTEN connections at 1 regardless of concurrency. Trade-off: adds ~20 lines of fan-out code.

### 14.3 PostgreSQL-Primary Persistence

PostgreSQL is the single source of truth for all team coordination state. There is no in-memory shadow store. Every write goes to PG (awaited), every read queries PG. This eliminates an entire class of bugs around write ordering, crash windows, and cross-process visibility.

```
Agent calls post_note() or done()
  |
  +-- await INSERT INTO task_notes (...)
       Durable. Searchable (tsvector). Scope-indexed (ltree).
       Visible to all processes immediately on return.

Agent calls edit_file()
  |
  +-- await INSERT INTO file_changes (...)
       Durable. Queryable via scope_changed_since tool.
       Visible to all processes immediately on return.
```

**Why not dual-write:** An earlier revision of this plan proposed dual-write (PG + in-memory list). This was rejected because:

1. **Write ordering bugs** — PG-first-then-in-memory creates a crash window where PG has the note but the in-memory list doesn't. In-memory-first creates the opposite. Either way, the two stores can disagree.
2. **Hydration complexity** — multi-process deployments require a `hydrate()` call before every `context_for()` to sync the in-memory list from PG. This is easy to forget and hard to test.
3. **Marginal latency benefit** — `context_for()` runs once per task start, not in a hot loop. A PG round-trip (~1ms on localhost, ~5ms networked) is negligible compared to the LLM call that follows (~seconds).
4. **Consistency by construction** — with one store, `search_context` and `context_for` always agree on note visibility. No ordering invariants to maintain.

**Trade-off acknowledged:** Every `read()` and `context_for()` call hits PG. For single-process deployments on localhost this adds ~1ms per query — unmeasurable against LLM latency. If profiling later shows PG reads as a bottleneck (unlikely), a read-through cache can be added as a transparent layer without changing the write path or the consistency model.

### 14.4 Schema

```sql
CREATE EXTENSION IF NOT EXISTS ltree;

-- Task Center backing store
CREATE TABLE task_notes (
    id          UUID NOT NULL DEFAULT gen_random_uuid(),
    team_run_id TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    content     TEXT NOT NULL,
    scope_ltree ltree[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

-- Per-partition indexes (auto-created with each partition)
CREATE INDEX ON task_notes (task_id);
CREATE INDEX ON task_notes USING GiST (scope_ltree);
CREATE INDEX ON task_notes USING BRIN (created_at);
CREATE INDEX ON task_notes USING GIN (to_tsvector('english', content));

-- Ledger backing store
CREATE TABLE file_changes (
    id          BIGSERIAL NOT NULL,
    team_run_id TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    path_ltree  ltree NOT NULL,
    agent_id    TEXT NOT NULL,
    edit_type   TEXT DEFAULT 'edit',
    old_hash    TEXT DEFAULT '',
    new_hash    TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

CREATE INDEX ON file_changes USING GiST (path_ltree);
CREATE INDEX ON file_changes USING BRIN (created_at);

-- Task queue (dispatcher backing store)
CREATE TABLE tasks (
    id           TEXT NOT NULL,
    team_run_id  TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    task         TEXT NOT NULL,
    deps         TEXT[] DEFAULT '{}',
    scope_paths  TEXT[] DEFAULT '{}',
    scope_ltree  ltree[] DEFAULT '{}',
    cascade_policy TEXT DEFAULT 'cancel',  -- "cancel" | "retry_first" | "continue"
    parent_id    TEXT,
    root_id      TEXT DEFAULT '',
    depth        INT DEFAULT 0,
    pending_dep_count INT DEFAULT 0,   -- decremented by mark_done(); 0 = deps satisfied
    retry_count  INT DEFAULT 0,
    max_retries  INT DEFAULT 2,
    agent_run_id TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    failure_reason TEXT,
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

CREATE INDEX ON tasks (team_run_id, status);
CREATE INDEX ON tasks (team_run_id, depth, created_at);

-- Exploration cache (not partitioned — shared across runs)
CREATE TABLE exploration_memory (
    cache_key    TEXT PRIMARY KEY,
    scope_paths  TEXT[] NOT NULL,
    content_hash TEXT NOT NULL,
    notes        JSONB NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    accessed_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Partition lifecycle (called by TeamRun setup/teardown):
--
-- _VALID_RUN_ID = re.compile(r'^[a-zA-Z0-9_\-]+$')
--
-- async def create_partitions(run_id: str):
--     if not _VALID_RUN_ID.match(run_id):
--         raise ValueError(f"Invalid run_id: {run_id!r}")
--     h = hashlib.sha256(run_id.encode()).hexdigest()[:12]
--     for table in ["task_notes", "file_changes", "tasks"]:
--         -- table names and h are safe (hardcoded / hex digest).
--         -- run_id is validated above against [a-zA-Z0-9_\-].
--         await conn.execute(f"""
--             CREATE TABLE IF NOT EXISTS {table}_{h}
--             PARTITION OF {table} FOR VALUES IN ('{run_id}')
--         """)
--
-- async def drop_partitions(run_id: str):
--     if not _VALID_RUN_ID.match(run_id):
--         raise ValueError(f"Invalid run_id: {run_id!r}")
--     h = hashlib.sha256(run_id.encode()).hexdigest()[:12]
--     for table in ["task_notes", "file_changes", "tasks"]:
--         await conn.execute(f"DROP TABLE IF EXISTS {table}_{h}")
--     -- Instant cleanup, no vacuum needed
```

### 14.5 Index Strategy

| Query pattern | SQL | Index type | Why |
|---|---|---|---|
| Dep notes | `WHERE task_id = ANY($1)` | B-tree on `task_id` | O(log n) exact match |
| Scope hierarchy | `WHERE $1::ltree @> ANY(scope_ltree)` | GiST on `ltree[]` | Hierarchical containment — scalar `@>` (ancestor-of) against each element of the ltree array. `'src.auth' @> 'src.auth.sessionDpy'` is `TRUE`. |
| Changes in scope | `WHERE path_ltree <@ ANY($1::ltree[])` | GiST on `ltree` | Descendant match — scalar `<@` (descendant-of) against each query scope. Finds all files under a directory. |
| Changes since | `WHERE created_at > $1` | BRIN on timestamp | O(pages) for append-only data. Tiny index (KBs). |
| Full-text search | `to_tsvector('english', content) @@ plainto_tsquery($1)` | GIN on tsvector | Built-in PostgreSQL FTS |
| Cache lookup | `WHERE cache_key = $1` | Primary key | O(1) |
| Ready tasks | `WHERE status = 'ready' AND pending_dep_count = 0 FOR UPDATE SKIP LOCKED` | B-tree on `(team_run_id, status)` | Work queue pop — `pending_dep_count = 0` avoids correlated subquery on deps |

**`path_to_ltree()` specification:**

```python
import re

_LTREE_UNSAFE = re.compile(r'[^a-zA-Z0-9_]')

def _escape_char(ch: str) -> str:
    """Escape a non-alphanumeric character to a reversible representation.
    Dots → 'D', hyphens → 'H', others → 'X' + 2-digit hex ordinal.
    This prevents collisions: 'my-module' → 'myHmodule',
    'my_module' → 'my_module' (unchanged). Distinct inputs always
    produce distinct ltree labels."""
    if ch == '.':
        return 'D'
    if ch == '-':
        return 'H'
    return f'X{ord(ch):02x}'

def path_to_ltree(path: str) -> str:
    """Convert a file path to an ltree label path.

    Rules:
      1. Strip leading/trailing slashes.
      2. Split on '/'.
      3. For each path component, replace unsafe characters using
         reversible escaping (_escape_char). This avoids collisions:
         'my-module' and 'my_module' map to different labels.
      4. ltree labels must be [a-zA-Z0-9_], max 256 chars.
      5. Drop empty labels.

    Examples:
      "src/auth/"                → "src.auth"
      "src/auth/session.py"      → "src.auth.sessionDpy"
      "src/auth/__init__.py"     → "src.auth.__init__Dpy"
      "src/payment/utils.v2.py"  → "src.payment.utilsDv2Dpy"
      "src/my-module/foo.py"     → "src.myHmodule.fooDpy"
      "src/my_module/foo.py"     → "src.my_module.fooDpy"
      "/leading/slash"           → "leading.slash"

    Collision safety: 'my-module' → 'myHmodule' vs 'my_module' →
    'my_module'. Distinct paths always produce distinct ltree values.
    """
    parts = path.strip('/').split('/')
    labels = []
    for part in parts:
        label = _LTREE_UNSAFE.sub(lambda m: _escape_char(m.group()), part)
        if label:
            labels.append(label)
    return '.'.join(labels)
```

**Why `ltree` over `TEXT[]` with `&&`:** The `&&` (overlap) operator only checks if two arrays share an element. It cannot match `src/auth/` against `src/auth/session.py` — those are different strings. `ltree` handles hierarchical containment natively: `'src.auth' @> 'src.auth.sessionDpy'` is `TRUE`. This makes all scope queries correct by construction.

**Correct ltree operator usage:** The `@>` (ancestor-of) and `<@` (descendant-of) operators are defined on scalar `ltree` values, not on `ltree[]` arrays. When columns are `ltree[]` (like `scope_ltree`), use `ANY()` to unwrap: `s <@ ANY($1::ltree[])` checks if scalar `s` is a descendant of any element in the query array. For columns that are scalar `ltree` (like `path_ltree`), `path_ltree <@ ANY($1::ltree[])` works directly. Never use `ltree[] @> ltree[]` — that's the array containment operator, not the ltree hierarchy operator.

**Why BRIN on timestamps:** Ideal for append-only tables where timestamps are naturally ordered. BRIN indexes are tiny (KBs, not MBs) and scan only the physical pages that contain matching timestamps. Perfect for `changes_since()` queries.

### 14.6 Dispatcher: PostgreSQL-Backed Work Queue

The dispatcher's `pop_ready()` becomes a single PostgreSQL query using `FOR UPDATE SKIP LOCKED` — a purpose-built work queue primitive:

```python
class PGDispatcher:
    """Dispatcher backed by PostgreSQL. No in-memory DAG state.
    Uses async_sessionmaker from the team engine (Section 14.2).
    ORM for standard CRUD, text() for PG-specific atomic operations."""

    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def pop_ready(self, run_id: str) -> TaskRecord | None:
        """Atomically claim the next ready task. Lock-free under concurrency.
        Uses text() — the atomic UPDATE+subquery+SKIP LOCKED pattern
        has no ORM equivalent."""
        async with self._sf() as db:
            row = (await db.execute(text("""
                UPDATE tasks SET status = 'running', started_at = NOW()
                WHERE id = (
                    SELECT t.id FROM tasks t
                    WHERE t.team_run_id = :run_id
                      AND t.status = 'ready'
                      AND t.pending_dep_count = 0
                    ORDER BY t.depth, t.created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
            """), {"run_id": run_id})).fetchone()
            await db.commit()
            return TaskRecord.from_row(row) if row else None

    async def mark_done(self, task_id: str, run_id: str) -> list[str]:
        """Mark task done, decrement pending_dep_count on dependents,
        and promote any that reach zero. Uses text() — conditional
        arithmetic UPDATE has no ORM equivalent."""
        async with self._sf() as db:
            await db.execute(text(
                "UPDATE tasks SET status = 'done', finished_at = NOW() "
                "WHERE id = :task_id AND team_run_id = :run_id"),
                {"task_id": task_id, "run_id": run_id})
            # Decrement pending_dep_count for all tasks that depend
            # on the completed task, and promote those that hit zero.
            promoted = (await db.execute(text("""
                UPDATE tasks t
                SET pending_dep_count = pending_dep_count - 1,
                    status = CASE
                        WHEN pending_dep_count - 1 = 0 THEN 'ready'
                        ELSE status
                    END
                WHERE t.team_run_id = :run_id
                  AND t.status = 'pending'
                  AND :task_id = ANY(t.deps)
                  AND t.pending_dep_count > 0
                RETURNING CASE
                    WHEN pending_dep_count = 0 THEN t.id
                    ELSE NULL
                END AS promoted_id
            """), {"run_id": run_id, "task_id": task_id})).fetchall()
            await db.commit()
            return [r.promoted_id for r in promoted
                    if r.promoted_id is not None]

    async def insert_plan(self, run_id: str, tasks: list[TaskSpec],
                          parent_id: str | None = None,
                          parent_depth: int = 0,
                          parent_root_id: str | None = None) -> None:
        """Insert plan tasks atomically via ORM bulk insert.
        Roots start as 'ready', others as 'pending'.

        After insertion, a text() catch-up pass decrements
        pending_dep_count for any deps that are already done
        (handles external deps from prior plans whose mark_done()
        already fired)."""
        async with self._sf() as db:
            records = []
            for spec in tasks:
                status = "ready" if not spec.deps else "pending"
                root_id = parent_root_id if parent_id else spec.id
                records.append(TaskRecord(
                    id=spec.id, team_run_id=run_id,
                    agent_name=spec.agent, status=status,
                    task=spec.task, deps=spec.deps,
                    scope_paths=spec.scope_paths,
                    scope_ltree=[path_to_ltree(p) for p in spec.scope_paths],
                    parent_id=parent_id, root_id=root_id,
                    depth=(parent_depth + 1) if parent_id else 0,
                    pending_dep_count=len(spec.deps),
                ))
            db.add_all(records)
            await db.flush()  # IDs visible for catch-up query

            # Catch-up: decrement pending_dep_count for deps already done.
            # Uses text() — conditional arithmetic UPDATE with CTE.
            await db.execute(text("""
                WITH already_done AS (
                    SELECT id FROM tasks
                    WHERE team_run_id = :run_id AND status = 'done'
                )
                UPDATE tasks t
                SET pending_dep_count = pending_dep_count - (
                        SELECT COUNT(*) FROM already_done ad
                        WHERE ad.id = ANY(t.deps)
                    ),
                    status = CASE
                        WHEN pending_dep_count - (
                            SELECT COUNT(*) FROM already_done ad
                            WHERE ad.id = ANY(t.deps)
                        ) = 0 THEN 'ready'
                        ELSE status
                    END
                WHERE t.team_run_id = :run_id
                  AND t.status = 'pending'
                  AND t.deps && (SELECT array_agg(id) FROM already_done)
            """), {"run_id": run_id})
            await db.commit()
```

**What this replaces:**

| Concern | In-memory dispatcher | PG dispatcher |
|---|---|---|
| Ready queue | `collections.deque` + manual promotion | `WHERE status = 'ready' FOR UPDATE SKIP LOCKED` |
| Concurrency | `asyncio.Lock` around pop | `SKIP LOCKED` — no application lock |
| Crash recovery | Lost — must rebuild from checkpoint | Free — tasks in 'running' at crash time get retried |
| DAG traversal | Manual walk + parent pointers | `pending_dep_count` column, decremented atomically on completion |
| State consistency | Single-process only | Multi-process safe |

### 14.7 Real-Time Scope Awareness: LISTEN/NOTIFY (DEFERRED)

> **Status: Not yet implemented.** Agents currently get scope warnings at task start via `_inject_scope_warnings()` (executor) and can call `context_changed_since()` mid-task. LISTEN/NOTIFY adds real-time push during long-running tasks — valuable for high-parallelism but not blocking for the current single-process executor. Implement when multi-worker parallelism warrants it.

Agents discover concurrent file changes via push, not poll. When the Ledger records an edit, a PostgreSQL trigger notifies all listeners:

```sql
CREATE OR REPLACE FUNCTION notify_scope_change() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'scope_change_' || NEW.team_run_id,
        json_build_object(
            'file_path', NEW.file_path,
            'agent_id', NEW.agent_id,
            'edit_type', NEW.edit_type
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_scope_change
    AFTER INSERT ON file_changes
    FOR EACH ROW EXECUTE FUNCTION notify_scope_change();
```

**Executor-side listener:**

Uses the engine's existing synthetic message pattern — a `SystemReminderBlock` appended to both `api_messages` (so the agent sees it on the current turn) and `display_messages` (so it's persisted in conversation history). This is the same dual-write mechanism the engine uses for budget warnings and background task notifications.

```python
async def _listen_scope_changes(self, engine, run_id: str,
                                 scope_paths: list[str],
                                 api_messages: list[ConversationMessage]):
    """Subscribe to file changes in agent's scope via SQLAlchemy async engine.
    Drops to raw asyncpg connection for LISTEN — no SQLAlchemy abstraction exists.
    Appends SystemReminderBlock to api_messages on scope change."""
    channel = f"scope_change_{run_id}"
    self._pending_scope_changes: list[dict] = []
    self._last_flush = time.monotonic()

    # Access raw asyncpg connection through SQLAlchemy async
    self._listen_conn = await engine.connect()
    raw = await self._listen_conn.get_raw_connection()
    asyncpg_conn = raw.driver_connection
    await asyncpg_conn.add_listener(channel, lambda *args:
        self._buffer_scope_change(args, scope_paths))

def _buffer_scope_change(self, args, scope_paths):
    """Buffer scope changes for debounced flushing."""
    conn, pid, channel, payload = args
    change = json.loads(payload)
    if any(change["file_path"].startswith(p) for p in scope_paths):
        self._pending_scope_changes.append(change)

def _flush_scope_changes(self,
                         api_messages: list[ConversationMessage],
                         display_messages: list[ConversationMessage]):
    """Flush buffered scope changes as a single SystemReminderBlock.
    Called by the executor every 5 seconds (debounce window).
    Writes to both api_messages (immediate LLM visibility) and
    display_messages (persistent history)."""
    if not self._pending_scope_changes:
        return
    now = time.monotonic()
    if now - self._last_flush < 5.0:
        return

    # Deduplicate by file_path, keep latest
    seen = {}
    for change in self._pending_scope_changes:
        seen[change["file_path"]] = change
    changes = list(seen.values())
    self._pending_scope_changes.clear()
    self._last_flush = now

    lines = [f"- {c['file_path']} edited by {c['agent_id']} ({c['edit_type']})"
             for c in changes]
    text = ("Warning: files in your scope were edited by other agents. "
            "Re-read before editing:\n" + "\n".join(lines))

    msg = ConversationMessage(role="user", content=[
        SystemReminderBlock(category="scope_change", text=text)
    ])
    api_messages.append(msg)       # agent sees it on current turn
    display_messages.append(msg)   # persisted for history + compaction
```

**What this solves:** `context_for()` builds context at task start — a snapshot that goes stale during long-running tasks. `LISTEN/NOTIFY` closes this gap with push-based, real-time warnings about concurrent edits in the agent's scope. Near-zero cost — built into PostgreSQL's connection protocol, no additional process or polling.

**Why `SystemReminderBlock` into both `api_messages` and `display_messages`, not `inject_system_message()`:** The engine has no `inject_system_message()` API. It uses synthetic user messages with typed content blocks — the same pattern used for budget warnings (`notifications.py`) and background task notifications. Dual-write ensures:
1. **Immediate** — `api_messages` insertion means the agent sees scope changes on its current reasoning turn, before it acts on stale data
2. **Persistent** — `display_messages` insertion means the warning is part of conversation history, so the agent can reference it on later turns and the user can see what happened
3. **Compactable** — old scope-change warnings in `display_messages` are subject to the engine's compaction strategy, preventing unbounded accumulation
4. **Categorized** — `category="scope_change"` lets the engine filter or batch these blocks in both lists

**Backpressure:** In high-parallelism runs, an agent editing many files generates many NOTIFY events. The executor-side listener debounces: notifications for the same scope are batched into a single `SystemReminderBlock` every 5 seconds, deduplicated by file path. This prevents context window flooding while keeping agents informed.

### 14.8 Agent Search Tools

Two tools that delegate to Store classes (Section 14.2), available to all roles:

```python
class SearchContextTool(BaseTool):
    """Search notes by keyword and/or scope. Delegates to NoteStore."""
    name = "search_context"

    async def execute(self, arguments, context):
        query = arguments.get("query")
        scope = arguments.get("scope_paths")
        limit = arguments.get("limit", 10)
        run_id = context.metadata["team_run_id"]
        note_store: NoteStore = context.metadata["note_store"]

        ltree_scopes = [path_to_ltree(p) for p in scope] if scope else None
        rows = await note_store.search_fts(run_id, query, ltree_scopes, limit)
        return [{"task_id": r.task_id, "agent": r.agent_name,
                 "summary": r.content[:500], "scope": r.scope_ltree}
                for r in rows]


class ScopeChangedSinceTool(BaseTool):
    """Check what files changed in scope since a timestamp.
    Delegates to FileChangeStore."""
    name = "scope_changed_since"

    async def execute(self, arguments, context):
        paths = arguments["paths"]
        since = arguments["since"]
        run_id = context.metadata["team_run_id"]
        fc_store: FileChangeStore = context.metadata["file_change_store"]

        ltree_scopes = [path_to_ltree(p) for p in paths]
        rows = await fc_store.changes_in_scope(run_id, ltree_scopes, since)

        if not rows:
            return {"changed": False}
        return {"changed": True, "files": [
            {"path": r.file_path, "agent": r.agent_id,
             "type": r.edit_type,
             "seconds_ago": int(time.time() - r.created_at.timestamp())}
            for r in rows
        ]}
```

### 14.9 How Agents Use Search

```
Developer starts working on src/auth/middleware.py
  |
  +-- context_for() provides (from in-memory, fast):
  |     task description + scope
  |     dep notes (explorer findings)
  |     ledger changes in scope
  |     parent chain rationale
  |
  +-- LISTEN/NOTIFY provides (push, real-time):
  |     "Warning: src/auth/session.py edited by agent_A 2s ago"
  |     Agent re-reads file before editing
  |
  +-- Mid-run, agent wants more context:
  |     search_context(query="timeout handling", scope_paths=["src/auth/"])
  |     -> PostgreSQL full-text search, returns relevant notes from this run
  |
  +-- Before committing a large multi-file change:
  |     scope_changed_since(paths=["src/auth/"], since=task_started_at)
  |     -> PostgreSQL ledger query, returns file changes since task started
  |
  +-- Edit-time conflict:
        Arbiter handles it inside the tool (in-memory or advisory lock)
```

### 14.10 Cache/Search Consistency

When `ExplorationMemory.check()` returns cached notes from a previous run, those notes exist in in-memory `TaskCenter._notes` but NOT in the current run's `task_notes` partition. Agents using `search_context` won't find cached exploration data.

**Fix:** On cache hit, batch-insert cached notes via NoteStore:

```python
async def check(self, scope_paths, sandbox, run_id,
                note_store: NoteStore):
    cached = ...  # existing cache lookup
    if cached:
        # Insert into current run's task_notes for FTS discoverability.
        # Uses ORM bulk insert via the Store pattern.
        records = [TaskNoteRecord(
            id=n.id, team_run_id=run_id, task_id=n.task_id,
            agent_name=n.agent_name, content=n.content,
            scope_ltree=[path_to_ltree(p) for p in scope_paths],
            created_at=n.timestamp,
        ) for n in cached]
        await note_store.insert_batch(records)  # ON CONFLICT DO NOTHING
    return cached
```

One batch INSERT, zero schema changes. Cached notes become searchable in the current run.

### 14.11 Advisory Locks for Multi-Process Arbiter (DEFERRED)

> **Status: Not yet implemented.** The current Arbiter uses in-process `threading.Lock` for per-file locking, which is correct for single-process deployments. Advisory locks are needed only for multi-process horizontal scaling, which is not the current deployment model.

The current Arbiter uses in-memory `asyncio.Lock` for per-file locking. For multi-process executor deployments (horizontal scaling), PostgreSQL advisory locks provide distributed file-level locking with zero additional infrastructure:

```python
class PGArbiter:
    """Arbiter with PostgreSQL advisory locks. Multi-process safe.
    Uses SQLAlchemy text() — advisory locks are PG-specific."""

    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def acquire_file_lock(self, file_path: str) -> None:
        lock_key = self._path_to_lock_key(file_path)
        async with self._sf() as db:
            await db.execute(text("SELECT pg_advisory_lock(:key)"),
                             {"key": lock_key})

    async def release_file_lock(self, file_path: str) -> None:
        lock_key = self._path_to_lock_key(file_path)
        async with self._sf() as db:
            await db.execute(text("SELECT pg_advisory_unlock(:key)"),
                             {"key": lock_key})

    def _path_to_lock_key(self, path: str) -> int:
        """Stable hash of file path to PG advisory lock key (int8)."""
        return int(hashlib.sha256(path.encode()).hexdigest()[:15], 16)
```

**When to use:** Optional. Single-process deployments keep the in-memory `asyncio.Lock` (faster, simpler). Switch to advisory locks when running multiple executor processes against the same sandbox.

### 14.12 Updated Toolkit Matrix (Dispatched Roles)

Explorer is a subagent (see Section 10.3) — its toolkits are defined in its agent definition, not here.

| Toolkit | Planner | Developer | Reviewer | Replanner |
|---------|:-------:|:---------:|:--------:|:---------:|
| `code_intelligence` | read (blocked: ci_read_file, ci_edit_hotspots) | full | full | read (blocked: ci_read_file) |
| `sandbox_operations` | -- | full | full | -- |
| `subagent` | spawn (explorer only) | -- | -- | -- |
| `context` | read-only (blocked: post_note) | full | full | read-only (blocked: post_note) |
| `submission` | submit_plan | done, retry, replan | done, retry, replan | submit_replan |

See Section 10.4 for toolkit consolidation rationale (2 toolkits instead of 5).

---

*End of document.*
