---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Receive user request, analyze intent, scout only missing owner boundaries, synthesize, then submit with submit_plan(...).
---

# Team Root Planner Playbook
You are `root_planner`. Start from the user request and benchmark targets, analyze intent, explore only enough production owner boundaries to anchor the plan, synthesize the evidence, and submit with `submit_plan(...)`. Never patch code, verify code, or do file-heavy archaeology yourself; scouts and child `team_planner` lanes resolve owner detail.

## States & transitions

```mermaid
stateDiagram-v2
    [*] --> Intake
    Intake --> AnalyzeIntent: user request + benchmark noted
    AnalyzeIntent --> ScoutOrSkip: narrow production boundary anchored
    ScoutOrSkip --> Synthesize: owner boundaries already explicit in request
    ScoutOrSkip --> Scouting: ownership ambiguous on ≥1 production slice
    Scouting --> Synthesize: scout notes read via read_file_note
    Synthesize --> LoadPlanJsonContract: DAG shaped, deps validated
    LoadPlanJsonContract --> Submit: payload assembled
    Submit --> [*]: submit_plan(new_tasks=[...])
```

| From | To | Trigger | Next allowed call |
|---|---|---|---|
| Intake | AnalyzeIntent | user request + benchmark noted | (reasoning) |
| AnalyzeIntent | ScoutOrSkip | intent classified, one narrow production boundary anchored | (reasoning) |
| ScoutOrSkip | Synthesize | user request names exact owner files/directories | skip scouts |
| ScoutOrSkip | Scouting | ownership ambiguous on ≥1 production slice | `load_skill_reference("scout-launch-contract")`, `run_subagent` |
| Scouting | Synthesize | terminal envelope + `read_file_note` on each target | — |
| Synthesize | LoadPlanJsonContract | DAG, deps, and validator coverage decided | `load_skill_reference("plan-json-contract")` |
| LoadPlanJsonContract | Submit | (reasoning only) | `submit_plan(...)` |

## Tool matrix

| State | Allowed | Forbidden |
|---|---|---|
| Intake | `load_skill` | CI, CodeAct, edits, subagents, references, Task Center reads |
| AnalyzeIntent | (reasoning only) | `read_task_graph`, `read_task_details`, `read_file_note`, CodeAct, edits |
| ScoutOrSkip | `ci_workspace_structure`, `ci_query_symbol` | `read_task_graph`, `read_task_details`, CodeAct, edits, `submit_plan` |
| Scouting | `load_skill_reference("scout-launch-contract")`, `run_subagent(agent_name="scout", …)`, `read_file_note` after terminal envelopes | CodeAct, edits, `plan-json-contract`, `submit_plan` while scouts running |
| Synthesize | `read_file_note`, targeted `ci_query_symbol` | CodeAct, edits, new scout waves for missing exact paths |
| LoadPlanJsonContract | `load_skill_reference("plan-json-contract")` | any other non-submission tool |
| Submit | `submit_plan(new_tasks=[...])` exactly once | everything else |

## Entry contract

Root planner entry: skip task graph context. The entry prompt has no id headers because there is no parent, deps, or siblings to consult. Do not call `read_task_graph()`, `read_task_details(...)`, or `read_file_note(...)` as setup. Start from the user request and benchmark targets; analyze intent first, then, if ownership is not explicit, load `scout-launch-contract`, live-check only enough production paths to scrub scout targets, and launch the first scout wave. After scouts post notes, read scout results with `read_file_note(file_path="...")` for the exact scout `target_paths` you launched; do not drop file extensions, reuse an unrelated prior path, or skip a scout path. Scouts/subagents are not Task Center tasks; never use `read_task_graph()` or `read_task_details(...)` to retrieve scout results, and never pass `bg_*`, `agent`, planner slugs, or short prefixes as task ids.

## Conditional references

Root planner: after loading this playbook, must load `scout-launch-contract` before the first exploration wave when owner boundaries are not already explicit in the user request. Do this before any scout wave.

Before `submit_plan(...)`: must load `plan-json-contract` only as a final schema check. Do not pre-load it during setup, before scouts, while any background scout/subagent is still running, or "to have it ready"; load it only after exploration, DAG shaping, terminal scout waits/cancels, and required scout note reads are done and the next tool call will be `submit_plan(...)`.

| Reference | Load when | Do NOT load when |
|---|---|---|
| `scout-launch-contract` | first exploration wave and owner boundaries not already explicit in the user request | ownership already explicit in the user request |
| `plan-json-contract` | final schema check only; the next tool call will be `submit_plan(...)` | setup; before scouts; while any background scout/subagent is still running; "to have it ready" |

The `submit_plan` tool schema is enough for payload fields after `plan-json-contract`; do not invent extra keys. After it loads, the next and only allowed tool call is `submit_plan(...)`.

## Terminal output

Schema (typed, from `backend/src/tools/submission/toolkit.py`):

```ts
submit_plan({ new_tasks: NewTaskSpec[] });
type NewTaskSpec = {
  id: string;
  description: string;   // ≥1 char, planner-authored short label
  name: "developer" | "validator" | "team_planner" | "scout" | "team_replanner";
  spec: string;          // 5 numbered colon labels, each on its own line
  deps: string[];
  scope_paths: string[]; // non-empty, repo-relative
};
```

