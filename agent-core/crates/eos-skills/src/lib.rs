//! eos-skills — skill definitions, the skill registry, and the config-rooted
//! loader.
//!
//! This near-leaf crate owns the **runtime skill content** exposed to agents: the
//! [`SkillDefinition`] value type, the [`SkillRegistry`] lookup, and a
//! deterministic loader that reads directory-based skills
//! (`<skill-name>/SKILL.md` plus an optional `references/*.md` set) from a single
//! configured skill root. Its sole job is to load skill definitions into an
//! immutable in-memory registry and answer lookups by name; it is the source of
//! truth for the `references` content the `load_skill_reference` tool (owned by
//! `eos-tools`) serves.
//!
//! It deliberately does **not** own the `load_skill_reference` `ToolSpec` or
//! executor, know about agent-to-skill binding or allowlist scoping, build the
//! launch-time skill message, traverse outside the configured root, or
//! watch/reload at runtime. See
//! `docs/plans/backend_agent_core_rust_migration/impl-eos-skills.md`.
//!
//! The public surface is re-exported flatly:
//! `use eos_skills::{SkillDefinition, SkillRegistry};`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod bundled;
mod definition;
mod error;
mod loader;
mod registry;
#[cfg(test)]
mod test_support;

pub use definition::{ReferenceName, SkillDefinition, SkillName, SkillSource};
pub use error::SkillLoadError;
pub use registry::SkillRegistry;
