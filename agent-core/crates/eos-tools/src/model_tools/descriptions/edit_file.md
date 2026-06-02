Apply one exact search-and-replace edit to an existing file.

Use this when:
- You want a targeted, minimal change to an existing file (rename a symbol
  in one spot, fix a line, add an import).
- The block you want to replace can be unambiguously identified by 2–6
  adjacent lines.

Prefer over:
- `write_file` — for ANY modification of an existing file. Use
  `write_file` only when you are creating a new file or intentionally
  rewriting the whole thing.
- `exec_command` with `sed`/`awk` — `edit_file` is atomic, audited, and refuses
  ambiguous matches instead of silently mangling.

Do NOT use for:
- Creating new files — use `write_file`.
- Renaming a symbol across the whole repo in one call — call `edit_file`
  once per file, or use `write_file` if the file needs a wholesale
  rewrite.

Required precondition:
- You MUST have read the target file with `read_file` in this conversation
  before calling `edit_file`. The tool will error otherwise — this
  protects you from blind edits with stale assumptions about file
  contents.

Capabilities and constraints:
- `old_text` must match byte-for-byte: whitespace, indentation, newlines,
  all included.
- `old_text` must be unique in the file, unless `replace_all=true`. If it
  isn't unique and you want a single targeted change, widen the match with
  surrounding context until it is — don't trim it to be terser.
- `replace_all=true` replaces EVERY occurrence of `old_text` in one call.
  The only failure is when `old_text` is absent (`anchor not found`). Use
  it for a repo-symbol rename within one file. Concurrency caveat:
  `replace_all` replaces however many occurrences exist in the CURRENT
  committed content and does NOT detect concurrent edits to that file;
  prefer the default unique-match mode when correctness depends on the
  file being unchanged.
- You cannot create new files. If the path doesn't exist, the call fails.
- Optimistic concurrency: if the file changed under you (e.g., another
  tool or test run wrote to it), the result is `aborted_version` —
  re-read, recompute `old_text`, and retry.

Output shape:
- `status`: "edited" | "aborted_version" | "failed".
- `changed_paths`: the edited file (and any side-effects from the audit
  layer).
- `applied_edits`: counts edits applied, not occurrences — 1 for one
  edit even when `replace_all=true` hits several spots.
- `conflict_reason`: populated when `status != "edited"`.

Common pitfalls:
- Including the `<lineno><tab>` prefix from `read_file` output in
  `old_text` — drop it; that prefix isn't in the file.
- Deleting a section with empty `new_text`: works, but make sure
  `old_text` includes the trailing newline so you don't leave a blank
  line.
- Using `edit_file` for find-and-replace across many occurrences in one
  file — pass `replace_all=true` instead, or use `multi_edit` for several
  distinct edits to one file, or rewrite it with `write_file`.

Example:
  # Good: 3 lines of context, unique match
  edit_file(
    file_path="src/foo.py",
    old_text="def bar(x: int) -> int:\n    return x * 2\n\n",
    new_text="def bar(x: int) -> int:\n    return x * 3\n\n",
  )

  # Rename every occurrence of a symbol in one file
  edit_file(
    file_path="src/foo.py",
    old_text="old_name",
    new_text="new_name",
    replace_all=True,
  )