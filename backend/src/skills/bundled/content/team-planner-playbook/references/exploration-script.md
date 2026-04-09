# Exploration Script

Use this reference when the planner needs to understand a subsystem before assigning execution work.

## Goal

Produce a structural ownership map first, then assign developer and validator work against disjoint slices. Do not flatten broad exploration into one parent planner turn.

## Script

1. Seed the search space with live CI.
   Read the failing test, locate one or two candidate symbols, and identify the candidate paths or directories.

2. Decide whether this is already execution-sized.
   If there is one obvious owned file cluster and one direct validation target, dispatch workers.
   If there are multiple plausible owners, a directory-sized slice, or a large file with many relevant regions, switch to exploration.

3. Launch a bounded scout.
   Call `run_subagent(agent_name="scout", input={"target_paths": [...]})` with concrete paths only.
   Give the scout the smallest slice that can still answer the ownership question.

4. Read the scout brief and classify the result.
   `scope_coverage >= 0.9` with a clear ownership map:
   Plan workers immediately, and promote the brief if later work will overlap it.

   `0.0 < scope_coverage < 0.7` with `suggested_subdivisions`:
   Fan out child scouts on those disjoint subdivisions, or hand the slice to a child planner if you cannot close it in this turn.

   `scope_coverage == 0.0` and no subdivisions:
   Treat the area as genuinely empty and revise the target paths.

5. Recurse only on narrower owned slices.
   Parent planner owns only the broad map.
   Each child scout owns one explicit subdivision.
   Each child planner owns one named sub-slice or one large-file region.
   Never reopen sibling branches from a child.

6. Convert large-file exploration into child planning.
   If one file contains too many relevant regions, symbols, or branches for the current level, emit an expandable `team_planner` item that names:
   - the owned file
   - the owned region, symbol subset, or question cluster
   - explicit out-of-scope neighbors

7. Stop conditions.
   Stop exploring when you can name:
   - the owned production slice
   - the likely fix or investigation question
   - the direct validation command or test target

## Heuristics

- Prefer one scout over many serial planner `ci_read_file` calls when structure is still unclear.
- Prefer atlas reuse only when the cached brief already answers the decomposition question.
- Prefer child planners over extra parent reads when a large file needs region-level ownership.
- Prefer disjoint fanout over overlapping scouts.

## Anti-patterns

- Reading five windows of the same large file from the parent planner just to decide who should own it
- Using `developer` as a discovery worker
- Re-scouting a path already covered by shared context or a sibling scout
- Emitting a one-child recursive planner chain that simply restates the same broad slice

## Example: multi-file exploration

1. Read the failing test.
2. Use CI to locate two candidate modules.
3. Scout the containing subsystem paths.
4. If the scout finds three disjoint ownership branches, fan out three child scouts or three child planner items.
5. Assign developers only after each branch has a clear owned slice.

## Example: one large file

1. Read the failing test and locate the target file.
2. If the file has several relevant regions, do not keep paging it from the root planner.
3. Emit an expandable child planner for one named region such as `"schema generation discriminator handling"` and exclude adjacent regions.
4. Let the child planner run scouts or CI only within that owned region.
