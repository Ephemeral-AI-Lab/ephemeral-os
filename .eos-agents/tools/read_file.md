---
intent: read_only
terminal: false
hooks: []
---
Read a UTF-8 text file from the workspace, returned with line numbers.

Use this when:
- You need the actual contents of a specific file path.
- You need to inspect a code/config region before editing it (`edit_file`
  requires you to read first).

Prefer over:
- `exec_command` with `cat`/`sed -n`/`head`/`tail` — `read_file` is cheaper,
  returns structured output, and integrates with the edit precondition
  check.

Do NOT use for:
- Binary files (PDF, images, archives) — output is UTF-8 only.
- Directory listings or content search across many files — use `exec_command`
  with repository search tools when that capability is available to your agent.
- Re-reading a file you just edited — `edit_file`/`write_file` would have
  errored if the change failed; the harness already tracks the new
  content.

Capabilities and constraints:
- You can read up to MAX_READ_FILE_LINES (200) per call. Use `start_line`
  and `end_line` to page through larger files.
- Paths are workspace-relative or workspace-absolute. Paths outside the
  workspace return an error.
- Output is line-numbered with `cat -n` style prefixes
  (`<lineno><tab>line`), making it easy to cite specific lines
  (file_path:lineno).

Output shape:
- `file_path`: resolved path.
- `content`: numbered text block.
- `start_line`, `end_line`: window actually returned.
- `truncated`: True when more lines exist past `end_line`.

Common pitfalls:
- Indentation in `edit_file`: the line-number prefix is NOT part of file
  content. When you echo a line back into `edit_file.old_text`, drop the
  `<lineno><tab>` prefix.
- Stale reads: if another tool has changed the file since your last read,
  the next `edit_file` may return `aborted_version` — re-read and retry.
- Empty files return an empty `content`, not an error.
