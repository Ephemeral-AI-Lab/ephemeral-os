---
intent: read_only
terminal: false
hooks: []
---
Read a stateless tail snapshot from a running or completed command session by `command_session_id`. Returns at most `last_n_lines` trailing output lines and any terminal status now known for the session. This tool has no progress cursor and does not mutate stdin; call it again when you need a fresh snapshot.
