# Atlas Scout Promotion Design

## Goal
Reduce duplicate exploration and token burn on fresh SWE-EVO runs by reusing completed foreground scout output inside the current run, while still persisting useful context to Atlas for later retries and resumes.

## Problem
Current fresh SWE-EVO runs can do both of these at once:

- Foreground planners launch `scout` subagents to map ownership.
- Atlas maintenance launches `atlas_builder` / `atlas_refresher`, which in turn launch more `scout` subagents for overlapping surfaces.

That means Atlas is acting like a second exploration lane during the same run instead of a cache for future runs.

Scout itself is already background-only today:

- planners invoke it through `run_subagent`
- `run_subagent` always backgrounds the task
- submitted plans cannot target subagents directly

So the immediate waste is not scout scheduling. The waste is Atlas maintenance spawning its own duplicate scout work alongside the foreground planner path.

## Design Summary

### Core split
- Same-run reuse comes from `shared_briefings`.
- Cross-run reuse comes from Atlas.

### High-parallelism requirements
For this design to remain resilient under high parallelism, it must provide:

- pre-edit scope packet: every developer and validator lane gets compact live scope status automatically before work starts
- multi-stage validation: the runtime revalidates scope freshness and ownership at startup, before acquiring a writable file set, and again at commit/apply time
- same-run freshness gate: if ledger activity appears inside a scope after a scout snapshot, that briefing is marked stale immediately
- active collision signal: workers see in-flight edits or hotspot contention before they start editing
- single-flight scope refresh: at most one refresh scout per canonical scope may be in flight at a time
- stable ownership memory: the latest reusable scout per canonical scope is promoted and reused automatically
- bounded replacement policy: shared scope memory remains useful under fanout instead of rejecting new reusable scopes arbitrarily
- coherent live snapshot: workers see ledger, arbiter, tree, symbol, and briefing state from one coherent read boundary
- file-set targeted guidance: recommendations are computed for the worker's owned or intended write set, not only for a broad scope
- cheap runtime path: hot-path coordination remains read-only and non-LLM unless a real scout refresh is required

### Foreground path
- Planner launches `scout` exactly as today.
- Completed scout output becomes a real team artifact under a stable per-scope key.
- If the scout brief passes the reusable-quality gate, runtime promotes it into `shared_briefings` for the rest of the run.
- Developer and validator lanes receive a pre-edit scope packet derived from the latest shared briefing plus live scope status before they begin code work.
- If the packet recommends a refresh, runtime uses a per-scope single-flight lease in shared coordination state so sibling workers across the fleet wait on or reuse the same refresh instead of spawning duplicate scouts.

### Background path
- Atlas persistence happens only after scout completion.
- Persistence reuses the completed scout brief and computes `content_hashes` and `symbol_ids` without a second LLM turn.
- Fresh SWE-EVO runs do not launch Atlas-owned scout refreshes on lookup misses.
- Hot-path coordination stays read-only and non-LLM; only genuine stale/missing scope context should trigger a fresh scout.

### Atlas must not launch scout
Atlas should not initiate normal foreground exploration.

Required rule:

- planner or execution logic decides when fresh exploration is needed
- foreground `scout` produces the owned brief
- runtime reuses that brief for same-run context and Atlas persistence
- Atlas consumes scout output but does not spawn scout itself

In other words:

- `scout -> Atlas` is allowed
- `Atlas -> scout` is not allowed on the normal runtime path

Reason:

- avoids duplicate exploration work
- avoids competing with active planner/developer lanes
- keeps Atlas as a passive cache/persistence layer instead of a second exploration scheduler
- preserves predictable resource use under high parallelism

## Scope
This design is intentionally scoped to fresh SWE-EVO benchmark runs.

It does not change the default runtime behavior for:

- greenfield runs
- non-benchmark team runs
- resumed or retried SWE-EVO runs

Resumed and retried SWE-EVO runs should continue to prefer `atlas_lookup` early, because Atlas is most useful when a prior run has already populated it.

## Current Temporary Policy
Until deferred scout persistence lands, fresh SWE-EVO runs should keep Atlas maintenance disabled entirely.

