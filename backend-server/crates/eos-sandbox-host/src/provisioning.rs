//! Request-scoped sandbox provisioning: `prepare_for_run` either starts an
//! explicit sandbox id or creates a fresh one labelled `origin=workflow,
//! request_id=<id>`.
//!
//! The production path uses typed calls into [`SandboxLifecycle`] wired by
//! the agent-core request service; test substitutability comes from the `#[cfg(test)]` mock
//! adapter. A created sandbox always has a valid id because
//! [`SandboxInfo::id`](crate::SandboxInfo) is a non-empty `SandboxId`
//! (parse-don't-validate).

use std::sync::Arc;

use async_trait::async_trait;
use eos_sandbox_port::{RequestProvisioner, RequestSandboxBinding, SandboxProvisionError};
use eos_types::{RequestId, SandboxId};

use crate::error::SandboxHostError;
use crate::lifecycle::SandboxLifecycle;
use crate::provider::{CreateSandboxSpec, Labels};

/// Builds the create spec for the fresh-sandbox branch: a `request-<8 hex>`
/// name, the `origin=workflow, request_id=<id>` labels, and the configured
/// Docker default snapshot when present (AC-09).
pub(crate) fn fresh_create_spec(
    request_id: &RequestId,
    default_snapshot: Option<&str>,
) -> CreateSandboxSpec {
    let mut labels = Labels::new();
    labels.insert("origin".to_owned(), "workflow".to_owned());
    labels.insert("request_id".to_owned(), request_id.to_string());
    CreateSandboxSpec {
        name: format!(
            "request-{}",
            &uuid::Uuid::new_v4().simple().to_string()[..8]
        ),
        snapshot: clean_optional_text(default_snapshot),
        labels,
        ..Default::default()
    }
}

fn clean_optional_text(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

/// Provisions the sandbox a request runs in, over the typed lifecycle seam.
#[derive(Debug)]
pub struct RequestSandboxProvisioner {
    lifecycle: Arc<SandboxLifecycle>,
    default_snapshot: Option<String>,
}

impl RequestSandboxProvisioner {
    /// Build a provisioner with the configured Docker default snapshot used for
    /// fresh sandbox creation when the caller did not provide an explicit id.
    #[must_use]
    pub fn with_default_snapshot(
        lifecycle: Arc<SandboxLifecycle>,
        default_snapshot: Option<&str>,
    ) -> Self {
        Self {
            lifecycle,
            default_snapshot: clean_optional_text(default_snapshot),
        }
    }

    /// Prepare the sandbox for a run: start an explicit id (return value
    /// discarded), or create a fresh labelled sandbox. `request_id` flows
    /// through unchanged (never trimmed); the explicit/created ids are trimmed.
    pub async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding, SandboxHostError> {
        let explicit_id = sandbox_id.map(str::trim).filter(|s| !s.is_empty());
        if let Some(explicit) = explicit_id {
            // The explicit id is non-empty, so it parses (SandboxId only rejects
            // empty). start's return value is intentionally discarded.
            let id: SandboxId = explicit
                .parse()
                .map_err(|_| SandboxHostError::InvalidRequest("empty sandbox id".to_owned()))?;
            self.lifecycle.start(&id).await?;
            return Ok(RequestSandboxBinding {
                sandbox_id: id,
                request_id: request_id.clone(),
            });
        }
        let info = self
            .lifecycle
            .create(&fresh_create_spec(
                request_id,
                self.default_snapshot.as_deref(),
            ))
            .await?;
        Ok(RequestSandboxBinding {
            sandbox_id: info.id,
            request_id: request_id.clone(),
        })
    }
}

#[async_trait]
impl RequestProvisioner for RequestSandboxProvisioner {
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding, SandboxProvisionError> {
        // Map the host's typed failure into the daemon-agnostic port error at
        // the trait boundary; the inherent method keeps its `SandboxHostError`.
        RequestSandboxProvisioner::prepare_for_run(self, request_id, sandbox_id)
            .await
            .map_err(|err| SandboxProvisionError::new(err.to_string()))
    }
}

#[cfg(test)]
#[path = "../tests/provisioning/mod.rs"]
mod tests;
