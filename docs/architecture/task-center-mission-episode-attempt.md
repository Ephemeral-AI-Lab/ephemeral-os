# Task Center Harness Migration - Phase Index

This migration is split into sequential implementation documents. Read and
implement them in order; each phase leaves a runnable intermediate state for
the next phase to build on.

## Phase documents

1. [Phase 00 - Target architecture](task-center-mission-episode-attempt/phase-00-target-architecture.md)
2. [Phase 01 - Mission request and harness graph model](task-center-mission-episode-attempt/phase-01-mission-episode-attempt-model.md)
3. [Phase 02 - Harness graph orchestrator lifecycle](task-center-mission-episode-attempt/phase-02-attempt-orchestrator-lifecycle.md)
4. [Phase 03 - Agent roles and tool gates](task-center-mission-episode-attempt/phase-03-agent-roles-and-tool-gates.md)
5. [Phase 04 - Mission spawning](task-center-mission-episode-attempt/phase-04-mission-spawning.md)
6. [Phase 05 - End-to-end workflows and cutover](task-center-mission-episode-attempt/phase-05-workflows-and-cutover.md)
7. [Phase 06 - Context engine](task-center-mission-episode-attempt/phase-06-context-engine.md)

## Overview documents

- [Mission segmentation and harness graph workflow](task-center-mission-episode-attempt/mission-episode-attempt-workflow-overview.md)
- [Context semantics migration plan](task-center-mission-episode-attempt/mission-episode-attempt-context-migration-plan.md)

## Implementation order

The dependency order is intentional:

1. Establish the target mental model before changing code.
2. Add durable complex-task, segment, and harness-graph state plus
   `MissionHandler` and `EpisodeManager` before graph execution
   uses it.
3. Move planner/generator/evaluator lifecycle decisions into
   `AttemptOrchestrator`.
4. Enforce role and terminal-tool policy against the new state model.
5. Add complex-task request spawning and final report delivery.
6. Validate complete workflows, migrate callers, and remove obsolete paths.
7. Add role-specific context composition, durable summaries, and close-report
   payloads on top of the migrated lifecycle model.

## Scope

The migration reshapes the harness around two context axes:

- Request origin: a `Mission` is created when an executor calls
  `request_mission_solution(goal)`.
- Segment progression: a `Episode` owns attempt budget for one vertical
  slice of the request. A passing graph with `continuation_goal` creates the
  next segment; a failed graph returns to `EpisodeManager`, which decides
  whether to spend attempt budget by launching another `Attempt` in the
  same segment.

The detailed context-composition system is specified separately in Phase 06.
