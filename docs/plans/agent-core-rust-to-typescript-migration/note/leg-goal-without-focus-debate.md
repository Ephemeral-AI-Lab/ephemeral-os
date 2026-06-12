# Debate: Remove Focus and Use Leg-Owned `leg_goal`

Status: Concluded - proposed model adopted with three additions (see Conclusion)
Date: 2026-06-12
Owner: eos-agent-core
Related:
- `phase-05.1-workflow-context-redesign_SPEC.md`
- `phase-05.2-workflow-outcome-context-rendering_SPEC.md`
- `note/workflow-vocabulary-judge-report.md`

## Question

Should the `pursuit / leg / next_leg_goal` vocabulary remove the separate
planner-declared `focus` concept and instead make `leg_goal` the leg's
established target?

Proposed meaning:

- first leg `leg_goal` = the pursuit `goal`,
- next leg `leg_goal` = the previous successful leg's `next_leg_goal`,
- a planner does not have to submit a new `leg_goal` if it agrees with the
  existing leg goal,
- a planner submits `leg_goal` only when it intentionally refocuses the current
  leg.

This note uses `leg_goal`; any `leg_gal` spelling in discussion should be
treated as a typo.

## Current Model

The current Phase 05.1/05.2 model has two distinct concepts:

| Concept | Current term | Source |
| --- | --- | --- |
| Inferred objective for the current vertical unit | iteration goal | workflow goal for the first iteration; previous `deferred_goal` afterwards |
| Planner's chosen slice for this iteration | `iteration_focus` / `focus.md` | required in the first planner submission |
| Optional successor objective | `deferred_goal` / `deferred_goal.md` | valid only beside `iteration_focus` |

Important current invariant:

```text
If the iteration has no standing focus, the first valid planner submission must
declare `iteration_focus`.
```

That invariant makes every attempt traceable to a planner-authored focus.
Refocus is represented by a later declaration, and older attempts move under
`archived/`.

## Proposed Model

The proposed model makes the vertical unit itself own the current target:

| Concept | Proposed term | Source |
| --- | --- | --- |
| Whole delegated objective | pursuit `goal` | delegation input |
| Current leg target | `leg_goal` / `leg_goal.md` | set at leg creation; may be replaced by refocus |
| Optional successor objective | `next_leg_goal` / `next_leg_goal.md` | planner submission; promotes only after leg success |
| Retry direction change | new `leg_goal` | optional planner submission on retry/refocus |

Under this model, `leg_goal` is not a first-planner declaration requirement.
It already exists before the first planner run starts.

Creation rule:

```text
first leg:
  leg_goal = pursuit.goal

next leg:
  leg_goal = previous successful leg.next_leg_goal
```

Planner submission rule:

```text
leg_goal omitted:
  keep the current leg_goal

leg_goal present:
  replace the current leg_goal and mark older attempts in the leg as superseded

next_leg_goal omitted:
  no successor is declared; successful leg closes the pursuit

next_leg_goal present:
  if the leg succeeds, create the next leg with that value as its leg_goal
```

## Why This May Be Better

Convenience:

- The initial planner does not have to restate the obvious case where it accepts
  the leg's already-established goal.
- The common payload becomes smaller: `summary` plus `work_items`, with
  `next_leg_goal` only when continuation is needed.
- `leg_goal.md` reads naturally as the goal of this leg, not as a narrower
  "focus" that the planner had to invent before doing any work.

Vocabulary consistency:

- `leg_goal` pairs cleanly with `next_leg_goal`.
- The judge-selected gate remains literal: `next_leg_goal` creates the next
  leg only after the current leg succeeds.
- The model no longer has to explain why a leg already has a goal but still
  requires a separate first `focus`.

State shape:

- Each leg has a durable, directly readable `leg_goal.md` from T0.
- The first leg and every successor leg have the same local shape.
- Refocus remains possible but becomes an explicit exceptional action rather
  than a required first declaration.

## Risks

Loss of explicit planner commitment:

- The old model forced the planner to say what it was committing to complete in
  this iteration before work items existed.
- With leg-owned `leg_goal`, a planner can implicitly accept the inherited goal.
  That is convenient, but less explicit.

Potential broad first legs:

- If the pursuit goal is large and the first planner omits `next_leg_goal`, the
  planner may attempt to plan too much in one leg.
- This is mitigated by planner prompt guidance: it should use `next_leg_goal`
  when it intentionally leaves successor scope.

Refocus semantics must stay sharp:

- A submitted `leg_goal` must mean "replace the current leg goal," not "describe
  the same goal in different words."
- If this is too easy to misuse, prompts should tell planners to omit
  `leg_goal` unless they are intentionally changing direction.

## Consequences for Validation

Current validation:

```text
if iteration has no focus and payload.iteration_focus is absent:
  reject

if payload.deferred_goal is present and payload.iteration_focus is absent:
  reject
```

Proposed validation:

```text
if payload.leg_goal is absent:
  keep the current leg_goal

if payload.leg_goal is present:
  refocus current leg and supersede older attempts

if payload.next_leg_goal is present:
  accept it even when payload.leg_goal is absent
```

