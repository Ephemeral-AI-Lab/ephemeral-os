//! Tokio-backed agent-loop launcher.

use std::sync::Arc;

use eos_types::{
    AgentLoopCancellation, AgentLoopCancellationHandle, AgentLoopCompletion, AgentLoopLauncher,
    AgentLoopOutcome, AgentLoopOutcomeKind, AgentRunApi, StartAgentLoopRequest, StartedAgentLoop,
};
use tokio::sync::{oneshot, watch};

use super::{
    AgentLoopExecutor, AgentLoopExecutorInput, AgentLoopToolRegistryFactory,
    BackgroundSessionRuntimeFactory, ToolExecutionMetadataReader,
};
use crate::provider_stream::{ProviderStreamSource, ProviderStreamSourceFactory};
use crate::{AgentRunOutputs, AgentRunStreamSinkFactory};

#[derive(Clone, Debug)]
struct WatchAgentLoopCancellation {
    sender: watch::Sender<Option<String>>,
}

impl AgentLoopCancellation for WatchAgentLoopCancellation {
    fn cancel(&self, reason: &str) {
        self.sender.send_if_modified(|current| {
            if current.is_some() {
                return false;
            }
            *current = Some(reason.to_owned());
            true
        });
    }
}

/// Loop-side cancellation signal.
#[derive(Clone, Debug)]
pub(crate) struct AgentLoopCancelSignal {
    receiver: watch::Receiver<Option<String>>,
}

impl AgentLoopCancelSignal {
    /// Current cancellation reason, if cancellation has been requested.
    #[must_use]
    pub(crate) fn reason(&self) -> Option<String> {
        self.receiver.borrow().clone()
    }

    pub(crate) async fn cancelled_reason(mut self) -> String {
        loop {
            if let Some(reason) = self.reason() {
                return reason;
            }
            if self.receiver.changed().await.is_err() {
                std::future::pending::<()>().await;
            }
        }
    }

    #[cfg(test)]
    pub(crate) fn for_test() -> Self {
        let (_handle, signal) = agent_loop_cancel_pair();
        signal
    }

    #[cfg(test)]
    pub(crate) fn for_test_pair() -> (AgentLoopCancellationHandle, Self) {
        agent_loop_cancel_pair()
    }
}

/// Build a cancel handle/signal pair for one loop.
#[must_use]
fn agent_loop_cancel_pair() -> (AgentLoopCancellationHandle, AgentLoopCancelSignal) {
    let (sender, receiver) = watch::channel(None);
    (
        Arc::new(WatchAgentLoopCancellation { sender }),
        AgentLoopCancelSignal { receiver },
    )
}

#[derive(Clone)]
pub(crate) enum AgentLoopProviderStream {
    Static(Arc<dyn ProviderStreamSource>),
    Factory(ProviderStreamSourceFactory),
}

/// Tokio-backed non-blocking agent-loop launcher.
pub struct TokioAgentLoopLauncher {
    provider_stream_source: AgentLoopProviderStream,
    tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
    execution_metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    background_sessions: Option<BackgroundSessionRuntimeFactory>,
    run_outputs: AgentRunOutputs,
    stream_sink_factory: Option<AgentRunStreamSinkFactory>,
}

impl std::fmt::Debug for TokioAgentLoopLauncher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TokioAgentLoopLauncher")
            .finish_non_exhaustive()
    }
}

impl TokioAgentLoopLauncher {
    /// Build a Tokio-backed launcher from engine-owned loop services.
    #[must_use]
    pub fn new(
        provider_stream_source: Arc<dyn ProviderStreamSource>,
        tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
        execution_metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    ) -> Self {
        Self::new_with_provider_stream(
            AgentLoopProviderStream::Static(provider_stream_source),
            tool_registry_factory,
            execution_metadata_reader,
        )
    }

    /// Build a launcher with a source resolved from each loop request.
    #[must_use]
    pub fn with_provider_stream_source_factory(
        provider_stream_source_factory: ProviderStreamSourceFactory,
        tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
        execution_metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    ) -> Self {
        Self::new_with_provider_stream(
            AgentLoopProviderStream::Factory(provider_stream_source_factory),
            tool_registry_factory,
            execution_metadata_reader,
        )
    }

    #[must_use]
    fn new_with_provider_stream(
        provider_stream_source: AgentLoopProviderStream,
        tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
        execution_metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    ) -> Self {
        Self {
            provider_stream_source,
            tool_registry_factory,
            execution_metadata_reader,
            background_sessions: None,
            run_outputs: AgentRunOutputs::new(),
            stream_sink_factory: None,
        }
    }

    /// Attach runtime contracts for engine-owned background managers.
    #[must_use]
    pub fn with_background_sessions(mut self, inputs: BackgroundSessionRuntimeFactory) -> Self {
        self.background_sessions = Some(inputs);
        self
    }

    /// Attach output fan-out for each run.
    #[must_use]
    pub fn with_run_outputs(mut self, run_outputs: AgentRunOutputs) -> Self {
        self.run_outputs = run_outputs;
        self
    }

    /// Attach a live stream sink factory resolved for each loop start request.
    #[must_use]
    pub fn with_stream_sink_factory(mut self, factory: AgentRunStreamSinkFactory) -> Self {
        self.stream_sink_factory = Some(factory);
        self
    }
}

impl AgentLoopLauncher for TokioAgentLoopLauncher {
    fn start_agent_loop(
        &self,
        request: StartAgentLoopRequest,
        agent_run_api: Arc<dyn AgentRunApi>,
    ) -> StartedAgentLoop {
        let (completion_sender, completion_wait) = oneshot::channel();
        let (cancel_handle, cancel_signal) = agent_loop_cancel_pair();
        let run_outputs = self
            .stream_sink_factory
            .as_ref()
            .and_then(|factory| factory(&request))
            .map_or_else(
                || self.run_outputs.clone(),
                |sink| self.run_outputs.clone().with_stream(Some(sink)),
            );
        let loop_executor = AgentLoopExecutor::new(AgentLoopExecutorInput {
            provider_stream_source: self.provider_stream_source.clone(),
            tool_registry_factory: Arc::clone(&self.tool_registry_factory),
            execution_metadata_reader: Arc::clone(&self.execution_metadata_reader),
            cancel_signal,
            background_sessions: self.background_sessions.clone(),
            run_outputs,
            agent_run_api,
        });

        tokio::spawn(async move {
            let outcome = loop_executor.execute_agent_loop(request).await;
            let _ignored = completion_sender.send(outcome);
        });

        StartedAgentLoop {
            completion: AgentLoopCompletion::new(async move {
                completion_wait.await.unwrap_or_else(|_| AgentLoopOutcome {
                    kind: AgentLoopOutcomeKind::LoopFailed {
                        error_summary: "agent loop outcome sender dropped".to_owned(),
                    },
                    final_conversation_messages: Vec::new(),
                    total_token_count: None,
                })
            }),
            cancellation: cancel_handle,
        }
    }
}

#[cfg(test)]
#[path = "../../tests/agent_loop/launcher/mod.rs"]
mod tests;
