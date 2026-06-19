//! Implementations for the workspace network topologies.
//!
//! Both adapters create private overlay-backed workspaces. Host mode shares the
//! host network namespace; isolated mode adds a dedicated network namespace and
//! network plumbing. Workspace lifetime and publish behavior are caller-owned.

pub mod host;
pub mod isolated_network;
