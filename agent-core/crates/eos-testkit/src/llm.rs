//! Scripted LLM doubles: an [`EventSource`] that replays queued turns, the
//! [`EventSourceFactory`] builders that dispatch scripts per agent, and the
//! `tool_use_turn` / `text_turn` helpers that fabricate one model turn.
//!
//! This is the single definition of `ScriptedSource` in the workspace
//! (TESTING_SPEC AC3); the prior `eos-engine` and `eos-runtime` copies are gone.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::AgentDefinition;
use eos_engine::{
    AssistantMessageComplete, EngineError, EngineStream, EventSource, EventSourceFactory,
    StreamEvent,
};
use eos_llm_client::{ContentBlock, LlmRequest, Message, MessageRole, UsageSnapshot};

/// A scripted event source: each `stream()` call replays the next queued turn.
/// When `block_when_empty` is set, an exhausted source blocks forever instead of
/// returning an empty turn (keeps the agent "running" for park-and-inspect
/// tests).
#[derive(Debug)]
pub struct ScriptedSource {
    turns: tokio::sync::Mutex<Vec<Vec<StreamEvent>>>,
    block_when_empty: bool,
}

impl ScriptedSource {
    /// Replay `turns` in order; an exhausted source returns an empty turn.
    #[must_use]
    pub fn new(turns: Vec<Vec<StreamEvent>>) -> Self {
        Self {
            turns: tokio::sync::Mutex::new(turns),
            block_when_empty: false,
        }
    }

    /// Replay `turns`, then block forever (the agent stays running).
    #[must_use]
    pub fn new_blocking(turns: Vec<Vec<StreamEvent>>) -> Self {
        Self {
            turns: tokio::sync::Mutex::new(turns),
            block_when_empty: true,
        }
    }
}

#[async_trait]
impl EventSource for ScriptedSource {
    async fn stream(&self, _request: &LlmRequest) -> Result<EngineStream, EngineError> {
        let mut turns = self.turns.lock().await;
        if turns.is_empty() {
            if self.block_when_empty {
                drop(turns);
                std::future::pending::<()>().await;
                unreachable!("pending future never resolves");
            }
            return Ok(Box::pin(futures::stream::iter(Vec::new())));
        }
        let events = turns.remove(0);
        Ok(Box::pin(futures::stream::iter(events.into_iter().map(Ok))))
    }
}

/// An event source whose `stream()` never resolves; holds an agent open so a
/// test can park and abort the spawned run.
#[derive(Debug)]
pub struct BlockingSource;

#[async_trait]
impl EventSource for BlockingSource {
    async fn stream(&self, _request: &LlmRequest) -> Result<EngineStream, EngineError> {
        std::future::pending::<()>().await;
        unreachable!("pending future never resolves")
    }
}

/// A factory that always returns the given scripted turns.
#[must_use]
pub fn factory_from(turns: Vec<Vec<StreamEvent>>) -> EventSourceFactory {
    Arc::new(move |_def: &AgentDefinition| {
        Arc::new(ScriptedSource::new(turns.clone())) as Arc<dyn EventSource>
    })
}

/// A factory where the `root` agent plays `root_turns` then blocks (stays
/// running), and every other agent gets an empty (first-turn-erroring) source.
#[must_use]
pub fn factory_root_blocks_after(root_turns: Vec<Vec<StreamEvent>>) -> EventSourceFactory {
    Arc::new(move |def: &AgentDefinition| {
        if def.name.as_str() == "root" {
            Arc::new(ScriptedSource::new_blocking(root_turns.clone())) as Arc<dyn EventSource>
        } else {
            Arc::new(ScriptedSource::new(Vec::new())) as Arc<dyn EventSource>
        }
    })
}

/// A factory that dispatches scripted turns by agent name; an agent absent from
/// the map gets an empty (first-turn-erroring) source.
#[must_use]
pub fn factory_by_agent(by_agent: Vec<(&'static str, Vec<Vec<StreamEvent>>)>) -> EventSourceFactory {
    let scripts: HashMap<String, Vec<Vec<StreamEvent>>> = by_agent
        .into_iter()
        .map(|(name, turns)| (name.to_owned(), turns))
        .collect();
    Arc::new(move |def: &AgentDefinition| {
        let turns = scripts.get(def.name.as_str()).cloned().unwrap_or_default();
        Arc::new(ScriptedSource::new(turns)) as Arc<dyn EventSource>
    })
}

/// One model turn that calls `tool_name` with `input` (a non-object `input`
/// lowers to an empty object).
#[must_use]
pub fn tool_use_turn(tool_use_id: &str, tool_name: &str, input: serde_json::Value) -> Vec<StreamEvent> {
    let input = match input {
        serde_json::Value::Object(map) => map,
        _ => eos_types::JsonObject::new(),
    };
    vec![StreamEvent::AssistantMessageComplete {
        agent_name: String::new(),
        agent_run_id: None,
        payload: Box::new(AssistantMessageComplete {
            message: Message {
                role: MessageRole::Assistant,
                content: vec![ContentBlock::ToolUse {
                    tool_use_id: tool_use_id.parse().expect("tool use id"),
                    name: tool_name.to_owned(),
                    input,
                }],
            },
            usage: UsageSnapshot::default(),
            stop_reason: None,
        }),
    }]
}

/// One assistant text turn (no tool call).
#[must_use]
pub fn text_turn(text: &str) -> Vec<StreamEvent> {
    vec![StreamEvent::AssistantMessageComplete {
        agent_name: String::new(),
        agent_run_id: None,
        payload: Box::new(AssistantMessageComplete {
            message: Message {
                role: MessageRole::Assistant,
                content: vec![ContentBlock::Text {
                    text: text.to_owned(),
                }],
            },
            usage: UsageSnapshot::default(),
            stop_reason: None,
        }),
    }]
}
