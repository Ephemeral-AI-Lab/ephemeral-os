# Exploration Script

Use this reference only on fresh benchmark roots or any turn that still lacks clear ownership.

## Workflow

1. Must start with one narrow `ci_workspace_structure(...)` pass on the nearest likely production directory or package.
2. Must follow with `ci_scoped_status(scope_paths=[...])` on one exact existing production path from that listing.
3. Must launch scouts only after that live anchor exists.
4. Must keep each scout on one distinct unresolved owner slice.
5. Must stop exploring once the dominant owner slice and at least one residual boundary are clear.

## Scout fanout strategy

1. Must fan out by distinct production-owner slices, not by raw failing-test count.
2. The first wave should usually contain 2-4 scouts when 2-4 distinct owner slices are still unresolved.
3. If only one owner slice is unresolved, must launch one scout, not a broad wave.
4. If more than 4 unresolved owner slices remain, must launch the smallest useful disjoint subset first and leave the rest for a later wave or a child planner.
5. Must keep every scout narrow enough that it answers one ownership question.
6. Must launch another wave only when the first wave returns partial ownership and several disjoint owner slices are still unresolved.
7. Must stop fanout as soon as the next plan layer can name the dominant owner slice and the residual boundary.

## One-shot example

If the live anchor shows failures that plausibly map to `pkg/io/`, `pkg/schema/`, and `pkg/compat/`, the first wave should be three scouts:

- Scout 1: `target_paths=["pkg/io"]`
- Scout 2: `target_paths=["pkg/schema"]`
- Scout 3: `target_paths=["pkg/compat"]`

Must not split that into one scout per failing test file.
Must not collapse those three owner slices into one omnibus scout.
Must stop after that wave if it already identifies the dominant owner slice and the residual boundary.

## Rules

- Never open with root-wide CI queries.
- Never spend first-wave scouts on benchmark test files when a plausible production owner exists.
- Never guess missing production files from test names.
- Never bundle unrelated owner slices into one scout just to reduce lane count.
- Never keep scouting after owner sufficiency is reached.
- Treat duplicate-scout rejection, repeated wait protocol errors, and budget warnings as stop-and-plan signals.
