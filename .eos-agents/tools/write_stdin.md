---
intent: write_allowed
terminal: false
hooks: []
---
Interact with a running command session by `command_session_id`. Write literal text to its stdin (e.g. `"y\n"`), or poll for more output with empty `chars`. A `\x03` (Ctrl-C) character only interrupts the foreground program (SIGINT); to end the session entirely set `terminate: true` (SIGTERM→SIGKILL). Returns the final result once the command exits, otherwise `status: running` with output so far. Output is the merged PTY stream in `stdout`; `stderr` is always empty.
