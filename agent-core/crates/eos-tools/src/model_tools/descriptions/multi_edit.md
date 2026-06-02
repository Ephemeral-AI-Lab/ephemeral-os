Apply an ordered batch of search-and-replace edits to a single file in
one atomic call.

Use this when:
- You need several distinct edits to the SAME file and want them to land
  together or not at all.
- Later edits build on earlier ones: each edit is applied against the
  content produced by the previous edit (edit N sees edit N-1's result).

Semantics:
- Single `file_path`; `edits` is an ordered array of
  `{old_text, new_text, replace_all}` objects applied in order.
- All-or-nothing: if ANY edit fails (anchor not found, occurrence count
  mismatch, or a concurrent change), the whole call aborts and NOTHING is
  written.
- Per-edit `replace_all`: each edit independently chooses unique-match
  (default) or replace-every-occurrence.
- `applied_edits` counts edits applied, not occurrences.

Required precondition:
- You MUST have read the target file with `read_file` in this
  conversation before calling `multi_edit`.

Prefer over:
- `edit_file` — use that for a SINGLE change to one file.
- `write_file` — use that only to create a new file or rewrite
  a file wholesale.

Concurrency caveat:
- `replace_all` replaces however many occurrences exist in the CURRENT
  committed content and does NOT detect concurrent edits to that file;
  prefer the default unique-match mode when correctness depends on the
  file being unchanged.

Example:
  multi_edit(
    file_path="src/foo.py",
    edits=[
      {"old_text": "import os", "new_text": "import os\nimport sys"},
      {"old_text": "old_name", "new_text": "new_name", "replace_all": True},
    ],
  )