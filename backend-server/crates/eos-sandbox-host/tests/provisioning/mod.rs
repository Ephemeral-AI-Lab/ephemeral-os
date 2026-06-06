#![allow(clippy::unwrap_used)]
use std::path::PathBuf;
use std::sync::Arc;

use super::*;
use crate::daemon_client::DaemonClient;
use crate::registry::ProviderRegistry;
use crate::support::MockAdapter;

fn provisioner(adapter: MockAdapter) -> RequestSandboxProvisioner {
    let registry = ProviderRegistry::new();
    registry.set_default(Arc::new(adapter));
    let lifecycle = SandboxLifecycle::new(
        Arc::new(DaemonClient::new(Arc::new(registry))),
        PathBuf::from("/nonexistent"),
    );
    RequestSandboxProvisioner::with_default_snapshot(Arc::new(lifecycle), None)
}

fn rid() -> RequestId {
    "req-1".parse().unwrap()
}

#[test]
fn fresh_create_spec_has_request_name_and_labels() {
    let spec = fresh_create_spec(&rid(), None);
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
    assert!(spec.snapshot.is_none());
}

#[test]
fn fresh_create_spec_applies_configured_snapshot() {
    let spec = fresh_create_spec(&rid(), Some("  py:3.11  "));
    assert_eq!(spec.snapshot.as_deref(), Some("py:3.11"));

    let blank = fresh_create_spec(&rid(), Some("  "));
    assert!(blank.snapshot.is_none());
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
