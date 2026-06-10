//! The caller-workspace feature: file ops, command-session runs, the isolated
//! lifecycle, and the cross-substrate cancel surface.
//!
//! A workspace run composes the `eos-workspace-runtime` substrate with the
//! daemon-resident seams (OCC publish, resource telemetry, isolated-audit
//! sink). Each family submodule owns its dispatcher handlers; [`cancel`] is
//! the coordinator that tears down a caller's command sessions and isolated
//! namespace in order, so "cancel never publishes" stays structural.

pub(crate) mod cancel;
pub(crate) mod files;
pub(crate) mod isolated;
pub(crate) mod run;
