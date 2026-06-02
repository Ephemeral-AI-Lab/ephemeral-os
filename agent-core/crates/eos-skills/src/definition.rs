//! The skill value type and its validated newtypes.
//!
//! A [`SkillDefinition`] is a faithful, immutable port of the Python
//! `@dataclass(frozen=True)` (`skills/core/types.py`): the same six fields, with
//! the stringly `source` lifted to a [`SkillSource`] enum and the name / reference
//! keys lifted to validated newtypes ([`SkillName`], [`ReferenceName`]).

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::Serialize;

use crate::error::SkillLoadError;

/// Reject empty names and names carrying a path component — a parent-dir `..`,
/// a separator (`/` or `\`), or a NUL. A bare `.` and dotted stems (`api.v2`)
/// are allowed so `ReferenceName`s derived from a `.md` stem round-trip.
fn validate_name(value: &str) -> bool {
    !value.is_empty() && value != ".." && !value.contains(['/', '\\', '\0'])
}

/// A skill name: the parsed name (frontmatter `name`, else the directory name)
/// and the registry key, a 1:1 port of Python `registry.py`'s `skill.name` key.
///
/// The traversal-safety guarantee is that names are used **only as map keys**
/// (never path-joined); this newtype's separator/`..`/NUL rejection is
/// defense-in-depth on top of that (`api-parse-dont-validate`).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct SkillName(String);

impl SkillName {
    /// Parse a skill name, rejecting empty or path-bearing values.
    ///
    /// # Errors
    /// Returns [`SkillLoadError::InvalidName`] when the value is empty or
    /// contains a path separator, a `..` component, or a NUL.
    pub fn parse(value: impl Into<String>) -> Result<Self, SkillLoadError> {
        let value = value.into();
        if validate_name(&value) {
            Ok(Self(value))
        } else {
            Err(SkillLoadError::InvalidName(value))
        }
    }

    /// Borrow the name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A reference name: a skill's `references/*.md` file **stem** and its map key.
///
/// Same shape and validation as [`SkillName`]; accepts dotted stems like
/// `api.v2` (matching Python `ref_file.stem`).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct ReferenceName(String);

impl ReferenceName {
    /// Parse a reference name, rejecting empty or path-bearing values.
    ///
    /// # Errors
    /// Returns [`SkillLoadError::InvalidName`] when the value is empty or
    /// contains a path separator, a `..` component, or a NUL.
    pub fn parse(value: impl Into<String>) -> Result<Self, SkillLoadError> {
        let value = value.into();
        if validate_name(&value) {
            Ok(Self(value))
        } else {
            Err(SkillLoadError::InvalidName(value))
        }
    }

    /// Borrow the name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Where a skill was loaded from. Replaces the Python free `source: str`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum SkillSource {
    /// Loaded from the configured skill root (the only producer today).
    Bundled,
}

/// A loaded skill — the immutable runtime content exposed to agents.
///
/// Derives `Serialize` solely to pin the wire shape in a snapshot test; nothing
/// reconstructs a `SkillDefinition` from JSON (the loader builds it from
/// markdown, and `eos-tools` reads it in-memory via the registry).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[non_exhaustive]
pub struct SkillDefinition {
    /// The parsed skill name and registry key.
    pub name: SkillName,
    /// A one-line description (frontmatter `description`, else a fallback).
    pub description: String,
    /// The full `SKILL.md` text.
    pub content: String,
    /// Where the skill came from.
    pub source: SkillSource,
    /// The on-disk skill directory, if loaded from one.
    pub path: Option<PathBuf>,
    /// Eagerly-loaded `references/*.md`, keyed by file stem and ordered by key.
    pub references: BTreeMap<ReferenceName, String>,
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;

    // AC-skills-06: name validation blocks traversal; dotted stems are accepted.
    #[test]
    fn rejects_path_separators_accepts_dotted_stems() {
        // Rejected: separators, parent-dir, empty.
        assert!(SkillName::parse("../x").is_err());
        assert!(SkillName::parse("a/b").is_err());
        assert!(SkillName::parse("a\\b").is_err());
        assert!(SkillName::parse("..").is_err());
        assert!(SkillName::parse("").is_err());
        assert!(ReferenceName::parse("a/b").is_err());

        // Accepted: plain names, names with spaces, dotted stems, a bare dot.
        assert!(SkillName::parse("planner").is_ok());
        assert!(SkillName::parse("Beta Heading").is_ok());
        assert!(ReferenceName::parse("api.v2").is_ok());
        assert!(ReferenceName::parse(".").is_ok());
    }

    // AC-skills-08: the serde serialize shape is pinned by a committed snapshot.
    #[test]
    fn skill_definition_serialize_snapshot() {
        let mut references = BTreeMap::new();
        // Insert out of order to prove the BTreeMap key-sorts on serialize.
        references.insert(ReferenceName::parse("rubric").unwrap(), "RUBRIC".to_owned());
        references.insert(
            ReferenceName::parse("checklist").unwrap(),
            "CHECKLIST".to_owned(),
        );
        let definition = SkillDefinition {
            name: SkillName::parse("planner").unwrap(),
            description: "Planner skill".to_owned(),
            content: "# Planner\nbody".to_owned(),
            source: SkillSource::Bundled,
            path: Some(PathBuf::from("/skills/planner")),
            references,
        };
        insta::assert_json_snapshot!("skill_definition", definition);
    }
}
