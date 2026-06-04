---
intent: write_allowed
terminal: false
hooks: []
---
Create a new file, or completely overwrite an existing one, with UTF-8 text.

Use this when:
- You are creating a file from scratch.
- You are intentionally rewriting the entire contents of an existing file
  (e.g., a generated artifact, a config rewritten from a template).

Prefer over:
- `exec_command` with `echo >` or here-docs — `write_file` is atomic and
  audited; command redirection is not.

Do NOT use for:
- Partial changes to an existing file — use `edit_file`. `write_file`
  will silently destroy any content you don't supply.
- Appending — there is no append mode. To add to a file, read it, then
  write the combined content.
- Creating directories — the parent directory must already exist. Use
  `exec_command` to `mkdir -p` first if needed.

Capabilities and constraints:
- The call always overwrites if the path exists. There is no "create if
  not exists" mode.
- UTF-8 text only. Binary content is not supported.
- Path must be workspace-relative or workspace-absolute.

Output shape:
- `status`: "written" or a failure status.
- `file_path`: resolved path.
- `bytes_written`: UTF-8 byte count of `content`.
- `changed_paths`: typically `[file_path]`.

Common pitfalls:
- Using `write_file` to "fix one line" — that's almost always wrong; use
  `edit_file`. Wholesale rewrites are reviewer-hostile and easy to get
  wrong.
- Forgetting the trailing newline — most repos expect files to end with
  `\n`.