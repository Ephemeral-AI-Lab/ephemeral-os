---
name: pursuit
type: pursuit
description: Delegate a multi-leg coding pursuit.
tools:
  - delegate_pursuit
args:
  planner: planner
  worker: worker
  store: .eos-agents/pursuit/pursuit.sqlite
  context_root: .eos-agents/pursuit/context
  default_max_attempts: 2
---
# pursuit

Long-running goal pursuit: a pursuit owns ordered legs; each leg runs a planner
then worker attempts against an attempt budget.

## Operating loop

Call `delegate_pursuit` to start one. It returns immediately with a `pursuit_<id>`
and registers a background task under that id. Watch it with `list_background_tasks`,
read the `pursuit_<id>/` context paths for progress, and `cancel_background_task`
on the `pursuit_<id>` to stop it. A settlement notification is published once when
the pursuit ends.

## Tools

### delegate_pursuit

Start a pursuit from a `pursuit_goal`, optionally with predefined `leg_goals`
(omit them for dynamic mode, where each successful leg declares the next goal).
The returned task id and title both embed `pursuit_<id>`; cancel a running pursuit
with `cancel_background_task` on that id.
