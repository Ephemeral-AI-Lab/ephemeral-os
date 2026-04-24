# Planner / Root-Planner Playbook Guide

This guide governs the `team-root-planner-playbook` and
`team-planner-playbook` shape under `backend/config/skills`.

## Planning Shape

Team planning uses a tree of local DAGs. The root planner and child planners do
not need to fully explore every unresolved slice before submitting a plan.

```text
Caption: planners split boundaries, then delegate depth.

task set
  |-- trivial exact slice ----------------------> developer
  |-- broad / matrix / unresolved slice --------> expandable team_planner
  |-- completed producer needs same-payload check -> validator
```

Planner and replanner exploration is for routing, not exhaustive discovery.
They may fan out scouts when live evidence would change the current layer's
task split.

| Situation | Preferred action |
| --- | --- |
| Trivial or atomic, live-proven owner | Assign a `developer` task. |
| Expandable or ambiguous owner | Use scout evidence to split the boundary, then assign that slice as expandable `team_planner` work when depth allows. |
| Broad failure matrix in a mixed set | Launch boundary-focused scouts, then split broad slices by owner family and preserve atomic siblings. |
| Scout would change routing | Launch a small owner-family scout wave. |
| Scout would only chase details | Preserve uncertainty in the expandable task spec. |

## DAG Level Size

Each planner level should be easy to scan and schedule. Prefer a reasonable
number of sibling tasks at one level: enough to expose real parallelism, not so
many that each task is just a thin wrapper around one assertion or file.

| Boundary | Route |
| --- | --- |
| One owner, one mechanism, bounded verification | Atomic `developer` task. |
| One completed producer needs independent evidence | Atomic `validator` task with deps. |
| Several owners, a failure matrix, or unknown file boundary | Expandable `team_planner` task for that boundary. |
| Many tiny variants under one mechanism | One atomic task or one expandable task, not many sibling tasks. |
| Many unrelated owner families | Several siblings or expandable tasks, grouped by boundary. |

When a level feels crowded, run superficial scouts for owner families, group by
family or mechanism, and assign expandable tasks for broad clusters. When a
level has only one broad developer task, check whether it should be expandable.

```text
Caption: parallel DAG example. Independent producers feed one validation lane.

root planner
  |-- A developer: fix API serializer
  |-- B developer: fix CLI renderer
  `-- C validator: verify API + CLI output
        deps=[A, B]
```

```text
Caption: sequential DAG example. Later work depends on a concrete producer.

root planner
  |-- A developer: add compatibility guard
  `-- B developer: update adapter callsite
        deps=[A]
      `-- C validator: run adapter compatibility checks
            deps=[B]
```

```text
Caption: mixed DAG example. Exact work runs beside expandable planning.

root planner
  |-- A developer: patch exact config loader bug
  |-- B team_planner: decompose storage-engine matrix
  `-- C validator: verify config fix and storage outcomes
        deps=[A, B]
```

## Scout Fanout

Scout fanout is a planner judgment call. Use scouts to answer routing questions:
what owner boundary exists, what files or directories matter, and whether a
slice is atomic or expandable.

```text
Caption: scout orchestration starts from the planner's dependency hypothesis.

route question
  |
  |-- one likely file seam -----------> deep scout(["pkg/a.py"])
  |-- coupled files / call chain -----> deep scout(["pkg/a.py", "pkg/b.py"])
  |-- package/subsystem boundary -----> superficial scout(["pkg/subsystem"])
  |-- several unrelated candidates ---> parallel scouts, one per candidate group
  `-- too broad for deep inspection --> superficial directory scouts -> boundary split
