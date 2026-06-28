//! Observability CLI operation surface (the read-only `observability` execution
//! space).
//!
//! This crate is **spec-only** by nature, so it has a `cli_definition` adapter
//! layer but no `service/impls` logic layer. Every operation (`snapshot`,
//! `trace`, `events`, `cgroup`, `layerstack`) resolves to the single daemon op
//! `get_observability` with the operation name carried as the `view` value; the
//! implementation lives in `sandbox-daemon`'s observability views, and the
//! gateway's `request_builder` maps each catalog operation's flags onto that op
//! generically. There is no per-operation dispatch to host here — the
//! `CliOperationSpec` catalog is the complete surface.
#![forbid(unsafe_code)]

mod cli_definition;

pub use cli_definition::observability_catalog;
