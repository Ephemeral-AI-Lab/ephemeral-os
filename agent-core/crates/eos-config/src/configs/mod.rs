//! Typed config schemas and reusable section fragments.
//!
//! Each type owns its validation. These live here for now; they migrate to their
//! owning crates' `config.rs` (`eos-db`, `eos-llm-client`, `eos-workflow`) as
//! those crates stabilize. There is no aggregate struct — consumers deserialize
//! one section at a time via
//! [`ConfigDocument::section`](crate::ConfigDocument::section).

mod attempt;
mod database;
mod models;
mod providers;
mod runtime;
mod workflow;

pub use attempt::AttemptConfig;
pub use database::{DatabaseConfig, DatabaseUrl, DEFAULT_SQLITE_DATABASE_URL};
pub use models::{ModelRegistrationConfig, ModelsConfig};
pub use providers::{
    AnthropicApiConfig, ClaudeCodingPlanConfig, CodexCodingPlanConfig, OpenAiApiConfig,
    ProviderKind, ProvidersConfig, RetryConfig, SecretConfigValue,
};
pub use runtime::RuntimeConfig;
pub use workflow::{WorkflowConfig, DEFAULT_WORKFLOW_MAX_DEPTH};
