//! The provider registry as explicit application state (GC-02), plus the
//! Docker-only provider-selection resolver.
//!
//! The composition root builds one `Arc<ProviderRegistry>` and seeds the single
//! provider adapter. The seed is first-call-wins, so no separate bootstrap
//! singleton is needed.

use std::sync::Arc;

use parking_lot::RwLock;

use crate::error::SandboxHostError;
use crate::provider::{ProviderAdapter, ProviderKind};

/// The sandbox provider kind. agent-core is Docker-only, and sandbox
/// configuration (including any provider selection) is owned by the ephemeral-os
/// sandbox module — so this is a fixed host-side constant, not central config.
#[must_use]
pub fn resolve_provider_kind() -> ProviderKind {
    ProviderKind::Docker
}

/// Process-local holder for the single provider adapter, held as
/// `Arc<ProviderRegistry>` and seeded once at the composition root.
///
/// The host is Docker-only, so there is exactly one adapter and no per-sandbox
/// routing. Reads dominate, so the slot lives behind `parking_lot::RwLock`
/// (`own-rwlock-readers`); every read clones the `Arc` out and drops the guard
/// before any `.await` (`async-no-lock-await`).
#[derive(Debug, Default)]
pub struct ProviderRegistry {
    adapter: RwLock<Option<Arc<dyn ProviderAdapter>>>,
}

impl ProviderRegistry {
    /// Construct an empty registry (no adapter seeded).
    #[must_use]
    pub fn new() -> Self {
        Self {
            adapter: RwLock::new(None),
        }
    }

    /// Seed the provider adapter. The first seed wins: repeat calls are no-ops,
    /// with a warning if a different provider kind tries to replace the live one.
    pub fn set_default(&self, adapter: Arc<dyn ProviderAdapter>) {
        let mut slot = self.adapter.write();
        if let Some(existing) = slot.as_ref() {
            if existing.kind() != adapter.kind() {
                tracing::warn!(
                    first = existing.kind().as_str(),
                    now = adapter.kind().as_str(),
                    "sandbox provider already seeded; ignoring replacement"
                );
            }
            return;
        }
        *slot = Some(adapter);
    }

    /// The provider adapter, or [`SandboxHostError::NoDefaultProvider`] if the
    /// registry has not been seeded.
    pub fn adapter(&self) -> Result<Arc<dyn ProviderAdapter>, SandboxHostError> {
        self.adapter
            .read()
            .clone()
            .ok_or(SandboxHostError::NoDefaultProvider)
    }
}

#[cfg(test)]
#[path = "../tests/registry/mod.rs"]
mod tests;
