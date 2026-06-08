//! eos-agent-def — agent profile definitions, the Markdown+frontmatter loader,
//! the read-only agent registry, and the pure fragments of profile validation.
//!
//! This near-leaf crate owns filesystem loading and validation for agent
//! profiles. Passive agent DTOs and the read-only registry are contract-floor
//! types in `eos-types` and are re-exported here during the migration.
//!
//! It deliberately does **not** build `ToolSpec`s, resolve the `model: inherit`
//! sentinel, materialize the effective visible tool set, own the
//! `allowed_tools ∪ terminals` union policy, or run the `context_recipe` catalog
//! check — those live in `eos-engine` / `eos-workflow` / `eos-runtime`.
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
