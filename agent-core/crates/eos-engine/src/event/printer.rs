//! Engine event rendering.

use std::sync::Arc;

use super::StreamEvent;

type EngineEventPrintSink = Arc<dyn Fn(String) + Send + Sync>;

/// Renders engine events into a caller-provided sink.
#[derive(Clone)]
pub struct EngineEventPrinter {
    sink: EngineEventPrintSink,
}

impl std::fmt::Debug for EngineEventPrinter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EngineEventPrinter").finish_non_exhaustive()
    }
}

impl EngineEventPrinter {
    /// Create a printer from a text sink.
    #[must_use]
    pub fn new<F>(sink: F) -> Self
    where
        F: Fn(String) + Send + Sync + 'static,
    {
        Self {
            sink: Arc::new(sink),
        }
    }

    /// Render and emit one engine event.
    pub fn print(&self, event: &StreamEvent) {
        (self.sink)(render_engine_event(event));
    }
}

fn render_engine_event(event: &StreamEvent) -> String {
    match event {
        StreamEvent::ReasoningDelta { text, .. }
        | StreamEvent::AssistantTextDelta { text, .. }
        | StreamEvent::SystemNotification { text, .. } => text.clone(),
        StreamEvent::AssistantMessageComplete { .. } => "assistant message complete".to_owned(),
        StreamEvent::ToolUseDelta { name, .. } => format!("tool use: {name}"),
        StreamEvent::ToolExecutionStarted { tool_name, .. } => format!("tool started: {tool_name}"),
        StreamEvent::ToolExecutionCompleted {
            tool_name,
            is_error,
            is_terminal,
            ..
        } => {
            format!("tool completed: {tool_name} error={is_error} terminal={is_terminal}")
        }
        StreamEvent::ToolExecutionProgress {
            tool_name, output, ..
        } => {
            format!("tool progress: {tool_name}: {output}")
        }
        StreamEvent::ToolExecutionCancelled {
            tool_name, reason, ..
        } => {
            format!("tool cancelled: {tool_name}: {reason}")
        }
    }
}