This avoids:

- startup bootstrap work
- lookup-miss refresh work
- dirty-path idle refresh work
- duplicate Atlas-owned scout passes during the active benchmark run

This is a benchmark policy only. It does not imply Atlas should be removed from the general team runtime.

## Constraints

### 1. Auto-promotion must use the Atlas trust gate
Runtime must not promote every scout brief with a `canonical_scope`.

The auto-promotion rule should be exactly the same reusable-quality contract Atlas already uses:

- explicit empty-area brief is allowed
- otherwise `scope_coverage` must be present and above the reuse threshold
- `suggested_subdivisions` must be empty
- `gaps` must be empty

Implementation note:
- Do not duplicate this logic in `share_briefing` or benchmark-only code.
- Extract the existing Atlas reusable-brief predicate into a shared helper and reuse it for both Atlas reuse and same-run scout auto-promotion.
- When a promoted briefing becomes stale due to same-run ledger edits in scope, do not evict it immediately; mark it stale in live scope status so workers know to refresh before relying on it.
- Promotion and replacement must be version-guarded:
  - every promoted scout carries the `scope_generation` and `snapshot_time` it observed
  - replacing a latest-per-scope artifact or shared briefing must use compare-and-swap semantics
  - an older scout completion must not overwrite a newer promoted briefing for the same canonical scope
- Freshness must be graded, not binary:
  - `fresh`: no newer in-scope edits since `snapshot_time`
  - `locally_touched`: newer edits exist, but only within a narrow subset of the scoped surface, so the briefing remains advisory for untouched files
  - `structurally_stale`: newer edits or a newer scope generation invalidate the ownership brief for the scope as a whole
- Recommendation policy must distinguish `locally_touched` from `structurally_stale` so workers can narrow their target surface before escalating to a new scout.

### 2. Fresh-run scheduler policy must be explicit
Fresh SWE-EVO runs currently wire the Atlas scheduler and allow cold-start bootstrap and lookup-miss refresh behavior.

This design requires a benchmark-specific scheduler policy, not a one-off conditional on lookup misses.

Required policy for fresh SWE-EVO runs:

- disable cold-start Atlas bootstrap
- disable miss-driven Atlas refresh when `atlas_lookup` returns `action="scout"`
- defer Atlas persistence until after a foreground scout completes
- defer dirty refresh work until foreground planning/execution is no longer on the critical path
- keep pre-edit coordination on the cheap read-only path so worker fanout does not create extra LLM load
- route any same-run stale/missing scope refresh through a per-scope single-flight coordinator so parallel workers cannot trigger duplicate scout refreshes for the same canonical scope
- do not allow Atlas maintenance to trigger scout on the normal runtime path

Non-fresh runs can keep the existing Atlas scheduler behavior.

### 3. Promoted scout artifacts must use stable per-scope keys
Do not save scout artifacts under append-only refs like `scout:<scope>:<id>`.

Reason:
- the artifact store only reclaims bytes when the same key is overwritten
- append-only refs would grow artifact usage quickly under scout fanout

Use a stable key such as:

- `scout:<canonical_scope>`

This keeps only the latest reusable scout artifact per scope in prompt-facing storage.

Write rule:

- latest-per-scope artifact replacement must be guarded by `scope_generation` and `snapshot_time`
- a completion may overwrite the stable key only if it is at least as new as the currently stored promoted scout for that scope
- ties must break deterministically by run-local sequence so concurrent completions do not oscillate the latest view

The audit/history layer already exists in subagent run tracking and should remain the place for append-only debugging history.

### 4. Preserve run identity separately from artifact identity
Today `run_subagent` returns `artifact_ref=sub_run_id` for summary/brief envelopes.

If scout results become real team artifacts, `artifact_ref` should refer to the stored team artifact, not the subagent run id.

The envelope should therefore split these fields:

- `artifact_ref`: real team artifact ref when one exists
- `run_id`: subagent run id for audit, progress, and persistence lookups

Do not continue overloading one field with two meanings.

