//! The static identity of an agent profile: the `AgentType` / `AgentRole`
//! vocabularies, the `AgentName` newtype, and the `AgentDefinition` value type
//! with its construction-time invariants.
//!
//! Source of truth: `backend/src/agents/definition/model.py`. Pydantic
//! validators become parse-don't-validate construction (`api-parse-dont-validate`):
//! the serde DTO [`RawAgentDefinition`] funnels through
//! [`AgentDefinition::from_frontmatter`], so an invalid definition is
//! unrepresentable.

use std::fmt;
use std::num::NonZeroU32;
use std::path::{Path, PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::error::AgentDefError;

/// Runtime class of an agent profile (`model.py:AgentType`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AgentType {
    /// A regular agent.
    Agent,
    /// A worker subagent targetable by `run_subagent`.
    Subagent,
}

impl Default for AgentType {
    /// Matches `model.py`'s `agent_type: AgentType = AGENT`.
    fn default() -> Self {
        Self::Agent
    }
}

/// Canonical category of an agent profile (`model.py:AgentRole`).
///
/// Closed vocabulary (deliberately **not** `#[non_exhaustive]`): the
/// planner-submission gate and audit tag rely on an exhaustive `match`. The
/// `executor` profile carries `role: generator`; `executor` never enters this
/// state (anchor §4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AgentRole {
    /// The root request agent.
    Root,
    /// Authors the attempt DAG.
    Planner,
    /// Does the work; the `executor` profile maps here.
    Generator,
    /// Digests dependency outputs; the attempt exit gate.
    Reducer,
    /// The advisor helper.
    Helper,
    /// The read-only explorer subagent.
    Subagent,
}

impl AgentRole {
    /// The canonical `snake_case` token (matches the serde value).
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Root => "root",
            Self::Planner => "planner",
            Self::Generator => "generator",
            Self::Reducer => "reducer",
            Self::Helper => "helper",
            Self::Subagent => "subagent",
        }
    }
}

impl fmt::Display for AgentRole {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A registry key / dispatchable name, validated non-empty after trimming.
///
/// Format-only newtype (`type-newtype-ids`): it does **not** check membership
/// against any catalog. Empty-rejection is a Rust hardening of the Python model
/// (which has no name validator); the loader applies the `path.stem` default
/// before construction so the newtype only ever sees a resolved stem on the
/// file-parse path.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(transparent)]
#[schemars(transparent)]
pub struct AgentName(String);

impl AgentName {
    /// Construct a name, trimming surrounding whitespace.
    ///
    /// # Errors
    /// Returns [`AgentDefError::EmptyName`] when the trimmed value is empty.
    pub fn new(raw: impl Into<String>) -> Result<Self, AgentDefError> {
        let trimmed = raw.into().trim().to_owned();
        if trimmed.is_empty() {
            return Err(AgentDefError::EmptyName);
        }
        Ok(Self(trimmed))
    }

    /// Borrow the name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AgentName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// The serde DTO for the YAML frontmatter block (`extra="forbid"` →
/// `#[serde(deny_unknown_fields)]`, GC-eos-agent-def-02).
///
/// The loader post-processes this (name/description defaults, contract prepend,
/// skill resolution) and then funnels it through
/// [`AgentDefinition::from_frontmatter`]. `role` is `Option` so a missing value
/// becomes a path-bearing [`AgentDefError::MissingRole`] rather than an opaque
/// serde "missing field" error.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RawAgentDefinition {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub system_prompt: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub tool_call_limit: u32,
    #[serde(default)]
    pub role: Option<AgentRole>,
    #[serde(default)]
    pub agent_type: AgentType,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default)]
    pub terminals: Vec<String>,
    #[serde(default)]
    pub notification_triggers: Vec<String>,
    #[serde(default)]
    pub skill: Option<PathBuf>,
    #[serde(default)]
    pub context_recipe: Option<String>,
}

/// Full agent definition with all configuration fields (`model.py`).
///
/// Construction enforces invariants so an invalid value is unrepresentable:
/// `tool_call_limit` is [`NonZeroU32`], `terminals` is non-empty after stripping
/// blanks, and blank `notification_triggers` are dropped. No `Default` impl:
/// `name`/`description`/`tool_call_limit`/`terminals` have no sensible default
/// (`api-default-impl`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AgentDefinition {
    /// Registry key and dispatchable name.
    pub name: AgentName,
    /// Human-readable description.
    pub description: String,
    /// Composed system prompt; `None` when the profile declares none.
    #[serde(default)]
    pub system_prompt: Option<String>,
    /// Raw model id; the `"inherit"` sentinel is resolved downstream, kept verbatim here.
    #[serde(default)]
    pub model: Option<String>,
    /// Per-run cap on tool dispatches (positive by construction).
    pub tool_call_limit: NonZeroU32,
    /// Canonical role; required on the file-parse path (GC-eos-agent-def-06).
    pub role: AgentRole,
    /// Regular agent or worker subagent.
    #[serde(default)]
    pub agent_type: AgentType,
    /// Tools the agent may call (plain names; resolved to specs in `eos-engine`).
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    /// Terminal tools that end the query loop (non-empty by construction).
    pub terminals: Vec<String>,
    /// Declarative notification-trigger ids (blanks stripped).
    #[serde(default)]
    pub notification_triggers: Vec<String>,
    /// Absolute path to the agent's workflow skill, resolved by the loader.
    #[serde(default)]
    pub skill: Option<PathBuf>,
    /// Context-engine recipe id resolved at compose time.
    #[serde(default)]
    pub context_recipe: Option<String>,
}

