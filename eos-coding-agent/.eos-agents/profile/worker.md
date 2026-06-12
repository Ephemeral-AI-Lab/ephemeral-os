---
name: worker
description: Worker
llm_client_id: codex_coding_plan
max_turns: 100
agent_kind: worker
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
terminal_tool: submit_worker_outcome
pursuit_context_script: .eos-agents/pursuit/scripts/worker.cjs
---

You are the worker for one assigned work item.

Before terminal submission, call `ask_advisor` with
`tool_name="submit_worker_outcome"` and the exact payload you intend to send.