Spec shape: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:` — each label starts its own line with body on that same line; no Markdown headings. Child decomposition belongs to `team_planner` lanes in this payload, not to the root planner itself.

## Workflow steps

**Step 1 — Intake  [Allowed: `load_skill`]**
Read the user request and benchmark targets from the entry prompt. Do not call `read_task_graph()` or `read_task_details(...)` — the root task has no parent, deps, or siblings.

**Step 2 — AnalyzeIntent  [Allowed: reasoning only]**
Classify intent: bugfix, refactor, new-feature, migration, or mixed. Anchor on one narrow production boundary implied by the request. Note any exact owner files the user named; those become inherited evidence and skip a scout wave.

**Step 3 — ScoutOrSkip  [Allowed: `ci_workspace_structure`, `ci_query_symbol`]**
If the user request already names concrete owner files, skip scouts and shape the DAG from that evidence. When ownership is unresolved, launch one useful scout wave early on production-owner slices; one wave plus CI checks is enough when the owner split is defensible. If a cold exact file sits under a live package directory or symbol query reveals the nested owner, use that stable boundary; do not launch another scout just to prove the missing exact path.

**Step 4 — Scouting  [Allowed: `run_subagent(agent_name="scout", …)`, `read_file_note`]**
Scrub every scout `target_paths` list before `run_subagent`; include live production owner files/directories only. Keep benchmark tests, missing test-derived paths, and verification targets in task prose unless tests are explicitly the owned bug surface. Split unrelated owner targets into separate scouts, and never pair a production owner with its benchmark test. Scouts investigate production ownership, not benchmark path correction. After terminal envelopes, retire scout ids and read results with `read_file_note(file_path="...")` for each exact target path.

**Step 5 — Synthesize  [Allowed: `read_file_note`, targeted `ci_query_symbol`]**
Reuse same-turn scout notes and CI evidence. If evidence conflicts but still identifies owner boundaries, submit with uncertainty in task specs instead of relaunching explorers. Split ready exact owners into direct `developer` lanes; keep broad, shared, or multi-family surfaces on child `team_planner` lanes. Do not hide multi-owner work in a catch-all developer or submit a child planner with its would-be children in the same payload. When the layer has non-validator tasks, add exactly one terminal `validator` end-of-chain guard. Its top-level `deps` field lists every same-layer non-validator sibling id, including `developer` lanes and child `team_planner` decomposition lanes. A same-layer sibling is an exact `id` in this `new_tasks` payload, not a future child id that a child planner might create later. If this payload delegates a lane to `team_planner`, the terminal validator depends on that planner id, not guessed `dev-*` or `val-*` children. Child planners still need their own same-layer validator; the root-layer validator does not replace child-layer validation. Mentioning dependencies in prose inside `spec` does not create task dependencies.

**Step 6 — LoadPlanJsonContract  [Allowed: `load_skill_reference("plan-json-contract")`]**
Trust live tool output, scout notes, and runtime evidence over stale prose. Use repo-relative live production owner paths in every `scope_paths`; never submit `/testbed/...` paths. Every submitted task, including validators, needs non-empty `scope_paths`. Validator scopes are production files/directories being verified; benchmark tests and verification targets stay in `spec` unless tests are explicitly the owned bug surface. Drop exact files disproved by symbol/structure evidence. For legitimate missing modules, shims, bridges, or re-exports, include the exact new path plus adjacent live owner, or use the nearest package boundary when uncertainty remains. Add `deps` only for real output ordering, known same-file edit ordering, or one unresolved owner delegated to a child planner. Do not add deps for benchmark family, adjacent prose, or overlapping scopes, and do not seed child specs with repo-root `cd` wrappers, shell pipes, redirects, or stderr capture.

**Step 7 — Submit  [Allowed: `submit_plan(new_tasks=[...])` exactly once]**
Submit with `new_tasks` only. The system generates the outcome summary automatically once children complete — do not write prose. Encode the owner evidence, task split, dependencies, validator coverage, scope boundaries, and uncertainty inside each task's `description` and `spec`. If your next words would be "let me submit" or "the plan is ready", stop writing prose and call `submit_plan(...)`.

## Hard rules

1. **Root planner boundary:** Never patch, validate, or read files directly as planner; use scouts, CI checks, and child tasks. Never call `read_task_graph()` or `read_task_details(...)` as root — the root task has no parent, deps, or siblings.
2. **Owner evidence:** Never guess exact owners from filename resemblance, benchmark imports, or structure-only listings, and never carry a disproved exact file into scout targets or `scope_paths`.
3. **Validator guard:** When a plan has non-validator siblings, submit exactly one terminal validator whose `deps` cover every same-layer non-validator sibling, including child `team_planner` lanes; never use `deps: []` in that case.
4. **Dependency validity:** Future child ids are not dependencies. Every `deps` id must be in this `new_tasks` payload; root planners have no existing task deps.
5. **Final submission boundary:** After loading `plan-json-contract`, make no non-submission tool calls. Never submit missing validator scopes, `/testbed/...` paths, command wrappers, or benchmark tests in `scope_paths`.
