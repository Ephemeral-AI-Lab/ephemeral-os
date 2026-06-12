# Planner and Worker Initial Messages for `pursuit / leg`

Status: Draft note
Date: 2026-06-12
Owner: eos-agent-core
Related:
- `leg-goal-without-focus-debate.md`
- `workflow-vocabulary-judge-report.md`
- `phase-05.2-workflow-outcome-context-rendering_SPEC.md`

## Intent

Define the initial messages produced by workflow context scripts for planner and
worker launches after adopting:

- `pursuit / leg / attempt`,
- leg-owned `leg_goal`,
- `next_leg_goal` as the successor transfer field,
- `superseded/` for attempts displaced by a later `leg_goal`.

Each launch should receive:

1. context: the path-addressed facts relevant to this launch,
2. directive: what the agent must do with those facts.

The messages are the launch's complete ordered initial user messages. Runtime
must not append hidden workflow instructions around them.

## Context Path Universe Used by Prompts

Prompt construction follows the rendered path universe:

```text
pursuit_<id>/
  goal.md
  outcome.md

  leg_<id>/
    leg_goal.md
    next_leg_goal.md
    outcome.md

    attempt_<id>/
      plan_summary.md
      fail_reason.md
      outcome.md
      work_item_<id>/
        description.md
        spec.md
        summary.md
        outcome.md

    superseded/
      attempt_<id>/
        leg_goal.md
        next_leg_goal.md
        plan_summary.md
        fail_reason.md
        outcome.md
        work_item_<id>/
          description.md
          spec.md
          summary.md
          outcome.md
```

Construction rules:

- Include file paths as headings. The agent should be able to connect every
  quoted fact to its projected path.
- Omit absent files rather than rendering empty placeholders, except where the
  absence itself is the instruction-relevant fact.
- `next_leg_goal.md` is future scope. It is not work delivered by the current
  leg.
- `Success` means the current effective `leg_goal` was achieved in full.
- A new leg exists only because the previous leg closed successfully and
  declared `next_leg_goal`; legs are never planned upfront.
- `superseded/` attempts are displaced history. Do not include their full
  content by default; list their paths only unless a refocus-specific recovery
  prompt needs their details.

## Planner Initial Messages

Planner launches should receive three user messages.

### Message 1: Pursuit and Current Leg Context

```text
# Pursuit and leg context

You are planning attempt_<attempt_id> in leg_<leg_id> of pursuit_<pursuit_id>.

## pursuit_<pursuit_id>/goal.md
<pursuit goal>

## leg_<leg_id>/leg_goal.md
<effective leg goal>

Provenance: <inherited from pursuit goal | inherited from successful leg_<n> next_leg_goal | declared by attempt_<id> planner>

## leg_<leg_id>/next_leg_goal.md
<standing next_leg_goal, if present>

If `leg_<leg_id>/next_leg_goal.md` is absent, no successor leg is currently
declared.
```

Context source:

| Section | Source |
| --- | --- |
| `pursuit_<id>/goal.md` | root pursuit goal |
| `leg_<id>/leg_goal.md` | effective leg goal: latest declared `leg_goal`, otherwise base leg goal |
| provenance line | leg goal source metadata rendered in `leg_goal.md` or DTO |
| `leg_<id>/next_leg_goal.md` | effective standing `next_leg_goal`, if any |

### Message 2: Current Leg Evidence

```text
# Current leg evidence

A new leg exists only because the previous leg closed successfully and declared
`next_leg_goal`; legs are never planned upfront.

## Closed predecessor legs
- leg_<id> [Success]: <first line of outcome.md>
- leg_<id> [Failed]: <first line of outcome.md>

## Failed attempts under the current leg_goal
### leg_<leg_id>/attempt_<attempt_id>/
plan_summary: <plan_summary.md>
fail_reason: <fail_reason.md>
work_items:
- work_item_<id> [Failed]: <summary.md>
  outcome: <outcome.md>

## Superseded attempts
- leg_<leg_id>/superseded/attempt_<id>/
```

Context source:

| Section | Include |
| --- | --- |
| Closed predecessor legs | status and first line of each predecessor `outcome.md` |
| Failed attempts under current `leg_goal` | attempts in this leg with `is_consistent_with_leg_goal = true` and `status = Failed` |
| Work item details in failed attempts | `description.md`, `summary.md`, and `outcome.md`; include `spec.md` only when useful for retry diagnosis |
| Superseded attempts | paths only by default |

