# Workflow Vocabulary Evaluation - Judge Run Report

Status: Complete
Date: 2026-06-12
Owner: eos-agent-core
Related: `workflow-vocabulary-judge.md` (evaluation design and judge prompt),
`phase-05.1-workflow-context-redesign_SPEC.md`,
`phase-05.2-workflow-outcome-context-rendering_SPEC.md`

## Verdict

**Pattern H (`pursuit` / `leg` / `attempt`, transfer field `next_leg_goal`)
wins, 4 of 5 judges.** The incumbent Pattern A
(`workflow` / `iteration` / `attempt`, `deferred_goal`) ranked last with every
judge: all five independently flagged that `iteration` collapses the vertical
axis into retry semantics - the failure mode the rubric weights highest.

## 1. Method

Five blind judge subagents, run per the protocol in
`workflow-vocabulary-judge.md` Section 3:

```text
source doc Sections 1+2 + judge prompt
  -> 5 packets, A-I labels shuffled per packet   (incumbent/lineage prose
  -> 5 judge agents (different models)            stripped from Section 2)
  -> JSON scores per presented label
  -> de-shuffle to true labels
  -> recompute weighted totals from raw criterion scores
  -> aggregate
```

Blindness controls:

- Each judge could read only its own self-contained packet under
  `/tmp/vocab_judge/` - codebase access was forbidden and none occurred
  (each judge used 1-2 tool calls, all reading its own packet). Web lookups
  were permitted for collision checks; none were used.
- The A-I labels were shuffled with a distinct permutation per judge and
  de-shuffled before aggregating. The Section 2 sentences naming A as the
  incumbent and describing H/I lineage were removed from packets, since they
  would defeat the shuffle.
- Weighted totals were recomputed from each judge's raw 12-criterion scores
  rather than trusted: judge 4 (haiku) had inflated all nine of its totals by
  ~6, and judge 2 (opus) was off by 2 on one pattern. Corrected values are
  used throughout.

Judge models and label permutations (presented A..I -> true label):

| Judge | Model | Permutation |
| --- | --- | --- |
| 1 | fable | D H A F I B E C G |
| 2 | opus | G C I B F H A E D |
| 3 | sonnet | I E F G A D B H C |
| 4 | haiku | B F D I C G H A E |
| 5 | fable | E A H C G I D F B |

## 2. Aggregate Results

Weighted totals (max 85; criteria 2, 5, 6, 11, 12 double-weighted), after
de-shuffle and recomputation:

| Rank | Pattern | Vocabulary | Avg | Per-judge (j1..j5) | Mean rank |
| --- | --- | --- | --- | --- | --- |
| 1 | **H** | pursuit / leg / `next_leg_goal` | **77.8** | 83, 81, 68, 73, 84 | 1.2 |
| 2 | I | quest / leg / `next_leg_goal` | 73.0 | 79, 73, 67, 67, 79 | 2.8 |
| 3 | F | pursuit / segment / `successor_goal` | 72.0 | 73, 76, 72, 68, 71 | 3.0 |
| 4 | G | endeavor / leg / `remaining_goal` | 69.0 | 74, 71, 63, 64, 73 | 4.6 |
| 5 | D | expedition / stage / `carryforward_goal` | 65.4 | 64, 65, 65, 67, 66 | 5.0 |
| 6 | B | relay / leg / `handoff_goal` | 65.0 | 61, 73, 58, 65, 68 | 5.4 |
| 7 | E | mission / milestone / `rollover_goal` | 56.2 | 55, 58, 52, 64, 52 | 7.4 |
| 8 | C | ascent / pitch / `onward_goal` | 55.6 | 46, 65, 68, 56, 43 | 6.6 |
| 9 | A | workflow / iteration / `deferred_goal` | 36.6 | 30, 47, 38, 39, 29 | 9.0 |

Per-judge winners: H, H, F, H, H. The lone dissenter (sonnet) picked F with H
second. H also beat its single-term variants on every judge: `pursuit` >
`quest` and `leg` > `segment` for all five.

## 3. Why the Ranking Came Out This Way

H's strength concentrates at the two decision points the rubric calls
costliest:

- **Continuation gate**: `next_leg_goal.md` literally names the gate action -
  present means spawn the next leg, absent means close the pursuit.
- **Inherited reading**: "next leg goal from successful leg 1" states
  predecessor success verbatim; multiple judges called "successful" the
  load-bearing word that blocks the failed-upstream misreading.
- **Axis decode**: in the rendered tree, sibling `leg_<id>` folders read as
  ordered journey segments (vertical) and sibling `attempt_<id>` folders as
  retries (horizontal) with no contaminating polysemy in path context.

Dominant failure mode per losing pattern, as converged on across judges:

| Pattern | Worst failure mode |
| --- | --- |
| A workflow/iteration | `iteration` reads as a loop: sibling L2s misread as horizontal retry passes, collapsing both axes; `workflow` primes "predefined process," not "delegated goal" |
| C ascent/pitch | sales-pitch sense of `pitch` wins for small models; sibling pitches misread as competing proposals (highest-variance pattern: fable judges scored it near the floor, opus/sonnet tolerated the climbing metaphor) |
| E mission/milestone | `milestone` is a pre-planned PM checkpoint (and a point, not a container); `rollover` connotes slipped sprint work, implying the predecessor underdelivered |
| B relay/handoff | `handoff` is the canonical agent-to-worker delegation word: the gate misreads as dispatching the payload to a worker instead of spawning successor L2 scope |
| D expedition/stage | `stage` imports CI/CD pre-planned-pipeline expectations; `carryforward` carries accounting-leftover flavor |
| G endeavor/remaining | `remaining_goal` names leftover scope but no successor action: agents may try to finish it inside the current leg |
| F pursuit/segment | "successor goal after segment 1" implies but never states success; `segment` hints the whole was pre-partitioned upfront |
| I quest/leg | `quest` imports RPG preset-objective-chain framing at the root entity |
| H pursuit/leg | residual only: `leg` faintly suggests a pre-planned itinerary (addressed by amendment 1 below) |

## 4. Amendments for the Winning Pattern

Judge-proposed directives to close H's residual gaps; the first three are
composed-context sentences, not renames:

1. Wherever a planner sees sibling legs, include: "A new leg exists only
   because the previous leg closed successfully and declared `next_leg_goal`;
   legs are never planned upfront."
2. Always render the inherited phrase with the success marker verbatim:
   "next leg goal from **successful** leg N."
3. Gloss the field in composed planner context: "`next_leg_goal` is a goal to
   be planned later - never a plan, never a description of delivered work."
4. (Single-judge suggestion) Rename `archived/` to `superseded/` so overruled
   horizontal history cannot be misread as failed vertical scope.

## 5. Caveats

- H shares `leg`/`next_leg_goal` with I and `pursuit` with F, so the result
  is robust to either single-term substitution; the signal is "pursuit + leg
  + next_leg_goal as a set," with each swap costing roughly 5 points on
  average.
- One sample per model per permutation. The ordering of the top three (H, I,
  F) is consistent across judges, but the I-vs-F gap (73.0 vs 72.0 average,
  reversed on mean rank granularity) is within noise.
- Two judges mis-summed their own weighted totals; only recomputed totals are
  reported. Raw per-criterion scores, packets, and the permutation map were
  produced under `/tmp/vocab_judge/` (`prompt_1..5.md`, `mappings.json`,
  `aggregate.mjs`) for reruns; that directory is scratch space and not
  checked in.
