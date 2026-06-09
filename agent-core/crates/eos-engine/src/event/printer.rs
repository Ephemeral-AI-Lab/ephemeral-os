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

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use eos_types::{AgentRunId, JsonObject};

    use super::*;

    #[test]
    fn printer_renders_midflight_tool_events_without_records() {
        let lines = Arc::new(Mutex::new(Vec::new()));
        let captured = lines.clone();
        let printer = EngineEventPrinter::new(move |line| {
            captured.lock().expect("lines lock").push(line);
        });

        printer.print(&StreamEvent::ToolExecutionStarted {
            agent_name: "root".to_owned(),
            agent_run_id: Some(AgentRunId::new_v4()),
            tool_name: "submit_root_outcome".to_owned(),
            tool_input: JsonObject::new(),
            tool_use_id: "toolu_1".parse().expect("valid tool use id"),
        });

        assert_eq!(
            lines.lock().expect("lines lock").as_slice(),
            ["tool started: submit_root_outcome"]
        );
    }
}