impl AgentDefinition {
    /// Validate a post-processed frontmatter DTO into a definition.
    ///
    /// The loader supplies `path` for path-bearing errors and has already
    /// applied the name/description defaults and resolved the skill path.
    ///
    /// # Errors
    /// Returns [`AgentDefError`] when `role` is absent ([`AgentDefError::MissingRole`]),
    /// the resolved name is empty ([`AgentDefError::EmptyName`]), `terminals` is
    /// empty after stripping blanks ([`AgentDefError::EmptyTerminals`]), or
    /// `tool_call_limit` is not positive ([`AgentDefError::NonPositiveToolCallLimit`]).
    pub(crate) fn from_frontmatter(
        raw: RawAgentDefinition,
        path: &Path,
    ) -> Result<Self, AgentDefError> {
        let role = raw.role.ok_or_else(|| AgentDefError::MissingRole {
            path: path.to_owned(),
        })?;
        let name = AgentName::new(raw.name.unwrap_or_default())?;
        let tool_call_limit =
            NonZeroU32::new(raw.tool_call_limit).ok_or(AgentDefError::NonPositiveToolCallLimit)?;
        let terminals: Vec<String> = raw
            .terminals
            .into_iter()
            .filter(|t| !t.trim().is_empty())
            .collect();
        if terminals.is_empty() {
            return Err(AgentDefError::EmptyTerminals);
        }
        let notification_triggers = raw
            .notification_triggers
            .into_iter()
            .filter(|t| !t.trim().is_empty())
            .collect();
        Ok(Self {
            name,
            description: raw.description.unwrap_or_default(),
            system_prompt: raw.system_prompt,
            model: raw.model,
            tool_call_limit,
            role,
            agent_type: raw.agent_type,
            allowed_tools: raw.allowed_tools,
            terminals,
            notification_triggers,
            skill: raw.skill,
            context_recipe: raw.context_recipe,
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;

    fn valid_raw() -> RawAgentDefinition {
        RawAgentDefinition {
            name: Some("worker".to_owned()),
            description: Some("a worker".to_owned()),
            tool_call_limit: 10,
            role: Some(AgentRole::Generator),
            terminals: vec!["submit_generator_outcome".to_owned()],
            ..RawAgentDefinition::default()
        }
    }

    // AC-eos-agent-def-02: an unknown frontmatter key is rejected at deserialize.
    #[test]
    fn definition_rejects_unknown_field() {
        let yaml = "name: x\ndescription: y\ntool_call_limit: 1\nrole: generator\nterminals: [t]\nbogus_key: 1\n";
        let parsed = serde_yaml::from_str::<RawAgentDefinition>(yaml);
        assert!(parsed.is_err(), "deny_unknown_fields must reject bogus_key");
    }

    // AC-eos-agent-def-03: empty/blank terminals and non-positive limit fail.
    #[test]
    fn definition_enforces_terminals_and_limit() {
        let path = Path::new("test.md");

        let mut empty = valid_raw();
        empty.terminals = vec![];
        assert!(matches!(
            AgentDefinition::from_frontmatter(empty, path),
            Err(AgentDefError::EmptyTerminals)
        ));

        let mut blank = valid_raw();
        blank.terminals = vec!["   ".to_owned(), "".to_owned()];
        assert!(matches!(
            AgentDefinition::from_frontmatter(blank, path),
            Err(AgentDefError::EmptyTerminals)
        ));

        let mut zero = valid_raw();
        zero.tool_call_limit = 0;
        assert!(matches!(
            AgentDefinition::from_frontmatter(zero, path),
            Err(AgentDefError::NonPositiveToolCallLimit)
        ));
    }

    #[test]
    fn from_frontmatter_strips_blank_triggers_and_terminals() {
        let mut raw = valid_raw();
        raw.terminals = vec!["  ".to_owned(), "submit_x".to_owned()];
        raw.notification_triggers = vec!["keep".to_owned(), "  ".to_owned()];
        let def = AgentDefinition::from_frontmatter(raw, Path::new("t.md")).unwrap();
        assert_eq!(def.terminals, vec!["submit_x".to_owned()]);
        assert_eq!(def.notification_triggers, vec!["keep".to_owned()]);
    }

    // AC-eos-agent-def-06 (enum-value half): serde values match the Python schema.
    #[test]
    fn role_and_type_serde_values() {
        let role = serde_json::to_value(AgentRole::Generator).unwrap();
        assert_eq!(role, serde_json::json!("generator"));
        let ty = serde_json::to_value(AgentType::Subagent).unwrap();
        assert_eq!(ty, serde_json::json!("subagent"));
        // round-trip every variant token.
        for (variant, token) in [
            (AgentRole::Root, "root"),
            (AgentRole::Planner, "planner"),
            (AgentRole::Generator, "generator"),
            (AgentRole::Reducer, "reducer"),
            (AgentRole::Helper, "helper"),
            (AgentRole::Subagent, "subagent"),
        ] {
            assert_eq!(
                serde_json::to_value(variant).unwrap(),
                serde_json::json!(token)
            );
            assert_eq!(variant.as_str(), token);
        }
    }

    #[test]
    fn agent_name_trims_and_rejects_empty() {
        assert_eq!(AgentName::new("  root  ").unwrap().as_str(), "root");
        assert!(matches!(
            AgentName::new("   "),
            Err(AgentDefError::EmptyName)
        ));
    }
}

#[cfg(test)]
mod schema_parity {
    //! AC-eos-agent-def-09: the schemars `JsonSchema` for `AgentDefinition`
    //! matches the frozen Pydantic golden on field names and enum values; the
    //! required set diverges by exactly `{role}` (Pydantic's `role = GENERATOR`
    //! default makes it optional there; the Rust file-parse path makes it
    //! required, GC-eos-agent-def-06).
    #![allow(clippy::unwrap_used)]
    use std::collections::BTreeSet;
    use std::path::Path;

    use super::*;
    use pretty_assertions::assert_eq;

    fn python_golden() -> serde_json::Value {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity/schemas/agent_definition.schema.json");
        let raw = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read pydantic golden {}: {e}", path.display()));
        serde_json::from_str(&raw).expect("golden is valid json")
    }

    fn keys(value: &serde_json::Value, pointer: &str) -> BTreeSet<String> {
        value
            .pointer(pointer)
            .and_then(serde_json::Value::as_object)
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default()
    }

    fn string_set(value: &serde_json::Value) -> BTreeSet<String> {
        value
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Union every string in any nested `enum` array. Robust to schemars
    /// emitting per-variant `oneOf`/`enum` subschemas (variant doc comments) vs
    /// Pydantic's single flat `enum`.
    fn enum_values(value: &serde_json::Value) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        collect_enums(value, &mut out);
        out
    }

    fn collect_enums(value: &serde_json::Value, out: &mut BTreeSet<String>) {
        match value {
            serde_json::Value::Object(map) => {
                if let Some(values) = map.get("enum") {
                    out.extend(string_set(values));
                }
                for nested in map.values() {
                    collect_enums(nested, out);
                }
            }
            serde_json::Value::Array(items) => {
                for nested in items {
                    collect_enums(nested, out);
                }
            }
            _ => {}
        }
    }

    #[test]
    fn agent_definition_schema_snapshot() {
        let python = python_golden();
        let rust = serde_json::to_value(schemars::schema_for!(AgentDefinition)).unwrap();

        // 1. Field names must match exactly.
        let py_props = keys(&python, "/properties");
        let rs_props = keys(&rust, "/properties");
        assert_eq!(py_props, rs_props, "field names diverge");

        // 2. Required set diverges by exactly {role}.
        let py_required = string_set(&python["required"]);
        let rs_required = string_set(&rust["required"]);
        let only_in_rust: BTreeSet<_> = rs_required.difference(&py_required).cloned().collect();
        let only_in_python: BTreeSet<_> = py_required.difference(&rs_required).cloned().collect();
        assert_eq!(
            only_in_rust,
            BTreeSet::from(["role".to_owned()]),
            "rust required set must add exactly {{role}}"
        );
        assert!(
            only_in_python.is_empty(),
            "rust required must be a superset of python required, missing {only_in_python:?}"
        );

        // 3. Enum values for AgentRole / AgentType must match. The Rust schema
        //    nests them under `definitions`; generate them directly to avoid
        //    coupling to the `$defs` vs `definitions` ref-naming difference.
        let role_values =
            enum_values(&serde_json::to_value(schemars::schema_for!(AgentRole)).unwrap());
        let type_values =
            enum_values(&serde_json::to_value(schemars::schema_for!(AgentType)).unwrap());
        assert_eq!(
            role_values,
            enum_values(&python["$defs"]["AgentRole"]),
            "AgentRole enum values diverge"
        );
        assert_eq!(
            type_values,
            enum_values(&python["$defs"]["AgentType"]),
            "AgentType enum values diverge"
        );
    }
}
