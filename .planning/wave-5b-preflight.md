# Wave 5b Pre-flight Investigation

**Status:** All three files KEEP. W5b closes as NO-OP.
**Date:** 2026-05-14
**Branch:** `codex/fix-dot-path-normalization-tests`

The RFC's §14 pre-flight gate requires that each candidate inline target be
empirically classified as either THIN (mechanical re-export, safe to inline)
or REAL-LOGIC (carries non-trivial state or composition, KEEP). The three
files under investigation all carry real business logic; none of them are
thin wrappers. Inlining any of them into `daemon/service/occ_backend.py`
would inflate that file past the 500-LOC guideline (and would push it close
to the 600-LOC hard ceiling once W3's overlays are accounted for) without
removing actual logic.

## File: `backend/src/sandbox/daemon/service/result_projection.py` (87 LOC)

**Verdict:** REAL-LOGIC — KEEP.

Five projection helpers that translate the raw `ChangesetResult.files`
sequence into the public guarded-result shape consumed by `api.shell`,
`api.write`, and `api.edit`:

- `committed_paths(files, fallback_path)` — three-branch fallback resolution
  (published > first-aborted > caller-provided fallback). Encodes the
  audit-emission contract; not a re-export.
- `published_paths(files)` — filter, but the predicate `is_published_status`
  is the OCC public contract for committed-vs-aborted; pulling it into
  occ_backend would lose the projection seam tests depend on.
- `conflict_and_status(files)` — surfaces the first non-OK `FileResult` as a
  `ConflictInfo` + status string. Composite logic threaded through every
  guarded-result emission.
- `conflict_to_dict(conflict)` — serialization. Trivial on its own, but
  paired with `conflict_and_status` it forms the conflict-emission contract.
- `gitignore_cache_timings(gitignore)` — guarded getattr-with-defaults
  pattern. Comment WR-01 documents a prior crash on alternative oracles:
  collapsing this guard would re-introduce that failure mode for any future
  protocol-compatible oracle that omits the counters.

Inlining this module would scatter projection logic across `shell_runner`,
`handler/request_context`, and `handler/metrics`, breaking the single
projection seam.

## File: `backend/src/sandbox/daemon/service/shell_runner.py` (181 LOC)

**Verdict:** REAL-LOGIC — KEEP.

The runtime-local `api.shell` orchestration boundary. Owns:

- `execute_shell_api` — the public daemon-side entrypoint dispatched by
  `daemon/handler/tools/shell.py`. Builds the request, wires layer-stack /
  occ-client / gitignore / storage-root from `occ_backend`, dispatches
  `execute_command`, projects the result.
- `_payload_from_result` — non-trivial status derivation:
  `command_failed`/`changeset.success`/`conflict_status` triplet collapses to
  a single public `status` string. The branch ordering is load-bearing
  (command-exit-code override of OCC success state).
- `_command_request` — request construction with workspace-ref derivation.

This is the seam between the daemon's RPC layer and `execute_command`. It
cannot be merged into `occ_backend` (which is a passive factory) nor into
`handler/tools/shell.py` (which is the dispatcher wiring).

## File: `backend/src/sandbox/daemon/service/workspace_server.py` (173 LOC)

**Verdict:** REAL-LOGIC — KEEP.

Owns the per-root `LayerStackManager` cache, the one-shot stale-staging
fence, and the public `build_workspace_base` entrypoint. Specifically:

- `_MANAGER_CACHE` + `_MANAGER_CACHE_LOCK` — process-singleton cache used by
  every OCC backend instantiation site (`occ_backend.build_occ_backend`,
  `layer_stack_client.LayerStackClient.__init__`). Inlining would force the
  cache state into `occ_backend`, breaking the test seam used by
  `clear_backend_cache`.
- `_DAEMON_STARTED_AT` + `_FENCED_STAGING_ROOTS` + `fence_stale_staging` —
  one-shot per-process staging fence that removes staging dirs predating
  the current daemon. Has its own RPC entrypoint (`api.fence_stale_staging`)
  and unit tests asserting fence-once semantics. Not a wrapper.
- `build_workspace_base_api` — orchestrates the layer-stack base
  construction; depends on `WorkspaceBindingError` recovery semantics.

Inlining this module would conflate cache state with backend construction
state and would require duplicating the `_MANAGER_CACHE` singleton in every
import site of `LayerStackManager`.

## Decision

W5b → NO-OP. The inline candidates the RFC speculatively listed are
empirically real-logic. The §14 gate is satisfied: pre-flight investigation
performed, all three files documented as KEEP, no W5b commit required.

The `daemon/service/` boundary remains as it stands today: 6 files
(`__init__.py`, `layer_stack_client.py` (85 LOC), `occ_backend.py` (115 LOC),
`result_projection.py` (87 LOC), `shell_runner.py` (181 LOC),
`workspace_binding.py` (38 LOC), `workspace_server.py` (173 LOC)). Total 679
LOC across these files. The two thin candidates `layer_stack_client.py` and
`workspace_binding.py` are *adapters*, not wrappers: `LayerStackClient`
forwards method calls verbatim onto `LayerStackManager` (preserving the OCC
Protocol seam at `occ/ports.py`), and `RuntimeWorkspaceBindingReader`
enforces the fail-closed binding contract before OCC mutation dispatch.
Inlining the adapters would defeat the architectural Protocol boundary and
mechanically violate AC #11 (600-LOC ceiling) once their methods land
inside `LayerStackManager`.
