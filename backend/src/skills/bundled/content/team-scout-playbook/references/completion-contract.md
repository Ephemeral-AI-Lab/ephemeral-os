# Completion Contract
Use this reference when `target_paths` is a single file, a short fixed file list, or you feel tempted to return more subdivision.

## Closure rule

- Must treat the handed scope itself as the deliverable.
- Must finish with exactly one raw JSON object containing `summary` and `artifact`.
- If every listed path was mapped, return `scope_coverage: 1.0`, `gaps: ""`, and `suggested_subdivisions: []`.
- Must keep `files` and `open_questions` as JSON lists in every case; use `[]` when nothing remains open or no file read was needed.
- Must keep `gaps` as a string in every case; use `""` when nothing is missing, or one short sentence when something is missing.
- `open_questions` may record uncertainty, but must not be a disguised request to scout the same scope again.
- If the draft is prose or lacks `artifact`, it is unfinished; if it does not literally start with `{` and include `"artifact":`, it is still unfinished. Rebuild one raw JSON object instead of ending with `Here is the brief:` or ```json fences.

## When subdivisions are valid

- Must use non-empty `suggested_subdivisions` only when `target_paths` itself is a directory or package whose real child owners remain distinct.
- Never subdivide a single file just because it is long, risky, or carries multiple bug hypotheses.
- Never subdivide a short fixed file list when the same downstream worker could act on that exact boundary.

## Few-shot examples

- Example: `target_paths=["pkg/config.py"]` or `["pkg/registry.py","pkg/io/reader.py"]`.
  Map that exact boundary, return coverage `1.0`, keep `suggested_subdivisions: []`, and put any remaining runtime hypothesis into `open_questions`.
- Example: `target_paths=["pkg/compat.py"]` and the scope feels trivial after one read.
  End with raw JSON like `{"summary":"This file defines compatibility helpers.","artifact":{"target_paths":["pkg/compat.py"],"files":["pkg/compat.py"],"entry_points":["entry_points"],"open_questions":[],"scope_coverage":1.0,"gaps":"","suggested_subdivisions":[]}}`.
  Do not stop at prose like `Mapped pkg/compat.py` or JSON like `{"summary":"Mapped cli helpers"}` or `{"summary":"Mapped compat helpers"}`.
- Example: `target_paths=["pkg/core.py"]` is huge.
  Read the opening plus directly relevant regions, then return one short `gaps` sentence instead of paging `600 -> 800 -> 1000` or widening into siblings.
- Example: the draft reply uses `payload`, omits `artifact`, has `gaps: []`, has `open_questions: "Need runtime confirmation"`, has `files: "pkg/io/parquet/core.py"`, or ends as prose like `Mapped cli helpers`.
  Rename `payload` back to `artifact`, restore the missing `artifact` object, keep `gaps` as a string, keep `files` and `open_questions` as JSON lists, and rebuild one raw JSON object before finishing.