### 5. Atlas persistence must reuse existing write semantics
The deterministic Atlas write path already lives in `submit_atlas`:

- subsystem derivation
- `snapshot_time` handling
- `content_hashes`
- `symbol_ids`
- version-guarded upsert

Do not fork those semantics into a second Atlas writer.

Instead:

- factor the core "brief -> AtlasChunk(s) -> upsert" logic into a reusable helper
- keep `submit_atlas` as one caller of that helper
- add a runtime persistence path as another caller of that helper

This is a real refactor, not a small enum addition to the scheduler.

## Proposed Runtime Flow

### Fresh SWE-EVO run
1. Root planner starts without Atlas bootstrap pressure.
2. Planner launches `scout`.
3. `run_subagent` receives a completed scout brief.
4. Runtime derives `canonical_scope` and evaluates the shared reusable-quality gate.
5. Runtime stores the scout artifact under a stable per-scope artifact key.
6. If reusable, runtime promotes it into `shared_briefings`.
7. Runtime enqueues a lightweight Atlas persistence task that reuses the completed scout brief and writes it to Atlas without another scout.
8. Planner and sibling subagents reuse `shared_briefings` during the current run.

### Resumed or retried SWE-EVO run
1. Planner uses `atlas_lookup` early once it has stable subsystem keys.
2. `use` hits are attached as explicit briefings via real artifact refs.
3. `refresh` or `scout` results fall back to fresh foreground scouting.
4. Freshly completed scouts are again promoted to same-run shared context and persisted back to Atlas asynchronously.

## Required Code Changes

### A. Introduce explicit scheduler policy
Add a scheduler policy for fresh benchmark runs so the Atlas scheduler can be configured without changing global runtime defaults.

Suggested shape:

- default policy: current behavior
- fresh SWE-EVO policy: no bootstrap, no miss-driven Atlas scout refresh, deferred persistence only

### B. Save completed scout briefs as real team artifacts
Update `run_subagent` so successful scout briefs in a team run can be stored in the team artifact store under a stable per-scope key.

Requirements:

- stable key per canonical scope
- returned envelope includes both `artifact_ref` and `run_id`
- no append-only scout artifact keys in the team artifact store

### C. Add runtime promotion helper
Create a runtime helper that:

- checks the shared reusable-quality gate
- promotes a stored scout artifact into `project_context.shared_briefings`
- replaces older reusable context for the same canonical scope
- uses a bounded eviction policy when capacity is full

This should not weaken greenfield invariants and should only be activated under the fresh SWE-EVO policy.

Required eviction policy:

- never reject a reusable promoted scope solely because the table is full
- replace same-scope entries in place
- otherwise evict in this order:
  - stale entries first
  - then lowest-confidence / lowest-coverage entries
  - then least-recently-consumed reusable entries

This preserves stable ownership memory under broad worker fanout.

### D. Factor Atlas write helper out of `submit_atlas`
Extract the chunk-building and upsert logic into a shared helper so both posthook-driven Atlas writes and runtime persistence use the same semantics.

### E. Add deferred Atlas persistence path
After a foreground scout completes, enqueue a persistence task that:

- reuses the completed scout brief
- computes hashes and symbol ids
- upserts the corresponding Atlas chunk

This task should be non-LLM and should not spawn another scout.

## Dynamic Codebase Context Strategy
Agents need two different kinds of awareness in a changing repo:

- stable understanding of subsystem ownership and structure
- live awareness of what other workers have changed since that understanding was gathered

Atlas is only appropriate for the first category. The second category should come from live code-intelligence state.

### Recommended split
- Atlas: cross-run reusable briefs for stable subsystem context
- shared briefings: same-run reusable scout context
- ledger: recent file edit history with agent attribution
- arbiter: current conflict and hotspot awareness
- tree cache and symbol index: current file contents and symbol routing

### Proposed agent workflow in a changing codebase
1. Planner or worker identifies a target subsystem.
2. On resumed runs, try `atlas_lookup` first for stable structure context.
3. Before editing, inspect live change awareness for that scope:
   - recent changes under the target paths
   - edit hotspots
   - current owner or latest editing agent when available
