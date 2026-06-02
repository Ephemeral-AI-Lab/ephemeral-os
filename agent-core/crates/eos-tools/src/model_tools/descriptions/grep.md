Regex-scan workspace file contents.

Use this when:
- You need to find which files contain a pattern (`files_with_matches`
  mode).
- You need to count matches per file (`count` mode).
- You need to extract matching lines for inspection (`content` mode).

Prefer over:
- `exec_command` with `grep`/`rg` — `grep` is cheaper, routed read-only, and
  returns structured output.

Do NOT use for:
- Reading whole files — once you know the path, use `read_file`.
- Enumerating files by name (no content match) — use `glob`.
- Structural code search (AST-aware) — there is no `--type`-aware mode;
  combine `glob` to narrow scope, then call `grep`.

Capabilities and constraints:
- Pattern is Python `re` regex (NOT PCRE2). Possessive quantifiers and
  recursive groups are unsupported. Literal braces work without escaping.
- VCS directories (`.git`/`.svn`/`.hg`/`.bzr`/`.jj`/`.sl`) are excluded.
- Files larger than 10 MB and non-UTF-8 files are skipped silently.
- Output is capped at 20 KB total content AND `head_limit` entries
  (default 250; 0 = unlimited subject to the byte cap).
- `multiline=True` enables `re.MULTILINE | re.DOTALL` — `.` matches
  newlines, `^`/`$` match line boundaries.
- `glob_filter` is fnmatch (e.g. `'*.py'`), not bash glob.

Output shape:
- `mode`: "files_with_matches" | "count" | "content".
- `filenames`: matched files in scan order.
- `content`: rendered match content (`content` mode) or `path:count`
  lines (`count` mode); empty in `files_with_matches` mode.
- `num_files`, `num_lines`, `num_matches`: cardinalities.
- `applied_limit`, `applied_offset`, `truncated`: paging signals.

Common pitfalls:
- Forgetting `multiline=True` for cross-line patterns — the regex won't
  match across newlines by default.
- Over-broad scope: scanning the whole workspace for "TODO" returns
  truncated output. Pass `path=...` or `glob_filter=...` to narrow first.
- Confusing `head_limit=0` with "no results": 0 means "unlimited".

Example:
  # Find every place a symbol is defined
  grep(pattern=r"^class (Foo|Bar)\b", output_mode="content",
       line_numbers=True)