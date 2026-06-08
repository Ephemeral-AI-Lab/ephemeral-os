//! `eos-tools` — concrete model-facing tool construction.
//!
//! Shared tool contracts live in `eos-tool-ports`; engine tool-call policy lives
//! in `eos-engine`. This crate owns concrete tool DTOs, specs, executors, and
//! default registry assembly.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

#[path = "registry/mod.rs"]
mod registry;
#[path = "runtime/mod.rs"]
mod runtime;
#[path = "tools/mod.rs"]
mod tools;

#[cfg(test)]
#[path = "../tests/support/mod.rs"]
mod support;

pub use registry::config::{ToolConfig, ToolConfigError, ToolConfigSet};
pub use tools::terminal::{
    descriptor, render_tool_instruction, TerminalDescriptor, TerminalTool, ToolInstructions,
};
pub use tools::{
    build_default_registry, build_default_registry_with_services, AttemptSubmissionService,
    CallerScope, CommandToolService, RootSubmissionService, SandboxToolService, SkillToolService,
};