4. If a same-run reusable scout exists, consume it from `shared_briefings`.
5. When files are edited, update ledger, arbiter generation, tree cache, and symbol index immediately.
6. If a previously gathered brief is now stale relative to ledger edits in scope, treat it as advisory only and re-scout or refresh selectively.

### Missing runtime capability
The main missing piece is a first-class "live scope status" helper that merges:

- latest scout/shared briefing for a scope
- recent ledger entries in that scope
- hotspot or active-edit signals from the arbiter
- symbol-index pointers for current definitions

That helper should be the default pre-edit context for developers and validators. Atlas alone cannot provide this because it is intentionally stale-tolerant and cross-run oriented.

### Snapshot coherence requirement
`ci_scope_status` must not assemble its response from unrelated point-in-time reads.

Required behavior:

- ledger, arbiter, tree cache, symbol index, and briefing state must be read under one coherent boundary
- the response must include a machine-checkable coherence token such as:
  - `coherence_token`
  - `scope_generation`
  - `reservation_generation`
  - `tree_generation`
- recommendation outputs are valid only for that returned boundary
- the pre-write and commit/apply rechecks must reject stale coherence tokens instead of trusting startup context

Without this, workers can receive torn state and make conflicting decisions from inconsistent snapshots.

### Required coordination primitive: scope refresh single-flight
Same-run refreshes for a canonical scope must deduplicate.

Required behavior:

- maintain a per-scope refresh lease keyed by canonical scope in shared coordination state, not only in local process memory
- the first worker that detects `refresh_scout` acquires the lease and launches the scout
- later workers targeting the same scope while the lease is active do not launch another scout
- later workers either:
  - wait for the existing refresh result when blocked on the same scope, or
  - continue on a narrowed untouched surface if `ci_scope_status` marks their files as `locally_touched`
- when the refresh completes, publish the new artifact/shared briefing and resolve all waiters from the same result
- if the refresh fails or times out, clear the lease and surface terminal evidence instead of retry looping silently

Lease correctness requirements:

- lease acquisition must use compare-and-swap or equivalent fencing semantics
- the lease record must include `lease_id`, `holder_run_id`, `acquired_at`, and `expires_at`
- refresh completion may publish results only if the finisher still owns the active lease
- resumed coordinators or sibling processes must observe the same lease record for the same canonical scope

This prevents stale-scope fanout from degenerating into duplicate scout storms.

### Proposed tool: `ci_scope_status`
The first implementation should be a read-only code-intelligence tool rather than a planner/runtime-only internal hook.

Suggested shape:

```json
{
  "scope": "pydantic/root_model.py",
  "owned_files": [
    "/repo/pydantic/root_model.py"
  ],
  "intended_write_files": [
    "/repo/pydantic/root_model.py"
  ],
  "coherence": {
    "coherence_token": "scope:pydantic/root_model.py:1842:991:2201",
    "scope_generation": 1842,
    "reservation_generation": 991,
    "tree_generation": 2201
  },
  "briefing": {
    "source": "shared_briefings",
    "artifact_ref": "scout:pydantic/root_model.py",
    "summary": "...",
    "snapshot_time": "2026-04-10T10:00:00Z",
    "reusable": true,
    "freshness": "fresh",
    "stale_due_to_recent_edits": false
  },
  "recent_changes": [
    {
      "file": "/repo/pydantic/root_model.py",
      "agent_id": "developer-2",
      "edit_type": "edit",
      "timestamp": 1712700000.0,
      "description": "adjust RootModel generic handling"
    }
  ],
  "hotspots": [
    {
      "file": "/repo/pydantic/root_model.py",
      "edit_count": 3
    }
  ],
  "active_edits": [
    {
      "file": "/repo/pydantic/root_model.py",
      "locked": true,
      "agent_id": "developer-2",
      "reservation_state": "editing",
      "acquired_at": 1712700000.0
    }
  ],
  "symbols": [
    {
      "name": "RootModel",
      "kind": "class",
      "file": "/repo/pydantic/root_model.py",
      "line": 42
    }
  ],
  "recommendation": {
    "action": "reuse_briefing",
    "reason": "shared briefing is fresh and no in-scope edits since snapshot"
  },
  "refresh": {
    "scope": "pydantic/root_model.py",
    "lease_state": "idle",
    "lease_holder": null
  }
}
```

