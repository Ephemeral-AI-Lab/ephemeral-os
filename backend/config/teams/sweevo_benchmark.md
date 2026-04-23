---
name: sweevo_benchmark
entry_planner: root_planner
roster:
  planner:
    - root_planner
    - team_planner
  developer:
    - developer
  reviewer:
    - validator
  replanner:
    - team_replanner
  explorer:
    - scout
  parent_summarizer:
    - parent_summarizer
---
Default SWE-EVO benchmark team using the builtin root_planner/team_planner/developer/validator/replanner/scout/parent_summarizer agents.
