//! Runtime-owned config file loader and runtime-local config section.

mod document;
mod loader;
mod runtime;

pub(crate) use document::ConfigDocument;
pub(crate) use loader::load;
pub use runtime::RuntimeConfig;