Interpretation:

- `owned_files` / `intended_write_files`: the worker-targeted file set used to compute actionable guidance
- `coherence`: the read boundary that makes the response internally consistent and recheckable
- `briefing`: best structural context for the scope, preferring same-run shared context
- `recent_changes`: live ledger-backed edit history in scope
- `hotspots`: conflict-prone files from arbiter churn data
- `active_edits`: in-flight ownership/collision signal
- `symbols`: current symbol-index view of the code as it exists now
- `recommendation`: simple runtime guidance so agents do less policy reasoning ad hoc
- `refresh`: single-flight state for refresh coordination

### Pre-edit scope packet
`ci_scope_status` should not remain an optional manual tool call for execution lanes.

The runtime should build a compact pre-edit scope packet from it and inject that packet automatically into developer and validator startup context for their owned scope.

The packet should include only the minimum high-signal fields needed on the hot path:

- latest shared briefing summary and ref
- freshness grade for that briefing under same-run ledger activity
- recent in-scope edits with agent attribution
- active edit or contention warning
- top symbol pointers for the scope
- the coherence token needed for later rechecks
- a single recommendation action
- refresh-lease state when the recommendation is `refresh_scout`

This keeps worker prompts small while still providing just-in-time awareness.

Startup packets are advisory. Authoritative coordination must happen in three stages:

- startup packet injection for immediate awareness
- pre-write recheck immediately before acquiring or editing the worker's writable file set
- commit/apply recheck before publishing the worker's changes

The later stages must validate the returned coherence token, active reservations, and scope freshness for the actual file set being written.

### First implementation boundary
The first cut should stay conservative:

- use `shared_briefings` as the only briefing source for fresh SWE-EVO runs
- use ledger, arbiter, tree cache, and symbol index for live signals
- do not pull Atlas into `ci_scope_status` for fresh runs
- add Atlas fallback later only for resumed or retried runs

This keeps same-run coordination separate from cross-run memory.

### Same-run freshness gate
The runtime must treat same-run ledger edits as an immediate invalidation signal for previously gathered scout context.

Required behavior:

- compare briefing snapshot time against ledger entries in scope
- compute both:
  - `scope_generation`: latest generation for the canonical scope
  - `file_generations`: latest known generation per file in the scope
- if no newer in-scope edits exist, mark the briefing `fresh`
- if newer edits exist only in a small subset of the scoped files, mark the briefing `locally_touched`
- if newer edits imply ownership drift, broad path churn, or a newer scope generation than the briefing observed, mark the briefing `structurally_stale`
- downgrade recommendation from `reuse_briefing` to `refresh_scout` only for `structurally_stale`
- use `avoid_edit_conflict` or narrowed execution guidance for `locally_touched`

This is the critical safeguard that keeps same-run shared context trustworthy under parallel execution.

Deterministic first-cut heuristics:

- mark `fresh` when there are zero in-scope ledger entries newer than `snapshot_time`
- mark `locally_touched` when all newer edits are confined to known files within the scope and both of these hold:
  - edited files account for at most 20% of files covered by the briefing
  - no edited file is tagged by the scout as a scope root, ownership boundary, or subdivision candidate
- mark `structurally_stale` when any of these hold:
  - newer edits touch more than 20% of covered files
  - newer edits touch a scope root path, canonical boundary file, or scout-identified ownership anchor
  - a newer `scope_generation` exists than the briefing observed
  - the briefing has no file coverage metadata, so locality cannot be proven safely

These thresholds are intentionally conservative. The implementation may tune the percentages later, but the first cut must keep the recommendation stable for the same inputs.

Worker-targeting rule:

