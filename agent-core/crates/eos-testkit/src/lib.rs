//! Shared agent-core test doubles.
//!
//! The single home for the doubles per-crate mock tests substitute at the three
//! external I/O edges — the LLM ([`EventSource`](eos_engine::EventSource)), the
//! daemon RPC ([`SandboxTransport`](eos_sandbox_api::SandboxTransport)), and
//! request provisioning ([`RequestProvisioner`](eos_runtime::RequestProvisioner))
//! — plus the Layer-B workflow runner/store doubles. Consumed as a
//! `[dev-dependencies]` crate, so its `src/` *is* test infrastructure and no
//! production crate carries test-support code in its own `src/` (TESTING_SPEC
//! I2).
//!
//! Features (TESTING_SPEC §6.1): `llm` (scripted `EventSource` doubles + the
//! `run_until` stepper, public-API only), `mock-state` (`build_test_state` +
//! `FakeProvisioner`, pulling `eos-runtime`), and `workflow` (the Layer-B
//! `AgentRunner`/store doubles + `wait_until`, pulling `eos-workflow`). Each
//! production crate enables only the feature it needs.
#![allow(clippy::unwrap_used, clippy::expect_used)]

#[cfg(feature = "llm")]
mod agents;
#[cfg(feature = "llm")]
mod engine;
#[cfg(feature = "llm")]
mod llm;
#[cfg(feature = "llm")]
mod sandbox;
#[cfg(feature = "mock-state")]
mod state;

#[cfg(feature = "llm")]
pub use agents::{agent_def, test_tools_root};
#[cfg(feature = "llm")]
pub use engine::run_until;
#[cfg(feature = "llm")]
pub use llm::{
    factory_by_agent, factory_from, factory_root_blocks_after, text_turn, tool_use_turn,
    BlockingSource, ScriptedSource,
};
#[cfg(feature = "llm")]
pub use sandbox::FakeTransport;
#[cfg(feature = "mock-state")]
pub use state::{build_test_state, FakeProvisioner};
