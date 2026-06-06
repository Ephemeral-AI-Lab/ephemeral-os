//! `eos-backend-config` — backend deployment and sandbox-lifecycle config.
//!
//! Owns [`ServerConfig`] (`bind`, `backend_db_path`, [`AgentCoreConfigSource`],
//! [`SandboxConfig`], [`ObsConfig`]) loaded from `backend.yml < local.yml`.
//! Config ownership stays decentralized (AC11): provider and workflow schema
//! belong to agent-core's `eos-config`, daemon/runtime schema to sandbox's
//! config, and only backend deployment + sandbox lifecycle defaults live here.
#![warn(missing_docs)]

mod loader;
mod obs;
mod sandbox;
mod server;

pub use loader::{load, load_from_paths, ConfigError};
pub use obs::ObsConfig;
pub use sandbox::SandboxConfig;
pub use server::{AgentCoreConfigSource, ServerConfig};
