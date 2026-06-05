# CODEX.md

Codex follows the full repo instructions in `AGENTS.md`. Keep this file aligned
with any Codex-specific rules that need to be visible by name.

## Test File Placement

- Keep test-only modules and helpers under the owning test tree. Rust
  `tests.rs` files should live under the crate's `tests/` folder; when private
  module access is still required, reference them from the source module with a
  `#[path]` attribute pointing at `../tests/<module>/mod.rs`.
- Test setup, config, seam/fake/mock, fixture, and harness files belong under
  `tests/` unless they are shared production APIs.
