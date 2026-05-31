//! `xtask`: build and release tooling for the workspace (musl static artifact,
//! fixture checks).
//!
//! Invariant: dev-only tooling, never linked into the runtime. `anyhow` is
//! allowed here (binary). Filled next phase; intentionally empty now.
#![forbid(unsafe_code)]

fn main() {}
