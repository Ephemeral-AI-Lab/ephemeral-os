//! The single typed error enum for this crate (spec-conventions §8,
//! `err-thiserror-lib`).

use std::path::PathBuf;

use crate::model::AgentRole;

/// Failures raised when loading, parsing, or validating an agent profile.
///
/// `#[non_exhaustive]` because the set may grow (`api-non-exhaustive`); messages
/// are lowercase with no trailing punctuation (`err-lowercase-msg`) and chain the
/// underlying cause via `#[source]` (`err-source-chain`).
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum AgentDefError {
    /// A profile `.md` omitted the required `role:` frontmatter field.
    #[error("agent profile {} is missing required 'role' frontmatter", path.display())]
    MissingRole {
        /// The profile file that omitted `role:`.
        path: PathBuf,
    },

    /// A resolved agent `name` was empty after trimming surrounding whitespace.
    #[error("agent name must be non-empty")]
    EmptyName,

    /// `terminals` was empty (or all-blank) — every agent must declare at least
    /// one terminal-capable tool.
    #[error("agent definition terminals must be non-empty")]
    EmptyTerminals,

    /// `tool_call_limit` was not strictly positive.
    #[error("tool_call_limit must be positive")]
    NonPositiveToolCallLimit,

    /// The profile file could not be read from disk.
    #[error("could not read agent profile {}", path.display())]
    Read {
        /// The file (or directory) that could not be read.
        path: PathBuf,
        /// The underlying I/O failure.
        #[source]
        cause: std::io::Error,
    },

    /// The YAML frontmatter failed to parse, or carried an unknown key.
    #[error("invalid frontmatter in {}", path.display())]
    Frontmatter {
        /// The profile file whose frontmatter failed to parse.
        path: PathBuf,
        /// The underlying YAML deserialization failure.
        #[source]
        cause: serde_yaml::Error,
    },

    /// A declared `skill:` path did not resolve to an existing file.
    #[error("agent profile {} declares skill {declared}, but {} does not exist", path.display(), resolved.display())]
    SkillNotFound {
        /// The profile file that declared the skill.
        path: PathBuf,
        /// The relative `skill:` value as authored.
        declared: String,
        /// The resolved (absolute) path that did not exist.
        resolved: PathBuf,
    },

    /// A `context_recipe` was declared by a role that has no context builder
    /// (anything outside `{planner, generator, reducer}`).
    #[error(
        "agent {agent} declares context_recipe {recipe}, but role {role} has no context builder"
    )]
    RecipeRoleMismatch {
        /// The offending agent's name.
        agent: String,
        /// The declared recipe id.
        recipe: String,
        /// The agent's role, which has no context builder.
        role: AgentRole,
    },

    /// One or more declared skill files violated the terminal-silence contract.
    #[error("skill-file lint failed")]
    SkillLint {
        /// One human-readable description per violation.
        violations: Vec<String>,
    },
}