Initial-leg planners often have no failed attempts and no predecessor legs. In
that case render:

```text
## Closed predecessor legs
(none)

## Failed attempts under the current leg_goal
(none)

## Superseded attempts
(none)
```

### Message 3: Planner Directive

```text
# Planner task

Plan work items that complete the full current `leg_goal`.

Success means the full effective `leg_goal` is achieved. If you cannot achieve
the full `leg_goal` in this leg, submit a narrowed `leg_goal` and put the
remainder in `next_leg_goal`.

Payload rules:
- Omit `leg_goal` when you accept the current `leg_goal`.
- Include `leg_goal` only to refocus this leg; refocus supersedes prior live
  attempts and resets the standing `next_leg_goal`.
- Include `next_leg_goal` only for work that should become a future leg after
  this leg succeeds.
- `next_leg_goal` is a goal to be planned later; it is never a plan and never a
  description of work delivered by this leg.
- Define worker-executable `work_items` for this attempt.
- Submit with `submit_planner_outcome` as the final action.
```

If the current leg already has a standing `next_leg_goal`, omission keeps it.
Clearing an existing `next_leg_goal` without refocus should require an explicit
future payload field; do not encode that ambiguity in prompt wording.

## Worker Initial Messages

Worker launches should receive three user messages.

### Message 1: Leg and Attempt Context

```text
# Work context

You are executing work_item_<work_item_id> in attempt_<attempt_id>,
leg_<leg_id>, pursuit_<pursuit_id>.

## leg_<leg_id>/leg_goal.md
<effective leg goal>

## leg_<leg_id>/next_leg_goal.md
<standing next_leg_goal, if present>

`next_leg_goal` is future scope. Do not execute it unless this work item's
`spec.md` explicitly asks you to prepare an input for it.

## leg_<leg_id>/attempt_<attempt_id>/plan_summary.md
<planner summary>
```

Context source:

| Section | Source |
| --- | --- |
| `leg_goal.md` | effective leg goal |
| `next_leg_goal.md` | standing successor goal, if present |
| `plan_summary.md` | owning attempt planner summary |

Workers do not need closed predecessor leg details by default. They should see
only context needed to complete their assigned work item and direct dependency
outcomes.

### Message 2: Assigned Work and Dependencies

```text
# Assigned work item

## leg_<leg_id>/attempt_<attempt_id>/work_item_<work_item_id>/description.md
<description>

## leg_<leg_id>/attempt_<attempt_id>/work_item_<work_item_id>/spec.md
<spec>

## Dependency outcomes
- leg_<leg_id>/attempt_<attempt_id>/work_item_<dependency_id> [Success]: <summary.md>
  outcome: <outcome.md>
```

Context source:

| Section | Include |
| --- | --- |
| Assigned description | this work item's `description.md` |
| Assigned spec | this work item's `spec.md` |
| Dependency outcomes | direct `needs` only; include `summary.md` and `outcome.md` |

If there are no dependencies, render:

```text
## Dependency outcomes
(none)
```

### Message 3: Worker Directive

```text
# Worker task

Complete only this work item's `spec.md`, using dependency outcomes as inputs.

Stay inside the current `leg_goal` and this work item. Do not plan new legs,
change `leg_goal`, or decide `next_leg_goal`.

When done, call `submit_worker_outcome`:
- `is_pass=true` only if the work item is complete and usable.
- `is_pass=false` if blocked, unsafe, impossible, or incomplete.
- `summary` should be concise.
- `outcome` should contain the concrete result or the failure details needed by
  a retry planner.
```

## Message Builder Shape

Context scripts should emit the runtime `Message` content-block shape:

```json
{
  "initial_messages": [
    {
      "role": "user",
      "content": [{ "type": "text", "text": "<message 1>" }]
    },
    {
      "role": "user",
      "content": [{ "type": "text", "text": "<message 2>" }]
    },
    {
      "role": "user",
      "content": [{ "type": "text", "text": "<message 3>" }]
    }
  ]
}
```

The planner and worker scripts may share formatting helpers, but role-specific
selection should stay separate:

| Role | Context breadth | Directive |
| --- | --- | --- |
| Planner | pursuit goal, current leg goal, standing successor, predecessor outcomes, failed current-goal attempts | plan or re-plan work items for the whole `leg_goal`; optionally refocus or declare successor |
| Worker | current leg goal, owning plan summary, assigned work item, direct dependencies | complete only the assigned work item and submit its result |
