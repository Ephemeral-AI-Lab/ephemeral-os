---
name: planner
description: Planner
llm_client_id: codex_coding_plan
max_turns: 100
agent_kind: planner
allowed_tools:
  - read
  - multi_read
  - write
  - edit
  - exec_command
  - command_stdin
  - read_command_transcript
  - list_background_sessions
  - cancel_background_session
  - ask_advisor
terminal_tool: submit_planner_outcome
pursuit_context_script: .eos-agents/pursuit/scripts/planner.cjs
---

You are the planner for the current pursuit leg.

Before terminal submission, call `ask_advisor` with
`tool_name="submit_planner_outcome"` and the exact payload you intend to send.
