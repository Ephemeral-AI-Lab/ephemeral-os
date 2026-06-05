//! Fake daemon transport: every op resolves to an empty payload, so
//! `command_session_count` is 0 and the no-inflight hook stays happy without a
//! live daemon.

use async_trait::async_trait;
use eos_sandbox_api::{DaemonOp, SandboxApiError, SandboxTransport};
use eos_types::{JsonObject, SandboxId};

#[derive(Debug, Default)]
pub struct FakeTransport;

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