- freshness grading may be computed at canonical-scope level
- recommendation policy must additionally evaluate the worker's `owned_files` or `intended_write_files`
- a scope that is broadly `locally_touched` may still return `reuse_briefing` or narrowed guidance for an untouched worker file set
- a scope-level `avoid_edit_conflict` must only block a worker when the conflict intersects the worker's intended write set

### Active collision signal
Workers need collision awareness before they edit, not after a failed write attempt.

Required behavior:

- expose per-file active edit ownership from the arbiter through a read-only helper backed by explicit reservations
- surface hotspot files for the target scope
- if a target file is actively edited or strongly contended, return `avoid_edit_conflict`

The worker can then defer, narrow scope, or choose another owned surface instead of wasting cycles on a collision.

Required arbiter model upgrade:

- track per-file reservation records, not just aggregate lock/token counts
- each reservation record must include:
  - `file_path`
  - `agent_id`
  - `state` (`reserved`, `editing`, `committing`)
  - `acquired_at`
  - `expires_at`
- reservation records must also include a fencing token or monotonic reservation id
- workers must renew reservations while actively editing long-lived file sets
- reservation ownership must be queryable without taking the write lock
- stale reservations must expire deterministically so dead workers do not block the fleet
- writes and commit/apply operations must verify the active reservation token before proceeding, so an expired worker cannot continue as if it still owns the file

Hotspot counts alone are insufficient for just-in-time collision avoidance.

### Stable ownership memory
Reusable scout output should become the latest ownership memory for that canonical scope inside the run.

Required behavior:

- store promoted scout artifacts under stable per-scope keys
- replace older reusable scout context for the same scope
- prefer the latest reusable promoted scout when building the pre-edit scope packet

This keeps ownership memory bounded and predictable during wide fanout.

### Cheap runtime path
High parallelism only works if coordination stays cheap.

Required behavior:

- `ci_scope_status` is read-only
- scope-packet assembly does not spawn agents
- no LLM call occurs on the hot path unless the recommendation is `refresh_scout`
- Atlas persistence remains deferred and non-LLM
- repeated `refresh_scout` recommendations for the same canonical scope reuse the existing single-flight lease instead of spawning additional work
- Atlas does not launch scout during normal multi-agent execution

This prevents coordination cost from scaling linearly with worker fanout.

### Required code changes for `ci_scope_status`
1. Add a new read-only tool in `tools/ci_toolkit/query_tools.py`.
2. Export it from the CI toolkit so execution agents can call it directly.
3. Add a small shared-briefing resolver that returns the best briefing for a canonical scope without rendering prompt text.
4. Add a scope-refresh single-flight coordinator keyed by canonical scope.
5. Add a read-only arbiter helper for active in-flight edits backed by reservation records, because current hotspot APIs do not expose per-file active edit ownership cleanly.
6. Add scope and file generation tracking plus coherence tokens so freshness can be graded as `fresh`, `locally_touched`, or `structurally_stale` from one coherent read boundary.
7. Add tests for recommendation behavior, single-flight reuse across processes, scope filtering, freshness grading, reservation renewal, reservation expiry, and stale-token rejection.
8. Add a runtime hook that injects a compact pre-edit scope packet into developer and validator startup context automatically.
9. Add mandatory pre-write and commit/apply rechecks in the write-capable execution path before a worker edits or publishes changes.

### Recommendation policy for the first cut
- `reuse_briefing` when a shared briefing exists and no recent in-scope edits make it stale
- `avoid_edit_conflict` when the target file is actively edited or highly contended
- `refresh_scout` when there is no shared briefing or the live change stream likely invalidated it

This policy should remain intentionally simple until same-run scout promotion is in place.

Clarifications for the first cut:

- `reuse_briefing` applies only when freshness is `fresh`
- `avoid_edit_conflict` applies when reservation ownership shows an in-flight editor on the worker's target file set
- `refresh_scout` applies for missing briefings or `structurally_stale` briefings
- `locally_touched` should prefer narrowed execution or a wait-on-refresh path before escalating to a broad rescout
- all recommendation outputs must be computed against the worker's `owned_files` or `intended_write_files`, not only the canonical scope

