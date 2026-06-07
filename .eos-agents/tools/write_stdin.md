---
intent: write_allowed
terminal: false
hooks: []
---
Send literal text to a running command session by `command_session_id` (for example `"y\n"`). `chars` must be non-empty. After writing, the tool waits up to `yield_time_ms` for output or completion and returns that result. Sending exactly `"\x03"` (Ctrl-C) or exactly `"\x04"` (Ctrl-D) tears down the PTY session through command-session cancel; mixed control text is rejected. Empty polling is not supported. Use `read_command_progress` to inspect output without writing stdin.
