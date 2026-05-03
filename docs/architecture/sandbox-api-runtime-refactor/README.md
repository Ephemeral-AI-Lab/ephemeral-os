# Sandbox API + Runtime Refactor — Slice Plans

Per-slice implementation plans. Each slice ends green: `make build`, `ruff check`, `make test` all pass. Old code paths coexist with new ones until the slice that deletes them — no intermediate broken states.

Design context (motivation, end-state shape §1, layering invariants §1.5, result types §1.6, plugin integration §3, out-of-scope §4, risks §5) lives in [`../sandbox-api-runtime-refactor.md`](../sandbox-api-runtime-refactor.md). These slice plans are not standalone — they assume that document.

## Slice index

| # | Slice | Goal |
|---|---|---|
| 1 | [Provider seam](./slice-1-provider-seam.md) | Add `ProviderAdapter` Protocol + Daytona adapter; no caller changes. |
| 2 | [`raw_exec` primitive](./slice-2-raw-exec.md) | Public `sandbox.api.raw_exec` over the adapter; importer-allowlist test. |
| 3 | [Runtime scaffolding](./slice-3-runtime-scaffolding.md) | `daemon/` → `runtime/`; add `bundle.py`, `setup_orchestrator.py`, `entrypoint.py`. |
| 4 | [OCC peer relocation](./slice-4-occ-relocation.md) | `mutations/` → `sandbox/occ/`; add `edit_pipeline`, `write_pipeline`. |
| 5a | [Overlay → OCC decouple](./slice-5a-overlay-decouple.md) | Strip OCC call from `OverlayCommandCommitter` in place. |
| 5b | [Overlay peer relocation](./slice-5b-overlay-relocation.md) | `overlay/` → `sandbox/overlay/`; add `shell_pipeline`. |
| 6 | [Public verb API](./slice-6-public-api.md) | `sandbox.api.{shell,read,write,edit}`; §1.6 result hierarchy. |
| 7 | [Delete legacy](./slice-7-delete-legacy.md) | Remove `code_intelligence/`, old API modules, `SandboxTransport`. |
| 8 | [Tests + docs](./slice-8-tests-docs.md) | Relocate tests; add runtime/pipeline coverage; supersede prior docs. |

## Ordering invariants

- 5a must land before 5b and must be revertible without 5b. The decoupling correctness fix is gated independently.
- 6 must land before 7. The public surface flips first; legacy deletes only after the new surface owns all callers.
- Each slice keeps the old path alive until the slice *after* the migration completes; no slice both adds and deletes the same surface.