### Recommended order of investment
1. Keep Atlas maintenance off for fresh SWE-EVO.
2. Promote reusable scout results into `shared_briefings`.
3. Add `ci_scope_status` so execution agents can query merged scope context directly.
4. Re-enable Atlas only after scout-complete persistence exists and no second scout pass is required.

## Durability and Resume

### Run-local behavior
Promoted scout artifacts and shared briefings should be available for the rest of the live run.

### Checkpoint behavior
Checkpoint snapshots already include:

- artifact store contents
- project context

So promoted scout artifacts and shared briefings will survive checkpoint/rollback within the same process lifecycle.

### Event-log resume
If process-loss resume must preserve promoted scout artifacts and shared briefings outside in-memory checkpoints, the runtime will need durable eventing for those promoted artifacts and shared-context mutations.

That is a separate extension and not required for the first cut.

## Latest-Per-Scope vs History
Prompt-facing artifact storage should use latest-per-scope semantics.

Reason:

- it matches byte-budget behavior
- it matches how shared context is consumed
- append-only history already exists in subagent run tracking

If historical scout comparison is needed later, it should live in the run/audit layer, not in prompt-facing artifacts.

## Non-Goals
- changing `share_briefing` into a trust/quality checker for all callers
- changing greenfield behavior
- globally disabling Atlas maintenance
- replacing Atlas with shared briefings
- making same-run shared context durable across process-loss resume in the first cut

## Phased Rollout

### Phase 1
- add fresh SWE-EVO scheduler policy
- split `artifact_ref` and `run_id` in the scout envelope
- save completed scout briefs under stable per-scope artifact keys

### Phase 2
- extract shared reusable-quality gate
- add runtime auto-promotion for reusable scout briefs under fresh SWE-EVO policy
- add same-run freshness gate for promoted briefings
- add bounded shared-briefing eviction policy

### Phase 3
- factor Atlas write helper out of `submit_atlas`
- add deferred non-LLM Atlas persistence from completed scout artifacts
- add `ci_scope_status`
- add scope-refresh single-flight coordination
- add arbiter-backed active collision reporting
- upgrade arbiter to expose per-file reservations with owner/state metadata
- add mandatory pre-write recheck before first write
- inject compact pre-edit scope packets into developer and validator startup context

Phase 3 release gate:

- do not ship `ci_scope_status` to production execution lanes unless all of the following land together:
  - scope-refresh single-flight coordination backed by shared coordination state with lease fencing
  - per-file reservation-backed active edit reporting
  - graded freshness with deterministic recommendation heuristics
  - coherent snapshot tokens across the merged live sources
  - mandatory pre-write and commit/apply rechecks for the actual worker file set
- remove Atlas-triggered scout launches from the benchmark/runtime path before enabling the Phase 3 coordination stack for fresh SWE-EVO runs
- a partial rollout is allowed only behind an internal development flag that does not affect benchmark or production team runs

This avoids exposing a false sense of coordination where workers see awareness data that is not yet authoritative enough to prevent races.

### Phase 4
- if needed, add durable eventing so promoted scout artifacts and shared-briefing mutations survive process-loss resume outside checkpoint snapshots

## Success Criteria
- fresh SWE-EVO runs stop launching Atlas-owned scout work on planner lookup misses
- same-run planners and subagents can reuse completed scout context through real artifact refs and shared briefings
- Atlas persistence no longer requires a second scout pass for the same scope
- artifact-byte growth remains bounded under scout fanout
- resumed and retried runs still benefit from Atlas reuse
- every developer and validator lane receives compact live scope awareness before editing
- same-run edits invalidate stale scout context quickly and deterministically
- workers can detect active collisions before wasting an edit attempt
- duplicate stale-scope refreshes collapse into one in-flight refresh per canonical scope
- workers can distinguish local file churn from whole-scope ownership drift
- workers receive recommendations that are specific to their intended write set
- latest-per-scope scout promotion never regresses due to out-of-order completion
- coordination decisions are made from coherent live snapshots rather than torn reads
- hot-path coordination stays read-only and non-LLM
- Atlas never duplicates foreground exploration by launching scout on the normal runtime path
