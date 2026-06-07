---
intent: write_allowed
terminal: false
hooks: []
---
Send literal text to a running command session by `command_session_id` (for example `"y\n"`). `chars` must be non-empty. Sending exactly `"\x03"` (Ctrl-C) or exactly `"\x04"` (Ctrl-D) tears down the PTY session through command-session cancel; mixed control text is rejected. This tool does not poll or read prior output. Use `read_command_progress` to inspect output.
