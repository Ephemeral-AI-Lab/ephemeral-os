//! Sandbox tools: `read_file`, `write_file`, `edit_file`, `multi_edit`, `grep`,
//! `glob`, `exec_command`, `write_stdin`. Each builds a typed request, calls the
//! `eos-sandbox-api` `tool_api` helper over the [`SandboxTransport`], and projects
//! the typed result into a serialized output DTO.
//!
//! Command-session **registration** with the background supervisor and the
//! `recover-from-supervisor` / `mark-reported` steps are engine-dispatch concerns
//! (anchor §3, "background execution is an engine dispatch mode"), relocated to
//! `eos-engine`; the tool body surfaces `command_session_id` and issues the
//! Ctrl-C cancel.

mod command;
mod common;
mod files;
mod outputs;
mod registration;
mod search;

#[cfg(test)]
mod tests;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    registration::register(registry, config);
}