This means `next_leg_goal` no longer depends on a sibling `leg_goal` field.
The leg already has a current goal, so a planner can declare only the successor
while planning against the existing `leg_goal`.

## Consequences for Context Paths

Proposed path universe:

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

Notes:

- `focus.md` disappears.
- `deferred_goal.md` becomes `next_leg_goal.md`.
- `leg_goal.md` appears when the leg is created, not only after planner
  submission.
- A superseded attempt carries declaration files only when that attempt's
  planner submitted a replacement `leg_goal` and/or `next_leg_goal`.

## Consequences for DTOs and Methods

Likely public/schema renames:

| Current | Proposed |
| --- | --- |
| `iteration_focus` | `leg_goal` |
| `deferred_goal` | `next_leg_goal` |
| `declared_focus` | `declared_leg_goal` |
| `declared_deferred_goal` | `declared_next_leg_goal` |
| `is_consistent_with_iteration_focus` | `is_consistent_with_leg_goal` |
| `effectiveDeferredGoal` | `effectiveNextLegGoal` |

Potential implementation nuance:

- If `leg_goal` is set at leg creation, it may belong on the leg row or remain
  a derived value from the pursuit goal / previous successful `next_leg_goal`.
- If refocus can replace `leg_goal`, the replacement needs a durable
  append-only declaration source so superseded attempts remain derivable.
- A simple compromise is:
  - keep base leg goal derived from pursuit/previous successor,
  - store optional planner declarations on plan rows,
  - effective `leg_goal` = latest declared `leg_goal`, otherwise base leg goal.

## Recommendation to Debate

Prefer the proposed model if convenience and vocabulary clarity are higher
priority than forcing every first planner to state an explicit focus.

The stronger version is:

```text
`leg_goal` is the current effective goal of the leg.
The first planner may omit it.
Submitting `leg_goal` means refocus.
Submitting `next_leg_goal` means create a successor leg only after this leg
closes successfully.
```

Keep the first-planner-required focus model only if the system must preserve the
old invariant that no work item can exist until a planner has explicitly
declared a narrower commitment than the inherited goal.

## Conclusion

Decision: adopt the proposed model. The vocabulary is
`pursuit / leg / attempt` with leg-owned `leg_goal` and the `next_leg_goal`
transfer field; the separate `focus` concept is removed.

Why this is the right simplification:

- `focus` was the hardest concept for a naive agent to decode ("attempts
  target the leg's goal, but the focus is one planner's framing of it").
  Collapsing to one replaceable `leg_goal` makes the vocabulary fully literal,
  which is the property the judge run rewarded in Pattern H.
- Path legibility improves: every entity folder carries its own goal file from
  T0, `leg_goal` / `next_leg_goal` form an obvious pair, and `next_leg_goal`
  loses the awkward sibling-field dependency in validation.
- One submission rule covers both initial narrowing and retry overrule
  (supersede of an empty attempt set is vacuous), and endorsement-by-omission
  removes restatement ceremony for legs 2..N along with the paraphrase-drift
  hazard of a restated focus diverging from the inherited goal.
- The change is orthogonal to the vocabulary verdict: all judged candidates
  shared the focus concept, so removing it does not undermine the H ranking.

The adopted model is the "stronger version" above plus three additions:

1. **Success invariant.** `Success` asserts the leg's effective `leg_goal` was
   achieved in full - never "`leg_goal` minus `next_leg_goal`". The planner
   prompt must carry: "If you cannot achieve the full `leg_goal` in this leg,
   submit a narrowed `leg_goal` and defer the remainder as `next_leg_goal`."
   Validation stays loose (no `next_leg_goal`-requires-`leg_goal` rule),
   because deferral without narrowing is legitimate when the planner completes
   the full `leg_goal` and declares discovered scope; validation cannot
   distinguish that from slice-deferral. Tighten to a validation rule only if
   E2E runs show planners deferring slices without narrowing.
2. **Refocus resets the standing `next_leg_goal`.** The validation table above
   omits this. When a submitted `leg_goal` replaces the current one, the
   standing `next_leg_goal` declared under the superseded direction resets,
   exactly as the old model reset the transfer payload on overrule; a deferred
   remainder of an abandoned direction is stale. The superseded-attempt file
   shape already supports this (declarations ride the attempt that made them).
3. **Provenance line in `leg_goal.md`.** Once `leg_goal` is mutable, a cold
   reader cannot tell inherited-from-predecessor from rewritten-by-overrule
   without diffing against `superseded/`. Render one provenance line in the
   file body ("inherited from pursuit goal" / "inherited from leg_1
   next_leg_goal" / "declared by attempt_3 planner"), keeping the
   every-fact-one-file rule.

Naming note: `leg_<id>/leg_goal.md` is kept over a uniform per-entity
`goal.md` for DTO field parity (`leg_goal` field <-> `leg_goal.md` file) and
self-describing flattened search hits; uniformity in the rendered tree was the
weaker benefit.
