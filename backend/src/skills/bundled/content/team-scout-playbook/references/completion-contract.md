# Completion Contract

Use this reference when `target_paths` is a single file, a short fixed file list, or you feel tempted to return more subdivision.

## Closure rule

- Must treat the handed scope itself as the deliverable.
- If every listed path was mapped, return `scope_coverage: 1.0`, `gaps: ""`, and `suggested_subdivisions: []`.
- `open_questions` may record uncertainty, but must not be a disguised request to scout the same scope again.

## When subdivisions are valid

- Must use non-empty `suggested_subdivisions` only when `target_paths` itself is a directory or package whose real child owners remain distinct.
- Never subdivide a single file just because it is long, risky, or carries multiple bug hypotheses.
- Never subdivide a short fixed file list when the same downstream worker could act on that exact boundary.

## Few-shot examples

- Example: `target_paths=["pkg/config.py"]`.
  The file mixes import-time setup, normalization helpers, and reload logic.
  Read the file, map its entry points, and return coverage `1.0` with no subdivisions; the planner can still assign a developer to two functions inside that file without another scout pass.
- Example: `target_paths=["pkg/registry.py","pkg/io/reader.py"]`.
  Those two files already define the public seam the planner cares about.
  Return a complete brief for the pair and put any remaining runtime hypothesis into `open_questions`; do not ask for a new scout lane called "registry" versus "reader" after the planner already bounded the pair.
- Example: `target_paths=["pkg/io/parquet"]`.
  The package contains `core.py`, `engine_a.py`, `engine_b.py`, and shared helpers.
  Here subdivisions are valid because a downstream worker would otherwise reopen the whole package to rediscover those distinct child owners.
