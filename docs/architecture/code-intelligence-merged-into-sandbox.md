# Code Intelligence merged into Sandbox

**Date:** 2026-04-30
**Plan:** [`/.omc/plans/code-intelligence-into-sandbox.md`](../../.omc/plans/code-intelligence-into-sandbox.md)
**Commits:**
- `refactor(sandbox): promote async_bridge to top-level`
- `refactor(sandbox): move code_intelligence into sandbox subpackage`
- `refactor(sandbox): SandboxService owns code_intelligence lifecycle`

## What changed

The `code_intelligence` package no longer lives at the top of `backend/src/`.
It is now `sandbox/code_intelligence/` — a subpackage of `sandbox/`.

### Import path

**Before**
```python
from code_intelligence.service import CodeIntelligenceService, get_code_intelligence
from code_intelligence.core.async_bridge import run_sync_in_executor
from code_intelligence.core.types import EditRequest
```

**After**
```python
from sandbox.code_intelligence.service import CodeIntelligenceService
from sandbox.async_bridge import run_sync_in_executor
from sandbox.code_intelligence.core.types import EditRequest

# External code (routers, benchmarks, anything outside sandbox/) MUST use
# SandboxService rather than the registry directly:
from sandbox.service import SandboxService
svc = SandboxService().code_intelligence_for(sandbox_id)
```

`async_bridge` was promoted out of `code_intelligence/core/` to `sandbox/`
top-level since it coordinates sandbox I/O loops, not CI internals.

### Lifecycle ownership

`SandboxService` is now the only public surface for obtaining a
`CodeIntelligenceService` from outside the `sandbox` package:

| Method                                  | Replaces                              |
| --------------------------------------- | ------------------------------------- |
| `code_intelligence_for(sandbox_id, …)`  | `get_code_intelligence(sandbox_id, …)` |
| `code_intelligence_if_exists(id)`       | `get_code_intelligence_if_exists(id)`  |
| `dispose_code_intelligence(id)`         | `dispose_code_intelligence(id)`        |

`SandboxService.delete_sandbox(id)` automatically calls
`dispose_code_intelligence(id)` so per-sandbox CI state cannot leak past
the underlying sandbox.

The registry at `sandbox.code_intelligence.registry` still exists and is
still callable, but it is reserved for whitebox tests inside the `sandbox`
package. External consumers (server routers, benchmarks, non-sandbox
package code) MUST go through `SandboxService` — those names are
intentionally absent from `sandbox.code_intelligence.service.__all__`.

## What is preserved

- `discover_workspace`-at-tool-dispatch-time timing — workspace discovery
  is still lazy. `create_sandbox` does NOT trigger CI bootstrap.
- Lazy CI service creation. The registry is still hit only on first
  access, just routed through `SandboxService.code_intelligence_for`.

## Operational notes

- Logger names changed from `code_intelligence.X` to
  `sandbox.code_intelligence.X`. Any log-based dashboards or alerts that
  filter on these names need updating.
- The empty stragglers under the old `code_intelligence/` directory
  (`analysis`, `editing`, `lsp`, `state`, `sandbox_daemon`) — leftovers
  from a prior abandoned WIP — were deleted as part of the move.

## Testing

- 436 tests passing in `backend/tests/test_sandbox` +
  `backend/tests/test_tools`. Same count as the pre-change baseline.
- Tests under `backend/tests/test_code_intelligence/` moved to
  `backend/tests/test_sandbox/test_code_intelligence/`.
- Whitebox tests (e.g., per-test fixtures that need
  `dispose_all_code_intelligence` to reset state) continue to import
  directly from `sandbox.code_intelligence.registry`.

## Why this exists

Before the merge, the two packages were already 1:1 per-sandbox by
construction (the registry is keyed by `sandbox_id`) but were
bidirectionally coupled at the import level — `code_intelligence/`
imported from `sandbox.daytona_utils`, and `sandbox/` reached back into
`code_intelligence` for runtime injection. Co-locating CI under
`sandbox/` makes the binding explicit at the package level and removes
the cycle.

The "external consumers go through SandboxService" rule keeps the public
surface to one well-known type, leaving room to add cross-cutting
concerns (instrumentation, validation, request scoping) in one place.

## See also

- ADR section in [`/.omc/plans/code-intelligence-into-sandbox.md`](../../.omc/plans/code-intelligence-into-sandbox.md)
  for the decision drivers, alternatives considered, and pre-mortem.
