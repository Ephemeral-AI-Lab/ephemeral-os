//! Request-scoped sandbox provisioning: `prepare_for_run` either starts an
//! explicit sandbox id or creates a fresh one labelled `origin=workflow,
//! request_id=<id>`. Faithful port of `runtime/sandbox_provisioning.py`.
//!
//! The Python `create_fn`/`start_fn` callable injection is dropped in favor of
//! typed calls into [`SandboxLifecycle`] (wired by `eos-runtime`); test
//! substitutability comes from the `#[cfg(test)]` mock adapter. The Python
//! `RuntimeError("create_sandbox returned no id")` branch is eliminated by
//! typing: [`SandboxInfo::id`](crate::SandboxInfo) is a non-empty `SandboxId`,
//! so a created sandbox always has a valid id (parse-don't-validate).

use std::sync::Arc;

use eos_types::{RequestId, SandboxId};

use crate::error::SandboxHostError;
use crate::lifecycle::SandboxLifecycle;
use crate::provider::{CreateSandboxSpec, Labels};

/// The resolved sandbox↔request binding produced by the provisioner.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestSandboxBinding {
    /// The sandbox the request runs in.
    pub sandbox_id: SandboxId,
    /// The originating request.
    pub request_id: RequestId,
}

/// Builds the create spec for the fresh-sandbox branch: a `request-<8 hex>`
/// name and the `origin=workflow, request_id=<id>` labels (AC-09).
pub(crate) fn fresh_create_spec(request_id: &RequestId) -> CreateSandboxSpec {
    let mut labels = Labels::new();
    labels.insert("origin".to_owned(), "workflow".to_owned());
    labels.insert("request_id".to_owned(), request_id.to_string());
    CreateSandboxSpec {
        name: format!(
            "request-{}",
            &uuid::Uuid::new_v4().simple().to_string()[..8]
        ),
        labels,
        ..Default::default()
    }
}

/// Provisions the sandbox a request runs in, over the typed lifecycle seam.
#[derive(Debug)]
pub struct RequestSandboxProvisioner {
    lifecycle: Arc<SandboxLifecycle>,
}

impl RequestSandboxProvisioner {
    /// Build a provisioner over a shared lifecycle.
    #[must_use]
    pub fn new(lifecycle: Arc<SandboxLifecycle>) -> Self {
        Self { lifecycle }
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
            .create(&fresh_create_spec(request_id))
            .await?;
        Ok(RequestSandboxBinding {
            sandbox_id: info.id,
            request_id: request_id.clone(),
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::path::PathBuf;
    use std::sync::Arc;

    use super::*;
    use crate::daemon_client::DaemonClient;
    use crate::registry::ProviderRegistry;
    use crate::testutil::MockAdapter;

    fn provisioner(adapter: MockAdapter) -> RequestSandboxProvisioner {
        let registry = ProviderRegistry::new();
        registry.set_default(Arc::new(adapter));
        let lifecycle = SandboxLifecycle::new(
            Arc::new(DaemonClient::new(Arc::new(registry))),
            PathBuf::from("/nonexistent"),
        );
        RequestSandboxProvisioner::new(Arc::new(lifecycle))
    }

    fn rid() -> RequestId {
        "req-1".parse().unwrap()
    }

    #[test]
    fn fresh_create_spec_has_request_name_and_labels() {
        let spec = fresh_create_spec(&rid());
        assert!(spec.name.starts_with("request-"));
        assert_eq!(spec.name.len(), "request-".len() + 8);
        assert!(spec.name["request-".len()..]
            .bytes()
            .all(|b| b.is_ascii_hexdigit()));
        assert_eq!(
            spec.labels.get("origin").map(String::as_str),
            Some("workflow")
        );
        assert_eq!(
            spec.labels.get("request_id").map(String::as_str),
            Some("req-1")
        );
        assert_eq!(spec.language, "python");
    }

    // AC-09: explicit-id path starts that id and binds it; fresh path creates and
    // binds the created id. (Setup is a no-op: the mock returns no project_dir.)
    #[tokio::test]
    async fn prepare_explicit_and_fresh() {
        // explicit id (whitespace-trimmed).
        let prov = provisioner(MockAdapter::new().with_id("box"));
        let binding = prov
            .prepare_for_run(&rid(), Some("  sb-explicit  "))
            .await
            .unwrap();
        assert_eq!(binding.sandbox_id.as_str(), "sb-explicit");
        assert_eq!(binding.request_id.as_str(), "req-1");

        // fresh create (blank/None id → create branch; binding uses the created id).
        let prov = provisioner(MockAdapter::new().with_id("created-box"));
        let binding = prov.prepare_for_run(&rid(), None).await.unwrap();
        assert_eq!(binding.sandbox_id.as_str(), "created-box");
        assert_eq!(binding.request_id.as_str(), "req-1");

        // whitespace-only id is treated as "no id" (create branch).
        let prov = provisioner(MockAdapter::new().with_id("created-box"));
        let binding = prov.prepare_for_run(&rid(), Some("   ")).await.unwrap();
        assert_eq!(binding.sandbox_id.as_str(), "created-box");
    }
}
