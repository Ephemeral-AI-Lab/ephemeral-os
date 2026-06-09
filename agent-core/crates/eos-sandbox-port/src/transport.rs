//! The `SandboxTransport` DIP seam (anchor §6).
//!
//! One async RPC boundary to the sandbox daemon. This crate declares the trait;
//! `eos-sandbox-host` implements the daemon-backed concrete (`DaemonSandboxTransport`)
//! and stamps the wire-level protocol version; backend composition injects it as
//! `Arc<dyn SandboxTransport>`. The tool-dispatch helpers depend only on
//! `&dyn SandboxTransport`, never on a concrete client.

use async_trait::async_trait;
use eos_types::{JsonObject, SandboxId};

use crate::error::SandboxPortError;
use crate::ops::DaemonOp;
use crate::tool_dispatch::{plugin_ensure_payload, PluginPackageEnsureRequest};

/// One sandbox RPC boundary. Implemented in `eos-sandbox-host` by the daemon
/// client and (in tests) by an in-memory mock.
///
/// Uses `#[async_trait]` because it is stored as `Arc<dyn SandboxTransport>` at
/// the composition root; it is intentionally **not** sealed (`eos-sandbox-host`
/// is an external implementor by design).
#[async_trait]
pub trait SandboxTransport: Send + Sync {
    /// Call one sandbox RPC. The implementor stamps a wire-level protocol
    /// version and reuses any `invocation_id` already present in `payload` for
    /// engine/daemon in-flight correlation.
    async fn call(
        &self,
        sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError>;

    /// Call one dynamically named sandbox RPC, used for plugin operations such
    /// as `plugin.lsp.hover` whose operation names are manifest/catalog data
    /// rather than built-in daemon enum variants.
    async fn call_dynamic(
        &self,
        _sandbox_id: &SandboxId,
        op: &str,
        _payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        Err(SandboxPortError::transport(
            None,
            format!("dynamic sandbox op {op:?} is not supported by this transport"),
        ))
    }

    /// Ensure a plugin package is available before plugin operation dispatch.
    ///
    /// The default implementation performs the daemon warm ensure. Host-backed
    /// transports override this to keep package upload private to the cold path.
    async fn ensure_plugin_package(
        &self,
        sandbox_id: &SandboxId,
        request: PluginPackageEnsureRequest,
    ) -> Result<JsonObject, SandboxPortError> {
        let timeout_s = request.timeout_s;
        let payload = plugin_ensure_payload(request.into_plugin_ensure_request(None))?;
        self.call(sandbox_id, DaemonOp::PluginEnsure, payload, timeout_s)
            .await
    }
}

#[cfg(test)]
pub(crate) mod mock {
    //! An in-memory `SandboxTransport` returning a canned outcome, used by the
    //! tool-dispatch conflict tests.

    use super::{async_trait, DaemonOp, JsonObject, SandboxId, SandboxPortError, SandboxTransport};

    pub(crate) struct MockTransport {
        outcome: Result<JsonObject, SandboxPortError>,
    }

    impl MockTransport {
        pub(crate) fn err(error: SandboxPortError) -> Self {
            Self {
                outcome: Err(error),
            }
        }
    }

    #[async_trait]
    impl SandboxTransport for MockTransport {
        async fn call(
            &self,
            _sandbox_id: &SandboxId,
            _op: DaemonOp,
            _payload: JsonObject,
            _timeout_s: u32,
        ) -> Result<JsonObject, SandboxPortError> {
            self.outcome.clone()
        }

        async fn call_dynamic(
            &self,
            _sandbox_id: &SandboxId,
            _op: &str,
            _payload: JsonObject,
            _timeout_s: u32,
        ) -> Result<JsonObject, SandboxPortError> {
            self.outcome.clone()
        }
    }
}
