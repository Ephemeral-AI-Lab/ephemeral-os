//! `build_test_state`: a fully-wired [`AppState`] over a temp SQLite db, the
//! fake provisioner/transport, the given agent registry, and an optional
//! event-source factory. `.provisioner(...)` is plain `pub` (TESTING_SPEC §14.4),
//! so no `test-util` feature is required.

use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::{AgentDefinition, AgentRegistry};
use eos_engine::EventSourceFactory;
use eos_runtime::{AppState, RequestProvisioner, RequestSandboxBinding};
use eos_types::RequestId;

use crate::agents::test_tools_root;
use crate::sandbox::FakeTransport;

/// A provisioner that binds a fixed sandbox id without touching Docker.
#[derive(Debug)]
pub struct FakeProvisioner {
    /// The sandbox id bound when the caller passes none (or a blank id).
    pub id: String,
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

/// Build a fully-wired test [`AppState`] over a temp SQLite db, the fake
/// provisioner/transport, the given agent registry, and an optional event-source
/// factory. Returns the state and the owning temp dir (keep it alive for the
/// test's duration).
pub async fn build_test_state(
    factory: Option<EventSourceFactory>,
    agents: Vec<AgentDefinition>,
) -> (AppState, tempfile::TempDir) {
    let dir = tempfile::tempdir().expect("tempdir");
    let url = format!("sqlite://{}", dir.path().join("test.db").display());
    let registry: AgentRegistry = agents.into_iter().collect();
    let mut builder = AppState::builder()
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
