//! eos-agent-def — agent profile definitions, the Markdown+frontmatter loader,
//! the read-only agent registry, and the pure fragments of profile validation.
//!
//! This near-leaf crate owns the static identity of an agent profile: the
//! [`AgentType`] / [`AgentRole`] vocabularies, the [`AgentName`] newtype, the
//! [`AgentDefinition`] value type (all fields from `agents/definition/model.py`),
//! the [`load_agents_dir`] / [`load_agents_tree`] loaders, and the
//! [`AgentRegistry`] lookup built via [`AgentRegistryBuilder`].
//!
//! It deliberately does **not** build `ToolSpec`s, resolve the `model: inherit`
//! sentinel, materialize the effective visible tool set, own the
//! `allowed_tools ∪ terminals` union policy, or run the `context_recipe` catalog
//! check — those live in `eos-engine` / `eos-workflow` / `eos-runtime`. See
//! `docs/plans/backend_agent_core_rust_migration/impl-eos-agent-def.md`.
//!
//! The public surface is re-exported flatly: `use eos_agent_def::{AgentDefinition,
//! AgentRegistry, load_agents_tree};`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod error;
mod loader;
mod model;
mod registry;
mod validation;

pub use error::AgentDefError;
pub use loader::{load_agents_dir, load_agents_tree};
pub use model::{AgentDefinition, AgentName, AgentRole, AgentType};
pub use registry::{AgentRegistry, AgentRegistryBuilder};
pub use validation::{check_context_recipe_role, skill_lint};
