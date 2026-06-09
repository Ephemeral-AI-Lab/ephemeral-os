//! Runtime-owned static plugin catalog.
//!
//! This module turns the on-disk plugin catalog into validated, in-memory
//! metadata the runtime can bind into real tools. It parses each plugin's
//! `plugin.md` frontmatter into a [`PluginManifest`], discovers all manifests
//! under one configured catalog root as an immutable [`PluginCatalog`], supplies
//! the catalog-native model-facing [`PluginToolSpec`] sources (today the 10 LSP
//! specs), and returns neutral plugin package descriptors consumed by sandbox
//! setup APIs.
//!
//! It deliberately does **not** import or execute plugin tool modules (no
//! `importlib`/`BaseTool` binding — GC-plugin-catalog-01), own
//! `eos_llm_client::ToolSpec`/`ToolExecutor`/`ToolRegistry` (those are bound in
//! `eos-agent-core` — GC-plugin-catalog-04), run `setup`/`runtime` scripts or hold
//! any Pyright/LSP session (GC-plugin-catalog-05), or traverse outside the
//! configured catalog root.
// Discovery/manifest parsing is folded here with the catalog crate, but runtime
// config does not yet wire external catalog roots into request startup.
#[cfg_attr(not(test), allow(dead_code))]
mod discovery;
#[cfg_attr(not(test), allow(dead_code))]
mod error;
#[cfg_attr(not(test), allow(dead_code))]
mod frontmatter;
#[cfg_attr(not(test), allow(dead_code))]
mod manifest;
#[cfg_attr(not(test), allow(dead_code))]
mod names;
mod package;
#[cfg(test)]
#[path = "../../../tests/plugins/catalog/support/mod.rs"]
mod support;
mod tool_specs;

pub(super) use package::plugin_package_descriptor;
pub(super) use tool_specs::{plugin_tool_specs, PluginToolSpec};
