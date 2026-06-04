use std::num::NonZeroU32;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::{AgentDefinition, AgentName, AgentRole, AgentType};
use eos_engine::{EngineError, EngineStream, EventSource, StreamEvent};
use eos_llm_client::LlmRequest;
use eos_sandbox_api::{DaemonOp, SandboxApiError, SandboxTransport};
use eos_sandbox_host::RequestSandboxBinding;
use eos_types::{JsonObject, RequestId, SandboxId};

use super::{EventSourceFactory, RequestProvisioner};

#[test]
fn eosd_artifact_dir_is_repo_sandbox_dist() {
    assert_eq!(
        super::default_eosd_artifact_dir("/repo"),
        std::path::PathBuf::from("/repo/sandbox/dist")
    );
}

/// A sandbox transport that returns an empty payload for every op (so
/// `command_session_count` resolves to 0) - keeps the no-inflight hook happy
/// without a live daemon.
#[derive(Debug, Default)]
pub(crate) struct FakeTransport;

#[async_trait]
impl SandboxTransport for FakeTransport {
    async fn call(
        &self,
        _sandbox_id: &SandboxId,
        _op: DaemonOp,
        _payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxApiError> {
        Ok(JsonObject::new())
    }
}

/// A scripted event source: each `stream()` call replays the next queued
/// turn. When `block_when_empty` is set, an exhausted source blocks forever
/// instead of returning an empty turn (keeps the agent "running").
#[derive(Debug)]
pub(crate) struct ScriptedSource {
    turns: tokio::sync::Mutex<Vec<Vec<StreamEvent>>>,
    block_when_empty: bool,
}

impl ScriptedSource {
    pub(crate) fn new(turns: Vec<Vec<StreamEvent>>) -> Self {
        Self {
            turns: tokio::sync::Mutex::new(turns),
            block_when_empty: false,
        }
    }

    pub(crate) fn new_blocking(turns: Vec<Vec<StreamEvent>>) -> Self {
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

/// An event source whose `stream()` never resolves; used to hold a root agent
/// open so a test can abort the spawned task (join-error path, AC-03b).
#[derive(Debug)]
pub(crate) struct BlockingSource;

#[async_trait]
impl EventSource for BlockingSource {
    async fn stream(&self, _request: &LlmRequest) -> Result<EngineStream, EngineError> {
        std::future::pending::<()>().await;
        unreachable!("pending future never resolves")
    }
}

/// A provisioner that binds a fixed sandbox id without touching Docker.
#[derive(Debug)]
pub(crate) struct FakeProvisioner {
    pub(crate) id: String,
}

#[async_trait]
impl RequestProvisioner for FakeProvisioner {
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> anyhow::Result<RequestSandboxBinding> {
        let resolved = sandbox_id
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(&self.id);
        Ok(RequestSandboxBinding {
            sandbox_id: resolved.parse()?,
            request_id: request_id.clone(),
        })
    }
}

/// Build a minimal agent definition for tests.
pub(crate) fn agent_def(
    name: &str,
    role: AgentRole,
    allowed: &[&str],
    terminals: &[&str],
) -> AgentDefinition {
    AgentDefinition {
        name: AgentName::new(name).expect("name"),
        description: name.to_owned(),
        system_prompt: Some("test profile".to_owned()),
        model: Some("test-model".to_owned()),
        tool_call_limit: NonZeroU32::new(8).expect("nonzero"),
        role,
        agent_type: AgentType::Agent,
        allowed_tools: allowed.iter().map(|s| (*s).to_owned()).collect(),
        terminals: terminals.iter().map(|s| (*s).to_owned()).collect(),
        notification_triggers: Vec::new(),
        skill: None,
        context_recipe: None,
    }
}

/// An event-source factory that always returns the given scripted turns.
pub(crate) fn factory_from(turns: Vec<Vec<StreamEvent>>) -> EventSourceFactory {
    Arc::new(move |_def: &AgentDefinition| {
        Arc::new(ScriptedSource::new(turns.clone())) as Arc<dyn EventSource>
    })
}

/// A factory where the `root` agent plays `root_turns` then blocks (stays
/// running), and every other agent gets an empty source (errors on first
/// turn). Used by the delegation test (AC-05).
pub(crate) fn factory_root_blocks_after(root_turns: Vec<Vec<StreamEvent>>) -> EventSourceFactory {
    Arc::new(move |def: &AgentDefinition| {
        if def.name.as_str() == "root" {
            Arc::new(ScriptedSource::new_blocking(root_turns.clone())) as Arc<dyn EventSource>
        } else {
            Arc::new(ScriptedSource::new(Vec::new())) as Arc<dyn EventSource>
        }
    })
}

/// A factory that dispatches scripted turns by agent name; an agent absent
/// from the map gets an empty (first-turn-erroring) source. Used by the
/// advisor e2e test, where `root` and `advisor` need distinct turn scripts.
pub(crate) fn factory_by_agent(
    by_agent: Vec<(&'static str, Vec<Vec<StreamEvent>>)>,
) -> EventSourceFactory {
    let scripts: std::collections::HashMap<String, Vec<Vec<StreamEvent>>> = by_agent
        .into_iter()
        .map(|(name, turns)| (name.to_owned(), turns))
        .collect();
    Arc::new(move |def: &AgentDefinition| {
        let turns = scripts.get(def.name.as_str()).cloned().unwrap_or_default();
        Arc::new(ScriptedSource::new(turns)) as Arc<dyn EventSource>
    })
}

/// One model turn that calls `tool_name` with `input`.
pub(crate) fn tool_use_turn(
    tool_use_id: &str,
    tool_name: &str,
    input: serde_json::Value,
) -> Vec<StreamEvent> {
    use eos_engine::AssistantMessageComplete;
    use eos_llm_client::{ContentBlock, Message, MessageRole, UsageSnapshot};

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

/// Build a fully-wired test `AppState` over a temp `SQLite` db, a fake
/// provisioner, the given agent registry, and an optional event-source
/// factory. Returns the state and the owning temp dir (keep it alive).
/// The repo's `.eos-agents/tools` tree, resolved relative to this crate's
/// manifest so the (mandatory) tool-config build path has a real source in
/// tests without depending on the process working directory.
pub(crate) fn test_tools_root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../.eos-agents/tools")
}

pub(crate) async fn build_test_state(
    factory: Option<EventSourceFactory>,
    agents: Vec<AgentDefinition>,
) -> (super::AppState, tempfile::TempDir) {
    use eos_agent_def::AgentRegistry;

    let dir = tempfile::tempdir().expect("tempdir");
    let url = format!("sqlite://{}", dir.path().join("test.db").display());
    let registry: AgentRegistry = agents.into_iter().collect();
    let mut builder = super::AppState::builder()
        .database_url(url)
        .cwd(dir.path().display().to_string())
        .tools_root(test_tools_root())
        .provisioner(Arc::new(FakeProvisioner {
            id: "sb-test".to_owned(),
        }))
        .transport(Arc::new(FakeTransport))
        .agent_registry(Arc::new(registry));
    if let Some(factory) = factory {
        builder = builder.event_source_factory(factory);
    }
    let state = builder.build().await.expect("build app state");
    (state, dir)
}
