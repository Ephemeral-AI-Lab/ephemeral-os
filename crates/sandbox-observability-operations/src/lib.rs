//! Observability CLI operation surface (the read-only `observability` execution
//! space).
//!
//! This crate is **spec-only** by nature, so it has a `cli_definition` adapter
//! layer but no `service/impls` logic layer. Sandbox-scoped operations resolve
//! to the single daemon op `get_observability` with the operation name carried
//! as the `view` value; `snapshot` without `--sandbox-id` resolves to the
//! manager aggregate snapshot operation. The implementation lives in
//! `sandbox-daemon`'s observability views and `sandbox-manager`'s aggregate
//! snapshot dispatch.
#![forbid(unsafe_code)]

mod cli_definition;

pub use cli_definition::observability_catalog;
