---
intent: write_allowed
terminal: false
hooks: [destructive_git_shell, destructive_shell]
---
Run a command in a managed PTY session inside the sandbox. If the command finishes within `yield_time_ms` you get the final result; otherwise the session keeps running in the background and you get `status: running` with a `command_session_id` — use `write_stdin` to feed input, poll for more output, or tear it down. Set `timeout` (seconds) to bound the run and `max_output_tokens` to cap returned output. Output is a merged PTY stream: everything (including the program's stderr) arrives in `stdout`, and the `stderr` field is always empty.
