use std::sync::Arc;

use sandbox_observability_telemetry::{Observer, SpanRegistry};
use sandbox_runtime_namespace_execution::{
    ExecutionCaps, NamespaceExecutionEngine, NamespaceExecutionId,
};

use crate::command::{CommandConfig, CommandExecValue};
use crate::namespace_execution::RuntimeNamespaceExecutionSnapshot;
use crate::workspace_session::WorkspaceSessionService;

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: CommandConfig,
    engine: Arc<NamespaceExecutionEngine<CommandExecValue>>,
    exec_spans: Arc<SpanRegistry<NamespaceExecutionId>>,
    obs: Observer,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: CommandConfig,
        obs: Observer,
    ) -> Self {
        let exec_spans = Arc::new(SpanRegistry::new(obs.clone()));
        let engine = Arc::new(NamespaceExecutionEngine::new(
            exec_spans.clone(),
            ExecutionCaps {
                max_active: config.max_active,
                setup_timeout_s: config.setup_timeout_s,
                stdin_write_deadline: std::time::Duration::from_secs_f64(
                    config.execution.stdin_write_deadline_s,
                ),
                max_terminal_entries: config.execution.max_terminal_entries,
                max_transcript_window_bytes: config.execution.max_transcript_window_bytes,
                max_runner_result_bytes: config.execution.max_runner_result_bytes,
            },
        ));
        Self::with_engine(workspace, config, engine, exec_spans, obs)
    }

    /// Build a command service over a caller-supplied engine and the exec span
    /// registry wired into it. The same `exec_spans` must back both the engine's
    /// terminal hook and this service's launch path, so a parked span always has
    /// a recorder. The test harness wires the engine to a local fake launcher;
    /// production goes through `new`.
    #[doc(hidden)]
    #[must_use]
    pub fn with_engine(
        workspace: Arc<WorkspaceSessionService>,
        config: CommandConfig,
        engine: Arc<NamespaceExecutionEngine<CommandExecValue>>,
        exec_spans: Arc<SpanRegistry<NamespaceExecutionId>>,
        obs: Observer,
    ) -> Self {
        Self {
            workspace,
            config,
            engine,
            exec_spans,
            obs,
        }
    }

    #[must_use]
    pub fn active_namespace_executions(&self) -> Vec<RuntimeNamespaceExecutionSnapshot> {
        let mut snapshots = self.engine.live_values(|command| {
            Some(RuntimeNamespaceExecutionSnapshot {
                namespace_execution_id: command.exec.id().clone(),
                workspace_session_id: command.workspace_session_id.clone(),
                operation_name: command.operation_name.to_owned(),
                command: Some(command.command.clone()),
            })
        });
        snapshots.sort_by(|left, right| {
            left.namespace_execution_id
                .cmp(&right.namespace_execution_id)
        });
        snapshots
    }

    #[must_use]
    pub fn config(&self) -> &CommandConfig {
        &self.config
    }

    #[must_use]
    pub(crate) fn engine(&self) -> &Arc<NamespaceExecutionEngine<CommandExecValue>> {
        &self.engine
    }

    #[must_use]
    pub(super) fn obs(&self) -> &Observer {
        &self.obs
    }

    #[must_use]
    pub(super) fn exec_spans(&self) -> &Arc<SpanRegistry<NamespaceExecutionId>> {
        &self.exec_spans
    }

    #[must_use]
    pub(super) fn workspace_handle(&self) -> &Arc<WorkspaceSessionService> {
        &self.workspace
    }
}
