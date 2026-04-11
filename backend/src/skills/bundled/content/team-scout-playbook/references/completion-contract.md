# Completion Contract
Use this reference when `target_paths` is a single file, a short fixed file list, or you feel tempted to return more subdivision.

## Closure rule

- Must treat the handed scope itself as the deliverable.
- Must finish with exactly one raw JSON object containing `summary` and `artifact`.
- If every listed path was mapped, return `scope_coverage: 1.0`, `gaps: ""`, and `suggested_subdivisions: []`.
- Must keep `files` and `open_questions` as JSON lists in every case; use `[]` when nothing remains open or no file read was needed.
- Must keep `gaps` as a string in every case; use `""` when nothing is missing, or one short sentence when something is missing.
- `open_questions` may record uncertainty, but must not be a disguised request to scout the same scope again.
- Never end with prose like `Here is the brief:` or with ```json fences; the final assistant message itself must be the raw object.

## When subdivisions are valid

- Must use non-empty `suggested_subdivisions` only when `target_paths` itself is a directory or package whose real child owners remain distinct.
- Never subdivide a single file just because it is long, risky, or carries multiple bug hypotheses.
- Never subdivide a short fixed file list when the same downstream worker could act on that exact boundary.

## Few-shot examples

- Example: `target_paths=["pkg/config.py"]` or `["pkg/registry.py","pkg/io/reader.py"]`.
  Map that exact boundary, return coverage `1.0`, keep `suggested_subdivisions: []`, and put any remaining runtime hypothesis into `open_questions`.
- Example: `target_paths=["pkg/core.py"]` is huge.
  Read the opening plus directly relevant regions, then return one short `gaps` sentence instead of paging `600 -> 800 -> 1000` or widening into siblings.
- Example: `target_paths=["pkg/io/parquet"]`.
  Subdivisions are valid because the package has real child owners; keep `files` as a JSON list like `["pkg/io/parquet/core.py","pkg/io/parquet/engine_a.py"]`.
- Example: the draft object uses `payload`, `gaps: []`, `open_questions: "Need runtime confirmation"`, or `files: "pkg/io/parquet/core.py"`.
  Rename `payload` back to `artifact`, keep `gaps` as a string, and keep `files` and `open_questions` as JSON lists before finishing.
- Example: a read on `pkg/tests/test_config.py` was rejected while scouting `target_paths=["pkg/config.py"]`.
  Ignore that rejected read, finish mapping `pkg/config.py`, and still end with one raw JSON object containing `summary` and `artifact`.
