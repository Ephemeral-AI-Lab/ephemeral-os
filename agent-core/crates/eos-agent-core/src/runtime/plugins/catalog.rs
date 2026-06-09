//! Runtime-owned static plugin catalog.
//!
//! This module turns the on-disk plugin catalog into validated, in-memory
//! metadata the runtime can bind into real tools. Production currently exposes
//! the built-in LSP package and catalog-native model-facing [`PluginToolSpec`]
//! sources.
//!
//! It deliberately does **not** import or execute plugin tool modules (no
//! `importlib`/`BaseTool` binding — GC-plugin-catalog-01), own
//! `eos_llm_client::ToolSpec`/`ToolExecutor`/`ToolRegistry` (those are bound in
//! `eos-agent-core` — GC-plugin-catalog-04), run `setup`/`runtime` scripts or hold
//! any Pyright/LSP session (GC-plugin-catalog-05), or traverse outside the
//! configured catalog root.
mod package;
mod tool_specs;

pub(super) use package::plugin_package_descriptor;
pub(super) use tool_specs::{plugin_tool_specs, PluginToolSpec};
