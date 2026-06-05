//! Shared agent-core test doubles.
//!
//! One home for the doubles the per-crate mock tests (and, later, the root
//! `integration-test` module) substitute at the three external I/O edges:
//! the LLM (`EventSource`), the daemon RPC (`SandboxTransport`), and request
//! provisioning (`RequestProvisioner`). Consumed as a `[dev-dependencies]`
//! crate; its `src/` is test infrastructure, so no production crate carries
//! test-support code in its own `src/`.
//!
//! Features: `llm` (scripted `EventSource` doubles, public-API only) and
//! `mock-state` (`build_test_state` + `FakeProvisioner`, which pull
//! `eos-runtime`'s `test-util` seam).
#![allow(clippy::unwrap_used, clippy::expect_used)]

mod agents;
mod llm;
mod sandbox;
#[cfg(feature = "mock-state")]
mod state;

pub use agents::{agent_def, test_tools_root};
pub use llm::{
    factory_by_agent, factory_from, factory_root_blocks_after, tool_use_turn, BlockingSource,
    ScriptedSource,
};
pub use sandbox::FakeTransport;
#[cfg(feature = "mock-state")]
pub use state::{build_test_state, FakeProvisioner};
