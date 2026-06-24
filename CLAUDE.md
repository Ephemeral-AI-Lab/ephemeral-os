# CLAUDE.md

Guidance for working in this repository. Read `README.md` for the component map
and boundary law; this file covers how to write and change code here.

## Project

Rust workspace (edition 2021, rust-version 1.85) for the EphemeralOS sandbox.
Crates live under `crates/`; see the component table in `README.md` for each
crate's job and what it must never do. Respect those boundaries — a change that
crosses them is wrong even if it compiles.

## Engineering practice (required)

- **SOLID, SRP first.** Every type, module, and function owns one
  responsibility. If you cannot name a unit's single job in one sentence, split
  it. Keep crate boundaries from `README.md` intact; depend on the narrowest
  abstraction, not a concrete implementation crate.
- **Prefer less.** Fewer fields, fewer types, fewer methods, fewer round trips.
  Before adding a field/struct/method, check whether an existing one already
  carries the responsibility. Collapse redundant indirection and avoid extra
  hops across the manager/daemon/runtime boundary when one suffices.
- **No inline comments in production code.** Names and types carry the meaning;
  if code needs an inline comment to be understood, restructure it instead.
  Doc comments (`///`/`//!`) on public items are fine. Inline comments are
  acceptable only in tests, where they explain intent of a scenario.
- **Parallel workers.** Other agents may be editing this repo concurrently. Only
  touch what your task requires, never revert or overwrite changes you did not
  make, and prefer additive, localized edits.

## Build & test

```sh
export PATH="$PWD/bin:$PATH"   # repo-local sandbox tools

cargo build
cargo test                     # whole workspace
cargo test -p sandbox-runtime  # focused crate
cargo test -p sandbox-daemon

cargo clippy --all-targets     # must pass; lints are configured in Cargo.toml
cargo fmt

cargo run -p xtask -- package                  # in-container daemon binary
cargo run -p xtask -- package --profile release
```

Workspace lints (`Cargo.toml`) deny `correctness`/`suspicious` and
`undocumented_unsafe_blocks`, and warn on `unwrap_used`/`dbg_macro`. Don't
introduce new violations; justify any `unsafe` with a `// SAFETY:` block.

## Conventions

- External crates are declared once in `[workspace.dependencies]` and consumed
  via `dep.workspace = true`. Don't pin versions inside member crates.
- The YAML parser is fenced behind `crates/sandbox-config/src/yaml.rs`; callers
  use `ConfigDocument` and typed section schemas, never the parser directly.
- Daemon protocol vocabulary belongs to `sandbox-protocol`; dispatch to
  `sandbox-daemon`; runtime operation specs to `sandbox-runtime/operation`.
