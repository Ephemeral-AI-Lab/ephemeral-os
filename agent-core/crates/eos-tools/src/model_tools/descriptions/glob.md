Enumerate workspace files matching a glob pattern.

Use this when:
- You need a list of files by name or extension (e.g. "every Python file
  in `pkg/`").
- You're narrowing scope before a more expensive operation (`grep`,
  `read_file` per file).

Prefer over:
- `exec_command` with `find`/`ls` — `glob` returns structured output.

Do NOT use for:
- Searching file CONTENTS — use `grep`.
- Recursive directory walks across hidden VCS data — `.git/`, `.svn/`,
  etc. are excluded by design.
- Following symlinks — symlinks are listed but not traversed.

Capabilities and constraints:
- Pattern is Python `fnmatch` style. `*` matches within a path segment;
  `**` does NOT recurse — set `path=...` plus a `*.py`-style narrowing
  instead.
- Brace expansion (`{a,b}`) is NOT supported.
- Leading-dot VCS directories are excluded.
- Result set is capped at 100 paths. Narrow with `path=...` when
  truncated.

Output shape:
- `filenames`: workspace-relative matched paths.
- `num_files`: count returned (post-cap).
- `truncated`: True when the cap was hit.

Common pitfalls:
- Expecting `**/*.py` to recurse — it does not. Use `path="src"` and
  `pattern="*.py"`, or call `glob` from a deeper `path` to scope down.
- Treating truncation as "all results" — check `truncated` before
  assuming completeness.