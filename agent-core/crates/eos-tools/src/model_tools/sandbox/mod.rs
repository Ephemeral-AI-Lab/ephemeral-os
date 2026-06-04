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

mod edit_file;
mod exec_command;
mod glob;
mod grep;
mod lib;
mod multi_edit;
mod read_file;
mod write_file;
mod write_stdin;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    lib::registration::register(registry, config);
}