```

### Choose What To Scan

Reason from the request, failing evidence, imports, symbols, directory names,
and likely runtime path before launching scouts.

| Signal | Scan target |
| --- | --- |
| Exact file, symbol, or small module is likely wrong | Single file. |
| Entry point and helper likely form one call chain | Multiple paths in one scout. |
| Adapter plus registry/config are coupled | Multiple paths in one scout. |
| Owner is likely a package, plugin family, or subsystem | Directory scout. |
| Several candidate owner families compete | Parallel scouts, one per candidate group. |
| Candidate groups are unrelated and numerous | Use superficial directory or multi-file scouts by boundary, then split into expandable tasks. |

The planner does not need proof before launching a scout; a reasonable dependency
guess is enough. Keep the guess visible in the scout prompt so the scout can
confirm, narrow, or disprove it.

### Shape Parallel Scouts

Launch scout workers in parallel when the groups are independent and the answer
will change this planner level's DAG.

```text
Caption: parallel scout wave. Independent candidate groups are scanned together.

parquet failure family  -> scout(["pkg/io/parquet"], objective="map owner")
csv failure family      -> scout(["pkg/io/csv"], objective="map owner")
cli output family       -> scout(["pkg/cli"], objective="map owner")
```

| Parallel shape | Use when |
| --- | --- |
| One scout per candidate owner family | The planner must choose separate developer or expandable lanes. |
| One scout with multiple paths | The paths probably form one mechanism and should produce one ownership answer. |
| One directory scout | Exact files are unknown, and a surface map is enough to route. |
| No scout | The uncertainty can be preserved in an expandable task spec. |

Avoid both extremes: one scout per failing test is too small-grained, and one
unrelated all-purpose scout is too broad.

### Set Scan Depth

Match depth to target shape. Single-file and small multi-file scouts can go
deeper; directory scouts should stay superficial unless the prompt names a
specific seam.

| Target shape | Expected depth |
| --- | --- |
| Single file | Deep: symbols, entry points, invariants, nearby diagnostics, and concrete owner seam. |
| Small coupled file set | Deep across the stated call chain or shared mechanism. |
| Directory/package | Superficial: map subdivisions, entry points, candidate owners, and gaps; do not inspect every file. |
| Broad subsystem | Superficial and boundary-focused; split into expandable tasks if still broad. |

### Write Objective-Based Prompts

Scout prompts should be objective based, not a single unified template. Name the
question, target paths, useful evidence, and desired routing output.

```text
Caption: objective-based scout prompt shape.

Objective: decide whether this package is one owner family or several expandable boundaries.
Targets: ["pkg/storage"]
Evidence: failing ids mention parquet and csv engines.
Return: owner seams, likely developer lanes, expandable parts, and gaps.
```

| Prompt part | Include |
| --- | --- |
| Objective | The routing decision the planner needs. |
| Targets | One file, coupled files, or directory chosen by dependency hypothesis. |
| Evidence | Failing ids, symbols, imports, commands, or hypotheses that shaped the target. |
| Depth | Deep for exact seams; superficial for directories or broad packages. |
| Return | Owner boundary, atomic vs expandable judgment, follow-up paths, and uncertainty. |

## Playbook Evolution

| Change style | Rule |
| --- | --- |
| Net size | Prefer negative net change. Add text only when it removes ambiguity or repeated failures. |
| Format | Prefer diagrams and tables with captions over long prose. |
| Constraints | Use light constraints and decision gates; reserve hard rules for runtime invariants or safety. |
| Logic | Express workflows as stage flows that an LLM can follow without backtracking. |
| References | Check companion `references/` files for drift before changing playbook behavior. Load references at stage entry; avoid reference map tables that encourage startup loading. |

## Review Checklist

| Check | Expected result |
| --- | --- |
| DAG split | Each level is reasonably sized and separates atomic developer lanes from expandable planning. |
| DAG examples | Parallel, sequential, and mixed patterns remain obvious from the guide. |
| Scout scope | Multi-path or directory scouts follow a dependency/owner hypothesis. |
| Simplification | The diff removes more ambiguity than it adds, preferably with negative net text. |
| Reference files | Companion `references/` files are checked, updated, split, or deleted when playbook behavior changes. |
| Reference timing | References are loaded at stage entry, not as a map at playbook load time. |
| Runtime contract | Terminal submission still uses `submit_plan(...)` for planner output. |
