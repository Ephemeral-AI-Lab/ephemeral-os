use std::sync::Arc;

use sandbox_runtime_namespace_execution::NamespaceExecutionEngine;

use crate::command::{CommandConfig, CommandExecution};
use crate::namespace_execution::NamespaceExecutionLedger;
use crate::observability::AsyncTraceSink;
use crate::workspace_session::WorkspaceSessionService;

use super::core::CommandOperationService;

/// Build a command service over a caller-supplied engine. The test harness wires
/// that engine to a local fake launcher; this facade only assembles service parts.
#[must_use]
pub fn command_service_from_engine(
    workspace: Arc<WorkspaceSessionService>,
    config: CommandConfig,
    engine: Arc<NamespaceExecutionEngine<CommandExecution>>,
    namespace_execution: Arc<NamespaceExecutionLedger>,
    async_trace_sink: Option<AsyncTraceSink>,
) -> CommandOperationService {
    CommandOperationService::from_parts(
        workspace,
        config,
        engine,
        namespace_execution,
        async_trace_sink,
    )
}
