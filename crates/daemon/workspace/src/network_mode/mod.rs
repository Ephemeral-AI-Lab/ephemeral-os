//! Implementations for workspace isolation profiles.
//!
//! Both adapters create private overlay-backed workspaces. Host mode preserves
//! host network access; isolated mode adds a dedicated network boundary and
//! network plumbing. Workspace lifetime and publish behavior are caller-owned.

pub mod host;
pub mod isolated_network;
