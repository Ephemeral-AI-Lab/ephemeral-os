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
  task_center_note_taker:
    - note_taker
  replanner:
    - team_replanner
  explorer:
    - scout
terminal_tools:
  note_taker:
    - submit_task_note
---
Default SWE-EVO benchmark team using the builtin root_planner/team_planner/developer/validator/note_taker/replanner/scout agents.
