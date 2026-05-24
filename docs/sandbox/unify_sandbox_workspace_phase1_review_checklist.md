# Phase 1 Workspace Unification Review Checklist

This checklist closes the Phase 1 blame-preservation acceptance item from
`docs/plans/unify_sandbox_workspace_phase1.md`.

## Rename / Extraction Evidence

- `backend/src/sandbox/daemon/service/sandbox_overlay.py` moved to
  `backend/src/sandbox/ephemeral_workspace/pipeline.py` in commit `88df4f723`
  with Git rename detection reporting `rename ... sandbox_overlay.py =>
  ephemeral_workspace/pipeline.py (72%)`.
- `backend/src/sandbox/isolated_workspace/manager.py` was mechanically
  decomposed in commit `88df4f723`; Git reports the focused extraction modules
  as new files and the original manager facade shrunk from 1624 lines. The
  final Phase 1 tree removes the compatibility facade from production imports.

## Reviewer Checks

- [x] `git show --summary --find-renames=50% 88df4f723 -- backend/src/sandbox/ephemeral_workspace/pipeline.py backend/src/sandbox/daemon/service/sandbox_overlay.py` shows the required rename.
- [x] `find backend/src/sandbox/isolated_workspace -name "*.py" -exec wc -l {} +` shows no isolated workspace file over 400 lines.
- [x] No production import remains for `sandbox.isolated_workspace.manager`.
